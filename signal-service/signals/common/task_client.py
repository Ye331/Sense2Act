"""只提交任务；不操作队列，不表示 Agent 已启动。"""
import os
from datetime import datetime, timezone

import httpx

from signals.common.schemas import SignalSubmission, SubmissionResult, expiry_timestamp


class TaskClient:
    def __init__(self, url: str | None = None, token: str | None = None,
                 timeout: float = 15, transport=None, expiry_supported: bool | None = None):
        self.url = url if url is not None else os.getenv('TASK_SUBMIT_URL', '')
        self.token = token if token is not None else os.getenv('TASK_SUBMIT_TOKEN', '')
        self.timeout = timeout
        self.transport = transport
        self.expiry_supported = (os.getenv('TASK_SUBMIT_EXPIRY_SUPPORTED') == '1'
                                 if expiry_supported is None else expiry_supported)

    def submit(self, signal: SignalSubmission, *, expires_at=None, now=None) -> SubmissionResult:
        expires_at = expiry_timestamp(expires_at)
        if expires_at is not None and expires_at <= expiry_timestamp(now or datetime.now(timezone.utc)):
            return SubmissionResult(signal_id=signal.signal_id, status='not_submitted', detail='信号已过期，未请求后端')
        if not self.url:
            return SubmissionResult(signal_id=signal.signal_id, status='not_submitted',
                                    detail='未配置后端任务入口；信号已生成，尚未提交')
        headers = {'Idempotency-Key': f'{signal.company_id}:{signal.signal_id}'}
        if expires_at is not None:
            if not self.expiry_supported:
                return SubmissionResult(signal_id=signal.signal_id, status='not_submitted',
                    detail='后端尚未确认有效期契约；新闻保持待提交，确认支持后设置 TASK_SUBMIT_EXPIRY_SUPPORTED=1')
            headers['X-Signal-Expires-At'] = expires_at
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(self.url, json=signal.model_dump(mode='json'), headers=headers)
                response.raise_for_status()
                body = response.json() if response.content else None
                if isinstance(body, dict) and body.get('status') in {'not_submitted', 'failed'}:
                    return SubmissionResult(signal_id=signal.signal_id, status=body['status'],
                                            detail='任务入口未接受信号', backend_response=body)
                # 兼容项目后端常见的 HTTP 200 + 业务 code 失败。
                if isinstance(body, dict) and (
                    body.get('success') is False or
                    ('code' in body and str(body['code']) not in {'0', '200'})
                ):
                    return SubmissionResult(signal_id=signal.signal_id, status='failed',
                                            detail='后端返回业务失败', backend_response=body)
                return SubmissionResult(signal_id=signal.signal_id, status='submitted', backend_response=body)
        except (httpx.HTTPError, ValueError) as exc:
            detail = '后端响应格式无效' if isinstance(exc, ValueError) else f'任务提交失败：{type(exc).__name__}'
            if isinstance(exc, httpx.HTTPStatusError):
                detail += f'，HTTP {exc.response.status_code}'
            return SubmissionResult(signal_id=signal.signal_id, status='failed', detail=detail)
