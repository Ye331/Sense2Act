"""S2 决策单(含硬校验)与统一提问机制的问题包。

决策单只放判断不放参数(决策 #11):平台由 S3 规则表推导,
query 由搜索提示顶上,explore 预算走全局默认。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ActionType, SchemaModel


class GapStep(SchemaModel):
    """缺口链一环:WHAT 不是 HOW;验收标准必填("什么算查到了")。"""

    gap: str
    acceptance: str
    failover: str = ""                        # 失败分支(取下一环的条件)


class AdjudicationMapping(SchemaModel):
    """裁决映射一条:选项原文 -> 草案 id(hypothesis→如 H1;claim/evidence→如 C1)。

    目标只放草案 id,应用时由裁决器补 {event_id}: 前缀——决策单只放判断不放参数(决策 #11)。
    """

    option: str
    target_id: str


class AdjudicationTarget(SchemaModel):
    """问人裁决目标声明(P1-8,2026-10-09):答案选项落到台账的哪个对象上。

    kind 三选一:hypothesis=假设覆写(standings 钉死)/ claim=口径重述(派生新条)/
    evidence=证据登记(人答作为一份证言入账)。map 为每个选项到目标的一一映射。
    """

    kind: Literal["hypothesis", "claim", "evidence"]
    map: list[AdjudicationMapping] = Field(min_length=2)


class QuestionPackage(SchemaModel):
    """统一提问机制的问题包(三触发点共用:选择题+证据摘要)。"""

    question: str
    options: list[str] = Field(min_length=2)
    evidence_summary: str
    adjudication_target: AdjudicationTarget | None = None   # 可选;旧件无该字段照常解析

    @model_validator(mode="after")
    def _check_target_map(self) -> "QuestionPackage":
        """结构硬校验:map 与 options 一一对应;hypothesis 裁决各选项目标不得重复。

        (目标 id 的存在性不在此校验——models 层不碰台账,放应用时确定性拦截。)
        """
        target = self.adjudication_target
        if target is None:
            return self
        if sorted(m.option for m in target.map) != sorted(self.options):
            raise ValueError("adjudicationTarget.map 必须与 options 一一对应(不得缺项/重复)")
        if target.kind == "hypothesis" and (
                len({m.target_id for m in target.map}) != len(target.map)):
            raise ValueError("hypothesis 裁决的各选项必须映射到不同假设")
        return self


class DecisionSheet(SchemaModel):
    round: int = Field(ge=1)
    main_target: str                          # 未决 claim 的草案 ID(如 "C1")
    action: ActionType
    gap_chain: list[GapStep] = Field(min_length=1)
    search_hints: list[str] = []              # 2-3 条种子词,仅提示可偏离
    side_targets: list[str] = []              # 同源搭车,不驱动规划
    question_package: QuestionPackage | None = None
    stop_reason: str = ""
    degraded: bool = False                    # planner_degraded 标记


def validate_decision_sheet(sheet: DecisionSheet, open_claim_ids: list[str]) -> None:
    """代码硬校验:主目标防幻觉 id / 缺口链必带验收 / 问人必带选项与证据摘要 / 停必带理由。"""
    issues: list[str] = []
    if sheet.action != ActionType.STOP and sheet.main_target not in open_claim_ids:
        issues.append(f"主目标 {sheet.main_target!r} 不在未决 claim 清单中(防幻觉 id)")
    for i, step in enumerate(sheet.gap_chain, 1):
        if not step.acceptance.strip():
            issues.append(f"缺口链第 {i} 环缺验收标准")
    if sheet.action == ActionType.ASK:
        if sheet.question_package is None:
            issues.append("动作=问人 但缺问题包")
        elif len(sheet.question_package.options) < 2:
            issues.append("问题包选项不足 2 个")
        elif not sheet.question_package.evidence_summary.strip():
            issues.append("问题包缺证据摘要")
    if sheet.action == ActionType.STOP and not sheet.stop_reason.strip():
        issues.append("动作=停 但缺停止理由")
    if issues:
        raise ValueError("决策单校验失败: " + "; ".join(issues))


def conservative_sheet(round_no: int, top_claim: str, claim_text: str) -> DecisionSheet:
    """planner_degraded:LLM 失败时代码生成的保守决策单,循环不卡死。"""

    return DecisionSheet(
        round=round_no,
        main_target=top_claim,
        action=ActionType.TOOL,
        gap_chain=[GapStep(
            gap=f"为断言『{claim_text[:50]}』寻找直接证据",
            acceptance="获得与该断言直接相关的权威登记源或相互独立的来源证据",
            failover="换平台或换关键词重查一次",
        )],
        search_hints=[claim_text[:20]],
        degraded=True,
    )
