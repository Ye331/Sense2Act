"""S6 裁定模型:意见(模型出)/ 判决(代码出)。

意见 JSON schema 为交接文档 §2.9 合成候选:
direction 四值而非 FEVER 三值——not-enough-info 落在代码侧(数根数的结果)。
"""
from __future__ import annotations

from pydantic import Field

from .base import ClaimStatus, Direction, SchemaModel


class EvidenceJudgment(SchemaModel):
    evidence_id: str
    direction: Direction
    quote: str                                # 必须为证据原文连续片段(验票)
    reason: str = ""


class SubclaimProposal(SchemaModel):
    """部分支撑提案(发现四,2026-10-16):模型对复合 claim 提出的"现有证据
    能完整支持的收窄断言"。提案≠落账——代码闸门(父条含信源条件谓词+待证实
    +非重复+证据引用合法)全过才派生子 claim(拆 claim 不拆态)。"""

    text: str
    evidence_ids: list[str] = []              # 必须是本次意见中判"支持"的证据
    reason: str = ""


class Opinion(SchemaModel):
    """(b) 步 LLM 意见:诞生于 (b),验于 (c),可被 (d) 否决,不落终态。"""

    claim_id: str
    evidence_judgments: list[EvidenceJudgment] = Field(min_length=1)
    verdict: ClaimStatus                      # 模型意见(仅意见)
    reason: str = ""
    subclaim: SubclaimProposal | None = None  # 部分支撑提案(可缺席=旧回放零迁移)


class VerdictResult(SchemaModel):
    """(d) 步代码终判 + (e) 归档结果。"""

    claim_id: str
    final_status: ClaimStatus
    support_roots: list[str] = []             # 独立谱系根(支持方)
    oppose_roots: list[str] = []              # 独立谱系根(反方)
    authoritative_oppose: bool = False        # 反方含权威登记源
    overridden: bool = False                  # LLM 意见被否决
    skipped: bool = False                     # 验票失败,维持原态
    note: str = ""
