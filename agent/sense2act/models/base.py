"""模型基类与领域枚举。

枚举值与架构文档术语严格对齐(中文域值进库,便于台账直读);
SectionState 依文档保留英文。camelCase 别名 + extra=forbid,
沿 signalx-agent 的 SchemaModel 惯例(回放/真实两模式走同一套校验)。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SchemaModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class ClaimStatus(str, Enum):
    PENDING = "待证实"
    VERIFIED = "已证实"
    CONTRADICTED = "矛盾"

    @property
    def terminal(self) -> bool:
        """终态 = 已证实/矛盾(矛盾也是终态:查清了,哪怕结论是假的)。"""
        return self in (ClaimStatus.VERIFIED, ClaimStatus.CONTRADICTED)


class ClaimCategory(str, Enum):
    FACT = "事实"
    ENTITY = "实体"
    RELATION = "关系"
    TEMPORAL = "时效"
    CONFLICT = "矛盾"


class TransitionCause(str, Enum):
    CONFLICT_FLIP = "矛盾翻转"
    TEMPORAL_FLIP = "时效翻转"
    STATE_REFRESH = "状态刷新"


class Direction(str, Enum):
    SUPPORT = "支持"
    OPPOSE = "矛盾"
    IRRELEVANT = "无关"
    REFRESH = "时效刷新"


class ActionType(str, Enum):
    TOOL = "查工具"
    EXPLORE = "explore"
    ASK = "问人"
    STOP = "停"


class SectionState(str, Enum):
    PENDING = "PENDING"
    STABLE_DRAFTED = "STABLE_DRAFTED"
    STALE = "STALE"
    REDRAFTED = "REDRAFTED"


class SectionKind(str, Enum):
    STANDARD = "standard"
    OPEN_QUESTIONS = "open_questions"
    RECOMMENDATION = "recommendation"


class AnchorStatus(str, Enum):
    ANCHORED = "已锚"
    UNANCHORED = "未锚"
    FORKED = "分叉"


class SignalType(str, Enum):
    OPPORTUNITY = "商机"
    COMPETITIVE = "竞情"
    RISK = "风险"


TERMINAL_STATUSES = (ClaimStatus.VERIFIED.value, ClaimStatus.CONTRADICTED.value)
