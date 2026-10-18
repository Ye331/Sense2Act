from copy import deepcopy
import json

from fastapi.testclient import TestClient
import httpx
import pandas as pd
import pytest

from application import create_app
from signals.common.schemas import SignalSubmission
from signals.common.task_client import TaskClient
from signals.tender.cli import retry_file
from signals.tender.loader import load_announcements
from signals.tender.normalization import parse_amount
from signals.tender.schemas import DetectionRequest


def test_file_and_api_share_core(service, request_data, tmp_path):
    source = tmp_path / 'announcements.json'
    source.write_text(json.dumps(request_data['history'] + request_data['current']), encoding='utf-8')
    rows = load_announcements(source)
    file_request = deepcopy(request_data)
    file_request['history'] = [r for r in rows if r.publish_date.isoformat() < request_data['detection_date']]
    file_request['current'] = [r for r in rows if r.publish_date.isoformat() == request_data['detection_date']]
    expected = service.detect(DetectionRequest.model_validate(file_request))
    with TestClient(create_app(service)) as client:
        response = client.post('/api/v1/signals/tender/detect', json=request_data)
    assert response.status_code == 200
    actual = response.json()
    assert actual['signals'] == [s.model_dump(mode='json') for s in expected.signals]
    assert actual['records'][0]['amount_score'] == expected.records[0]['amount_score']


def test_api_rejects_future_history(service, request_data):
    request_data['history'][0]['publish_date'] = '2026-10-05'
    with TestClient(create_app(service)) as client:
        response = client.post('/api/v1/signals/tender/detect', json=request_data)
    assert response.status_code == 422


@pytest.mark.parametrize('extension', ['csv', 'xlsx'])
def test_export_aliases_and_amount_units(tmp_path, extension):
    frame = pd.DataFrame([{'项目名称': '医疗AI采购', '采购单位': '医院甲', '发布日期': '2026-10-04',
                           '预算金额（万元）': 12.5, '官网查看地址': 'https://example.com/1'}])
    path = tmp_path / f'announcements.{extension}'
    if extension == 'csv':
        frame.to_csv(path, index=False, encoding='utf-8-sig')
    else:
        frame.to_excel(path, index=False)
    rows = load_announcements(path)
    assert rows[0].amount == 125000
    assert rows[0].id.startswith('R-')


def test_invalid_file_date_is_reported(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('title,buyer,publish_date\n项目,单位,2026-02-30\n', encoding='utf-8')
    with pytest.raises(ValueError, match='第 2 行无效'):
        load_announcements(path)


def test_repeated_export_header_is_not_an_invalid_announcement(tmp_path):
    path = tmp_path / 'header.csv'
    path.write_text('项目名称,采购单位,发布日期\n项目名称,采购单位,发布日期\n真实项目,单位,2026-10-04\n', encoding='utf-8')
    assert len(load_announcements(path)) == 1


def test_ambiguous_amount_stays_unknown():
    assert pd.isna(parse_amount('10-20万元'))
    assert pd.isna(parse_amount('未披露'))
    assert parse_amount('1.2亿元') == 120000000


def test_backend_failure_and_original_payload_retry(service, batch, tmp_path):
    signal = service.detect(batch).signals[0]
    calls = []
    def handler(request):
        calls.append((json.loads(request.content), request.headers['Idempotency-Key']))
        return httpx.Response(503 if len(calls) == 1 else 201, json={'task_id': 'T1'})
    client = TaskClient(url='http://backend/tasks', transport=httpx.MockTransport(handler))
    assert client.submit(signal).status == 'failed'
    path = tmp_path / 'failed.json'
    path.write_text(json.dumps([signal.model_dump(mode='json')]), encoding='utf-8')
    result = retry_file(path, client)[0]
    assert result.status == 'submitted'
    assert calls[0] == calls[1]
    assert result.signal_id == signal.signal_id


def test_backend_business_failure_is_not_success(service, batch):
    client = TaskClient(url='http://backend/tasks', transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'code': 500, 'message': '失败'})))
    assert client.submit(service.detect(batch).signals[0]).status == 'failed'


def test_common_client_can_submit_rumor():
    signal = SignalSubmission(signal_id='S1', company_id='1', kind='rumor',
                              payload={'subject_name': '企业', 'summary': '待调查线索'})
    assert TaskClient(url='').submit(signal).status == 'not_submitted'


def test_submission_timeout_keeps_signal_identity(service, batch):
    signal = service.detect(batch).signals[0]
    def timeout(request):
        raise httpx.ReadTimeout('timeout', request=request)
    client = TaskClient(url='http://backend/tasks', transport=httpx.MockTransport(timeout))
    result = client.submit(signal)
    assert result.status == 'failed'
    assert result.signal_id == signal.signal_id


def test_missing_model_returns_service_unavailable(config, request_data, monkeypatch):
    from signals.tender.service import TenderService
    import core.semantic_encoder as detector
    def unavailable(*args):
        raise FileNotFoundError('missing model')
    monkeypatch.setattr(detector, '_shared_encoder', unavailable)
    with TestClient(create_app(TenderService(config))) as client:
        assert client.post('/api/v1/signals/tender/detect', json=request_data).status_code == 503


def test_empty_json_file_is_valid(tmp_path):
    path = tmp_path / 'empty.json'
    path.write_text('[]', encoding='utf-8')
    assert load_announcements(path) == []


@pytest.mark.parametrize('save_result', [False, True])
def test_cli_only_writes_when_output_is_explicit(service, request_data, tmp_path, monkeypatch, capsys, save_result):
    from signals.tender import cli
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request_data), encoding='utf-8')
    args = ['signal-detect', 'detect', '--request', str(request_path)]
    if save_result:
        args += ['--output', 'result.json']
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', args)
    monkeypatch.setattr(cli, 'TenderService', lambda config: service)
    cli.main()
    output = capsys.readouterr().out
    names = {p.name for p in tmp_path.iterdir()}
    assert names == ({'request.json', 'result.json'} if save_result else {'request.json'})
    data = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8') if save_result else output)
    assert data['signals'][0]['kind'] == 'tender'


def test_retry_detection_result_only_submits_pending_signals(service, batch, tmp_path):
    from signals.common.schemas import SubmissionResult
    response = service.detect(batch)
    first = response.signals[0]
    second = first.model_copy(update={'signal_id': 'another-signal'})
    response.signals.append(second)
    response.submissions[0] = SubmissionResult(signal_id=first.signal_id, status='submitted')
    response.submissions.append(SubmissionResult(signal_id=second.signal_id, status='failed'))
    path = tmp_path / 'result.json'
    path.write_text(response.model_dump_json(), encoding='utf-8')
    calls = []
    def handler(request):
        calls.append(json.loads(request.content)['signal_id'])
        return httpx.Response(201, json={'task_id': 'T2'})
    client = TaskClient(url='http://backend/tasks', transport=httpx.MockTransport(handler))
    results = retry_file(path, client)
    assert calls == [second.signal_id]
    assert results[0].signal_id == second.signal_id
