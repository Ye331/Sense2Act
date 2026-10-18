"""真实留存新闻节选验证检测与提交；AcceptingClient 代替外部任务入口记录提交。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from application import create_app
from signals.common.schemas import SubmissionResult, validate_signal
from signals.common.task_client import TaskClient
from signals.news.service import NewsService
from signals.news.signal_detector import DEFAULT_CONFIG, NewsSignalDiscovery
from signals.news.submission import news_to_submission

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def news():
    return json.loads((Path(__file__).parent / 'fixtures/news_sources.json').read_text(encoding='utf-8'))


class AcceptingClient:
    """记录提交;同一 signal_id 幂等,对应任务入口的 Idempotency-Key 语义。"""
    def __init__(self):
        self.tasks = {}

    def submit(self, signal, *, expires_at=None, now=None):
        self.tasks.setdefault(signal.signal_id, (signal.model_dump(mode='json'), expires_at))
        return SubmissionResult(signal_id=signal.signal_id, status='submitted')


def local_service(tmp_path):
    client = AcceptingClient()
    return NewsService(data_dir=tmp_path / 'news', task_client=client), client


def test_real_news_to_common_queue_only_and_originals_unchanged(news, tmp_path):
    service, client = local_service(tmp_path)
    before = deepcopy(news)
    result = service.detect(news, 42, now=NOW)
    assert result.company_id == '42' and result.summary['keptItems'] == 2
    assert len(result.signals) == 2 and all(r.status == 'submitted' for r in result.submissions)
    assert len(client.tasks) == 2 and result.pending_submissions == 0
    assert all(expires for _, expires in client.tasks.values())
    output = Path(result.summary['outputDirectory'])
    assert json.loads((output / 'input.json').read_text()) == before == news
    signals = json.loads((output / 'signals.json').read_text())
    assert all(s['schemaVersion'] == 'news_signal.v1' and s['investigationStatus'] == 'pending' for s in signals)
    for signal in result.signals:
        assert set(signal.model_dump()) == {'signal_id', 'company_id', 'kind', 'payload'}
        assert signal.kind == 'rumor' and signal.company_id != signals[0]['enterpriseId']
        assert set(signal.payload) == {'subject_name', 'subject_credit_code', 'summary', 'source',
                                      'signal_time', 'watch_focus', 'clues'}
        assert signal.payload['signal_time'] == NOW.isoformat()
        assert signal.payload['summary'].startswith('【摘要')
        assert len(signal.payload['clues']) == 1
        clue = signal.payload['clues'][0]
        original = next(e for e in news if e['evidenceId'] == clue['clue_id'])
        assert clue['published_at'] == original['publication']['value']
        assert clue['author_type'] == '未知' and clue['source'] == 'IT之家'
        assert clue['content'].startswith('【摘要') and original['content'] in clue['content']


def test_repeated_detection_and_restart_do_not_duplicate(news, tmp_path):
    service, client = local_service(tmp_path)
    first = service.detect(news, '42', now=NOW)
    output = Path(first.summary['outputDirectory'])
    originals = {p.name: p.read_bytes() for p in output.iterdir()}
    restarted = NewsService(data_dir=tmp_path / 'news', task_client=client)
    second = restarted.detect(news, '42', now=NOW + timedelta(minutes=2))
    assert second.summary['signalsCreated'] == 0 and not second.submissions
    assert {p.name: p.read_bytes() for p in output.iterdir()} == originals


def test_company_ids_are_explicit_and_state_isolated(news, tmp_path):
    service, _ = local_service(tmp_path)
    for invalid in (None, '', True, {}):
        with pytest.raises(ValueError):
            service.detect(news, invalid, now=NOW)
    a, b = [service.detect(news, company, now=NOW) for company in ('42', '43')]
    assert a.summary['dataDirectory'] != b.summary['dataDirectory']
    assert [s.signal_id for s in a.signals] == [s.signal_id for s in b.signals]
    assert {s.company_id for s in b.signals} == {'43'}


@pytest.mark.parametrize('change', ['no_proof', 'wrong_url', 'naive_time'])
def test_missing_or_unverifiable_source_stays_observed(news, tmp_path, change):
    for row in news:
        if change == 'no_proof':
            row.pop('publication')
        elif change == 'wrong_url':
            row['url'] = ''
        else:
            row['publication']['value'] = row['publication']['value'][:-6]
    service, _ = local_service(tmp_path)
    result = service.detect(news, '42', now=NOW)
    assert result.summary['screeningCounts']['observed'] == 2
    assert not result.signals and not result.submissions
    assert next((tmp_path / 'news').rglob('observation.jsonl')).is_file()


def test_original_time_overrides_search_timestamp(news, tmp_path):
    for row in news:
        row['publishedAt'] = '2030-01-01T00:00:00Z'
    service, _ = local_service(tmp_path)
    result = service.detect(news, '42', now=NOW)
    assert len(result.signals) == 2
    assert all(s.payload['clues'][0]['published_at'].startswith('2026-10-12T17:') for s in result.signals)


def test_date_only_uses_business_calendar_without_inventing_time(news, tmp_path):
    for row in news:
        row['publishedAt'] = None
        row['publication'].update(value='2026-10-13', raw='2026-10-13', precision='day')
    service, _ = local_service(tmp_path)
    # UTC 仍为 12 日，上海已经是 13 日；不能把原文日期误判为未来。
    now = datetime(2026, 10, 12, 16, 30, tzinfo=timezone.utc)
    result = service.detect(news, '42', now=now)
    assert len(result.signals) == 2
    assert all(s.payload['clues'][0]['published_at'] == '2026-10-13' for s in result.signals)
    internal = json.loads((Path(result.summary['outputDirectory']) / 'signals.json').read_text())
    assert all(s['publishedAt'] is None and s['evidence'][0]['publishedAt'] is None for s in internal)


def test_stale_news_and_duplicate_url_are_not_new_signals(news, tmp_path):
    discovery = NewsSignalDiscovery(data_dir=tmp_path / 'discovery')
    copy = deepcopy(news[0])
    copy['evidenceId'] += '-duplicate'
    copy['source'] = 'toutiao'
    summary = discovery.run([*news, copy, None, {'title': ''}], now=NOW)
    assert summary['screeningCounts'] == {'kept': 2, 'dropped': 2, 'observed': 0, 'duplicate': 1}
    assert not summary['agentTrigger']['dispatched']
    old = discovery.run(news, now=NOW + timedelta(days=40))
    assert old['screeningCounts']['dropped'] == 2


def test_legacy_runner_mode_is_disabled(tmp_path):
    payload = json.loads(DEFAULT_CONFIG.read_text())
    payload['monitor']['settings']['agentAutoTrigger'] = True
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='agentAutoTrigger=false'):
        NewsSignalDiscovery(config, tmp_path / 'news')


def test_submission_retry_reuses_original_envelope_and_expiry(news, tmp_path):
    calls = []
    def handler(request):
        calls.append((request.content, request.headers['Idempotency-Key'], request.headers['X-Signal-Expires-At']))
        return httpx.Response(503 if len(calls) <= 2 else 202, json={'task_id': 'accepted'})
    client = TaskClient(url='http://backend/tasks', expiry_supported=True, transport=httpx.MockTransport(handler))
    service = NewsService(data_dir=tmp_path / 'news', task_client=client)
    result = service.detect(news, '42', now=NOW)
    assert all(r.status == 'failed' for r in result.submissions)
    assert not service.retry_pending('42', now=NOW + timedelta(seconds=30))
    restarted = NewsService(data_dir=tmp_path / 'news', task_client=client)
    assert all(r.status == 'submitted' for r in restarted.retry_pending('42', now=NOW + timedelta(seconds=61)))
    assert calls[:2] == calls[2:]
    assert not restarted.retry_pending('42', now=NOW + timedelta(minutes=3))


def test_submitted_but_receipt_lost_deduplicates_after_restart(news, tmp_path):
    service, client = local_service(tmp_path)
    class LostReceipt:
        def submit(self, signal, **kwargs):
            client.submit(signal, **kwargs)
            raise httpx.ReadTimeout('private-response')
    service.task_client = LostReceipt()
    result = service.detect(news, '42', now=NOW)
    assert all(r.status == 'failed' for r in result.submissions)
    service.task_client = client
    receipts = service.retry_pending('42', now=NOW + timedelta(seconds=61))
    assert len(receipts) == 2 and all(r.status == 'submitted' for r in receipts)
    assert 'private-response' not in result.model_dump_json()
    assert len(client.tasks) == 2


def test_no_backend_expiry_contract_means_no_http_request(news, tmp_path):
    def forbidden(request):
        raise AssertionError('未经确认不得把有有效期的新闻交给后端')
    client = TaskClient(url='http://backend/tasks', expiry_supported=False, transport=httpx.MockTransport(forbidden))
    result = NewsService(data_dir=tmp_path / 'news', task_client=client).detect(news, '42', now=NOW)
    assert result.pending_submissions == 2
    assert all(r.status == 'not_submitted' for r in result.submissions)


def test_payload_marks_summary_when_full_flag_has_no_body(news, tmp_path):
    service, _ = local_service(tmp_path)
    result = service.detect(news, '42', now=NOW)
    internal = json.loads((Path(result.summary['outputDirectory']) / 'signals.json').read_text())[0]
    internal['evidence'][0].update(hasFullContent=True, content=None, snippet='')
    signal = news_to_submission(internal, '42')
    assert signal.payload['clues'][0]['content'].startswith('【摘要')
    assert signal.payload['summary'].startswith('【摘要')
    internal['evidence'][0]['authorType'] = '官方'
    assert news_to_submission(internal, '42').payload['clues'][0]['author_type'] == '官方'
    internal['evidence'][0]['url'] = None
    with pytest.raises(ValueError):
        news_to_submission(internal, '42')


def test_all_clues_are_preserved_and_missing_time_not_estimated(news, tmp_path):
    service, _ = local_service(tmp_path)
    result = service.detect(news, '42', now=NOW)
    internal = json.loads((Path(result.summary['outputDirectory']) / 'signals.json').read_text())[0]
    internal['evidence'] = news
    internal['evidence'][0]['publication'] = {'status': 'unavailable'}
    signal = news_to_submission(internal, '42')
    assert len(signal.payload['clues']) == 2
    assert signal.payload['clues'][0]['published_at'] == ''
    assert signal.payload['watch_focus']
    assert 'score' not in signal.payload
    assert validate_signal(signal) == signal


def test_api_news_detection_has_same_output_and_header_expiry(service, news, tmp_path, monkeypatch):
    detector, _ = local_service(tmp_path)
    monkeypatch.setattr(detector, '_now', lambda now: NOW)
    with TestClient(create_app(service, news_service=detector)) as client:
        response = client.post('/api/v1/signals/news/detect', json={'company_id': 42, 'items': news})
        assert response.status_code == 200, response.text
        assert len(response.json()['signals']) == 2
        assert client.post('/api/v1/signals/news/detect', json={'items': news}).status_code == 422


def test_business_relation_crosses_comma_and_title_to_lead(news, tmp_path):
    # 拆开真实留存标题中的主体和业务，不伪造真实采集输入。
    news[0]['title'] = '华为，小艺 AI 快捷栏功能上线时间公布'
    news[0]['snippet'] = '鸿蒙 HarmonyOS 7，预计 10 月底分批推送'
    news[0]['content'] = news[0]['snippet']
    summary = NewsSignalDiscovery(data_dir=tmp_path / 'news').run(news, now=NOW)
    assert summary['keptItems'] == 2 and summary['signalsCreated'] >= 2
    signals = json.loads((Path(summary['outputDirectory']) / 'signals.json').read_text())
    assert any(s['subjectName'] == '华为' and s['triggerEligible'] for s in signals)


def test_unrelated_remote_paragraph_does_not_borrow_phone_business(news, tmp_path):
    news[0]['title'] = '华为发布其他业务介绍'
    news[0]['snippet'] = news[0]['content'] = '其他业务介绍。' * 160 + '\n手机行业资料。'
    summary = NewsSignalDiscovery(data_dir=tmp_path / 'news').run([news[0]], now=NOW)
    assert not summary['keptItems'] and not summary['signalsCreated']


def test_completed_real_source_is_reused_even_if_enriched_body_changes(news, tmp_path):
    first = NewsSignalDiscovery(data_dir=tmp_path / 'first').run(news, now=NOW)
    old_path = Path(first['outputDirectory']) / 'signals.json'
    completed = json.loads(old_path.read_text())
    for signal in completed:
        signal['investigationStatus'] = 'completed'
        signal['investigationDirectory'] = 'old-investigation/' + signal['signalId']
    history = tmp_path / 'completed.json'
    history.write_text(json.dumps(completed))
    original = old_path.read_bytes(), history.read_bytes()
    class ForbiddenSubmission:
        def submit(self, *args, **kwargs):
            raise AssertionError('已经调查过的原文不能重复提交')
    news[0]['content'] += '\n' + news[0]['title']
    service = NewsService(data_dir=tmp_path / 'second', task_client=ForbiddenSubmission(), completed_signals=[history])
    result = service.detect(news, '42', now=NOW)
    assert not result.signals and not result.submissions and result.pending_submissions == 0
    assert result.summary['completedHistoryLoaded'] == result.summary['completedHistoryReused'] == 2
    output = json.loads((Path(result.summary['outputDirectory']) / 'signals.json').read_text())
    assert all(s['investigationStatus'] == 'completed' and s['investigationDirectory'] for s in output)
    assert {s['previousSignalId'] for s in output} == {s['signalId'] for s in completed}
    assert (old_path.read_bytes(), history.read_bytes()) == original
    restarted = NewsService(data_dir=tmp_path / 'second', task_client=ForbiddenSubmission())
    assert not restarted.retry_pending('42', now=NOW + timedelta(minutes=2))


def test_importing_completed_history_blocks_already_pending_submission(news, tmp_path):
    class NoBackend:
        def submit(self, signal, **kwargs):
            return {'signal_id': signal.signal_id, 'status': 'not_submitted'}
    service = NewsService(data_dir=tmp_path / 'news', task_client=NoBackend())
    first = service.detect(news, '42', now=NOW)
    saved = json.loads((Path(first.summary['outputDirectory']) / 'signals.json').read_text())
    for signal in saved:
        signal['investigationStatus'] = 'completed'
    path = tmp_path / 'history.json'
    path.write_text(json.dumps(saved))
    class ForbiddenSubmission:
        def submit(self, *args, **kwargs):
            raise AssertionError('导入历史后不应再提交旧 pending 信号')
    service = NewsService(data_dir=tmp_path / 'news', task_client=ForbiddenSubmission(), completed_signals=[path])
    assert not service.retry_pending('42', now=NOW + timedelta(minutes=2))


def test_primary_phone_scope_and_promotion_filter(news, tmp_path):
    news[0]['title'] = '华为智能家居功能上线'
    news[0]['snippet'] = news[0]['content'] = '可以通过手机应用控制。'
    news[1]['title'] = 'Deals: Honor 手机优惠'
    summary = NewsSignalDiscovery(data_dir=tmp_path / 'scope').run(news, now=NOW)
    assert not summary['keptItems'] and not summary['signalsCreated']
    assert summary['screeningCounts']['observed'] == summary['screeningCounts']['dropped'] == 1


def test_long_phone_title_does_not_lose_object_due_to_english_window(news, tmp_path):
    # 复用真实事件，只把原标题长度展开以检验动作前宾语。
    news[0]['title'] = '华为手机，' + '核心功能详细说明' * 8 + '，上线鸿蒙新功能'
    summary = NewsSignalDiscovery(data_dir=tmp_path / 'long-title').run([news[0]], now=NOW)
    signals = json.loads((Path(summary['outputDirectory']) / 'signals.json').read_text())
    assert signals and any(s['triggerEligible'] for s in signals)


def test_conversion_does_not_treat_legacy_search_time_as_original(news, tmp_path):
    summary = NewsSignalDiscovery(data_dir=tmp_path / 'legacy-time').run(news, now=NOW)
    signal = json.loads((Path(summary['outputDirectory']) / 'signals.json').read_text())[0]
    signal['evidence'][0].pop('publication')
    signal['evidence'][0]['publishedAt'] = '2030-01-01T00:00:00Z'
    converted = news_to_submission(signal, '42')
    assert converted.payload['clues'][0]['published_at'] == ''

