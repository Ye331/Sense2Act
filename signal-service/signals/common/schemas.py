"""信号提交、调查载荷校验与提交结果模型。"""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StringConstraints, field_validator, model_validator


class SignalSubmission(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    signal_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    kind: Literal['tender', 'rumor']
    payload: dict


class SubmissionResult(BaseModel):
    signal_id: str
    status: Literal['not_submitted', 'submitted', 'failed']
    detail: str = ''
    backend_response: object | None = None


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Score = Annotated[StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)]


def check_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('来源必须为可追溯的 HTTP/HTTPS 链接')
    return value


def check_timestamp(value: str) -> str:
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('时间必须包含时区')
    return value


def expiry_timestamp(value: str | datetime | None) -> str | None:
    """调度元数据，不进入 Agent 载荷；统一 UTC 精度便于队列比较。"""
    if value is None:
        return None
    if isinstance(value, str):
        check_timestamp(value)
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('有效期必须为带时区的时间')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class TenderPayload(Payload):
    project_id: Text
    procurement_project_id: Text
    title: Text
    buyer: Text
    publish_date: Text
    amount_yuan: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)] | None
    project_summary: str
    source_url: Text
    signal_reason: Text
    relevance_score: Score | None
    amount_score: Score | None
    novelty_score: Score | None
    frequency_score: Score | None
    amount_reliable: StrictBool
    novelty_reliable: StrictBool
    frequency_reliable: StrictBool
    category: str = ''

    @field_validator('publish_date')
    @classmethod
    def calendar_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('公告日期必须为 YYYY-MM-DD')
        return value

    _url = field_validator('source_url')(check_url)

    @model_validator(mode='after')
    def available_scores(self):
        for name in ('amount', 'novelty', 'frequency'):
            if getattr(self, name + '_score') is None and getattr(self, name + '_reliable'):
                raise ValueError(f'{name} 分数未知时可靠性必须为 false')
        return self


class Clue(Payload):
    clue_id: Text
    source: Text
    platform: str = 'other'
    title: str = ''
    content: Text
    url: Text
    published_at: str = ''
    # 未知身份不使用“自媒体”代替事实。
    author_type: str = '未知'

    _url = field_validator('url')(check_url)

    @field_validator('published_at')
    @classmethod
    def publication(cls, value):
        if not value:
            return value
        if len(value) == 10 and date.fromisoformat(value).isoformat() == value:
            return value
        return check_timestamp(value)


class RumorPayload(Payload):
    subject_name: Text
    subject_credit_code: str = ''
    summary: Text
    source: Text
    signal_time: Text
    watch_focus: Text
    clues: list[Clue] = Field(min_length=1)

    _time = field_validator('signal_time')(check_timestamp)

    @model_validator(mode='after')
    def unique_clues(self):
        ids = [clue.clue_id for clue in self.clues]
        if len(set(ids)) != len(ids):
            raise ValueError('clue_id 不能重复')
        return self


def validate_signal(signal: SignalSubmission | dict) -> SignalSubmission:
    signal = SignalSubmission.model_validate(signal)
    model = TenderPayload if signal.kind == 'tender' else RumorPayload
    payload = model.model_validate(signal.payload).model_dump(mode='json', exclude_unset=True)
    return signal.model_copy(update={'payload': payload})
