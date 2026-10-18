"""新闻检测与任务提交(TASK_SUBMIT_URL 未配置时只检测不提交)。"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from signals.common.schemas import SignalSubmission, SubmissionResult, expiry_timestamp
from signals.common.task_client import TaskClient
from .signal_detector import DEFAULT_CONFIG, NewsSignalDiscovery, directory_lock, write_json
from .signal_explainer import signal_deadline, completed_investigation
from .submission import news_to_submission


def company_key(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('必须显式提供项目中的 company_id')
    return value.strip()


class NewsDetectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_id: str = Field(min_length=1)
    items: list

    _company = field_validator('company_id', mode='before')(company_key)


class NewsDetectionResponse(BaseModel):
    company_id: str
    summary: dict
    signals: list[SignalSubmission]
    submissions: list[SubmissionResult]
    pending_submissions: int


class NewsService:
    def __init__(self, config_path=None, data_dir=None, task_client=None, *, publication_reader=None,
                 completed_signals=()):
        self.config_path = Path(config_path or os.getenv('SIGNAL_NEWS_CONFIG') or DEFAULT_CONFIG)
        self.data_dir = Path(data_dir or os.getenv('SIGNAL_NEWS_DATA') or 'outputs/news')
        self.task_client = task_client if task_client is not None else TaskClient()
        self.publication_reader = publication_reader
        self.completed_signals = completed_signals

    def _discovery(self, company_id):
        # 项目企业 ID 与新闻 enterpriseId 分别保留，状态按前者隔离。
        path = self.data_dir / ('company_' + sha256(company_id.encode()).hexdigest())
        return NewsSignalDiscovery(self.config_path, path, publication_reader=self.publication_reader,
                                   completed_signals=self.completed_signals)

    @staticmethod
    def _now(now):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('当前时间必须带时区')
        return now.astimezone(timezone.utc)

    def detect(self, items, company_id, *, now=None, fetcher=None, collection_mode='crawler_input'):
        company_id, now = company_key(company_id), self._now(now)
        discovery = self._discovery(company_id)
        with directory_lock(discovery.data_dir, 'news_service.lock'):
            summary = discovery.run(items, now=now, fetcher=fetcher, collection_mode=collection_mode)
            signals, submissions = self._submit_pending(discovery, company_id, now)
            response = NewsDetectionResponse(company_id=company_id, summary=summary, signals=signals,
                submissions=submissions, pending_submissions=sum(
                    a.get('status') == 'pending' and a.get('triggerEligible')
                    for a in discovery.tool.store.load_alerts()))
            write_json(Path(summary['outputDirectory']) / 'submission.json', response.model_dump(mode='json'))
            return response

    def retry_pending(self, company_id, *, now=None):
        """只重交未成功的原载荷；不重新发现信号，不更改识别时间或 ID。"""
        company_id, now = company_key(company_id), self._now(now)
        discovery = self._discovery(company_id)
        with directory_lock(discovery.data_dir, 'news_service.lock'):
            return self._submit_pending(discovery, company_id, now)[1]

    def _submit_pending(self, discovery, company_id, now):
        store = discovery.tool.store
        state = store.load_state()
        if state.get('newsEnterpriseId') not in (None, discovery.profile.enterprise_id):
            raise ValueError('更换目标企业请使用新的监测目录')
        discovery._import_completed(state)
        store.save_state(state)
        cache = state.setdefault('newsSubmissionCache', {})
        stamp = expiry_timestamp(now)
        signals, results = [], []
        for alert in store.load_alerts():
            if alert.get('signalSchema') != 'news_signal.v1' or alert.get('status') != 'pending' or not alert.get('triggerEligible'):
                continue
            signal_id = alert['alertId']
            previous = completed_investigation(signal_id, alert.get('evidence', []),
                                               state.get('newsCompletedHistory', {}))
            if previous:
                store.update_alert_status(signal_id, 'completed', '此前已完成调查，不重复提交')
                continue
            record = cache.get(signal_id)
            if record and record.get('retryAfter', '') > stamp:
                continue
            if record is None:
                try:
                    news = discovery.signal_payload(alert)
                    signal = news_to_submission(news, company_id)
                    deadline = signal_deadline(news, discovery.tool.config.settings.lookback_hours,
                                               discovery.profile.calendar_timezone)
                    record = {'signal': signal.model_dump(mode='json'), 'expires_at': expiry_timestamp(deadline)}
                except (ValueError, TypeError, OverflowError):
                    store.update_alert_status(signal_id, 'observed', '调查载荷校验失败，保留观察')
                    results.append(SubmissionResult(signal_id=signal_id, status='not_submitted', detail='线索或原文时间无法验证'))
                    continue
                cache[signal_id] = record
                # 先保存固定载荷，再请求提交；网络中断后的重试沿用相同 ID 和字段。
                store.save_state(state)
            if record['expires_at'] <= stamp:
                store.update_alert_status(signal_id, 'expired', '报道超过新闻时间窗，停止提交')
                results.append(SubmissionResult(signal_id=signal_id, status='not_submitted', detail='新闻信号已过期'))
                continue
            if len(signals) >= 100:
                break  # 每轮提交请求上限；调查每日额度由公共触发器控制。
            signal = SignalSubmission.model_validate(record['signal'])
            signals.append(signal)
            try:
                result = self.task_client.submit(signal, expires_at=record['expires_at'], now=now)
                result = SubmissionResult.model_validate(result)
                if result.signal_id != signal.signal_id:
                    raise ValueError('提交回执标识不一致')
            except Exception as exc:
                result = SubmissionResult(signal_id=signal_id, status='failed',
                                          detail='公共入口提交失败：' + type(exc).__name__)
            results.append(result)
            record['lastAttemptAt'] = stamp
            record['retryAfter'] = expiry_timestamp(now + timedelta(seconds=60))
            record['lastResult'] = result.model_dump(mode='json')
            store.save_state(state)
            with (discovery.data_dir / 'submissions.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'attempted_at': stamp, 'expires_at': record['expires_at'],
                    'signal': record['signal'], 'result': record['lastResult']}, ensure_ascii=False, allow_nan=False) + '\n')
            if result.status == 'submitted':
                store.update_alert_status(signal_id, 'submitted', '已登记公共任务；调查状态以公共任务记录为准')
        return signals, results
