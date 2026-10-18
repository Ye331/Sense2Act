"""招投标公告、检测请求和响应模型。"""
from datetime import date
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from signals.common.schemas import SignalSubmission, SubmissionResult

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Announcement(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: NonEmpty
    title: NonEmpty
    buyer: str = ''
    publish_date: date
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    project_summary: str = ''
    source_url: str = ''
    project_number: str = ''
    announcement_type: str = ''
    category: str = ''
    content: str = ''
    content_source_type: str = ''
    content_source_note: str = ''
    source_file: str = ''
    source_sheet: str = ''
    source_row: int | None = None
    data_origin: str = 'real'

    @field_validator('publish_date', mode='before')
    @classmethod
    def strict_date(cls, value):
        if isinstance(value, str):
            if len(value) != 10 or date.fromisoformat(value).isoformat() != value:
                raise ValueError('日期必须为 YYYY-MM-DD')
        elif type(value) is not date:
            raise ValueError('日期必须为 YYYY-MM-DD')
        return value

    @field_validator('data_origin')
    @classmethod
    def only_real(cls, value):
        if value != 'real':
            raise ValueError('正式检测只接受真实公告')
        return value

    @field_validator('amount', mode='before')
    @classmethod
    def amount_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError('金额不能是布尔值')
        return value

    @field_validator('source_url')
    @classmethod
    def traceable_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                raise ValueError('原公告链接必须是 HTTP/HTTPS URL')
        return value


class DetectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    company_id: NonEmpty
    company_profile: NonEmpty
    detection_date: date
    history_start_date: date
    history: list[Announcement]
    current: list[Announcement]

    @field_validator('company_id', mode='before')
    @classmethod
    def stringify_company(cls, value):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator('detection_date', 'history_start_date', mode='before')
    @classmethod
    def strict_date(cls, value):
        return Announcement.strict_date(value)

    @model_validator(mode='after')
    def check_coverage(self):
        if self.history_start_date > self.detection_date:
            raise ValueError('历史覆盖起始日不能晚于检测日')
        if any(not self.history_start_date <= row.publish_date < self.detection_date for row in self.history):
            raise ValueError('History 必须在覆盖区间内且严格早于检测日')
        if any(row.publish_date != self.detection_date for row in self.current):
            raise ValueError('Current 必须全部在检测日发布，旧公告须按发布日期处理')
        return self


class DetectionResponse(BaseModel):
    company_id: str
    detection_date: str
    records: list[dict]
    signals: list[SignalSubmission]
    submissions: list[SubmissionResult]
    normalization: dict
