"""S1 产出与台账侧模型。"""
from __future__ import annotations

from pydantic import Field

from .base import (
    AnchorStatus,
    ClaimCategory,
    ClaimStatus,
    SchemaModel,
    SectionKind,
    SignalType,
)


class ClaimDraft(SchemaModel):
    """S1b 产出的 claim 草案:出生即带调查属性(调查感知分解)。

    priority: 1-5,5 最高 = check-worthiness x 解锁效应 x 时效衰减 的综合判断;
    hypothesis_ids: 假设归属(服务于哪个竞争假设);
    section_index: 节-claim 唯一映射(只指向 0-3 四个标准节)。
    """

    id: str                                  # 草案 ID,如 "C1"(入库时代码加 event 前缀)
    text: str
    category: ClaimCategory
    section_index: int = Field(ge=0, le=3)
    priority: int = Field(default=3, ge=1, le=5)
    priority_reason: str = ""
    hypothesis_ids: list[str] = []
    valid_window_days: int | None = None      # 仅时效类
    refresh_cycle_days: int | None = None
    relation_type: str | None = None          # 仅关系类,须命中词表
    entity_names: list[str] = []              # 文中提到的实体名(供入账锚定)
    corpus_bound: bool = False                # S1b 标注:信封/语料内自证型断言
                                              # (true=复述/记载/字段取值;世界断言必须 false,
                                              #  拿不准 false。模型标注,代码判决——S6 第一方自证例外)


class HypothesisSeed(SchemaModel):
    id: str                                  # 如 "H0"
    label: str                               # 如 "竞对放风"
    description: str = ""
    claim_ids: list[str] = []                 # 组成(登记轮次可为空 = "未浮现")
    op: str = "AND"                           # AND | OR


class GapDraft(SchemaModel):
    """wh 型信息缺口(S1b 登记,喂 S2)。"""

    text: str
    related_claim_id: str | None = None


class QualificationResult(SchemaModel):
    """S1a 快速定性(轻分析):信号类型 + 假设种子选择。"""

    signal_type: SignalType
    reason: str
    selected_seed_labels: list[str] = []      # 从配置词表中选的种子标签
    basis_gaps: list[str] = []                # "依据不足"等登记为待查缺口


class AnalysisBlueprint(SchemaModel):
    """S1b 重分析:claim 清单 + 假设登记 + 缺口(解读定了之后才生成)。"""

    claims: list[ClaimDraft]
    hypotheses: list[HypothesisSeed]
    gaps: list[GapDraft]


class EntityRecord(SchemaModel):
    entity_id: str
    name: str
    canonical_name: str
    type: str = "company"                     # company|person|account|organization
    disambiguation: AnchorStatus = AnchorStatus.UNANCHORED
    credit_code: str = ""
    anchor_status: AnchorStatus = AnchorStatus.UNANCHORED
    first_event_id: str = ""


class Recommendation(SchemaModel):
    """行动建议(S7 第六节):每条强制挂依据链(溯源式建议)。"""

    action: str
    rationale: str
    basis_claim_ids: list[str] = Field(min_length=1)
    hypothesis_label: str = ""
