"""S3 执行层(双路版):路径 A 规则表->采集;路径 B 探索器(骨架=Stub,不可达)。

回执即观察:失败回执与成功同构入观察包(O2 完整性);探索器触达坚硬平台
直接拒绝并回"不可达" -> claim 标不可达 -> 待证实 -> Open Questions(三态纪律兜底)。
"""
from __future__ import annotations

from typing import Protocol

from ..collect.expansion import candidate_queries
from ..collect.gate import NetworkGate
from ..collect.rules import ToolRouter
from ..models.decision import DecisionSheet, GapStep
from ..models.observation import (
    CollectReceipt,
    EvidenceItemLite,
    ObservationPackage,
    SourceReport,
    TrajectoryStep,
)
from ..store.ledger import normalize_text


class Explorer(Protocol):
    def explore(self, gap: GapStep, hints: list[str]) -> dict: ...


class StubExplorer:
    """占位探索器:一律不可达(保留用于对照测试)。"""

    def explore(self, gap: GapStep, hints: list[str], claim_text: str = "") -> dict:
        return {
            "reachable": False,
            "items": [],
            "trajectory": [TrajectoryStep(
                desc=f"探索器未接入:缺口『{gap.gap[:40]}』标记不可达"
                     f"(验收标准: {gap.acceptance[:40]})", depth=0)],
        }


class WordFamilyExplorer:
    """路径 B 探索器 v1:换词重试、深度+1(确定性,零 LLM)。

    循环:候选查询(candidate_queries:S2 提示 d0 -> 缺口重建长项 d1 -> 短项 d2)
    逐一经路由+闸门尝试;命中即退出;候选耗尽/深度上限/调用上限 -> 不可达
    (claim 标不可达 -> Open Questions,三态纪律兜底)。
    v1 边界:命中判据=非空回执;验收自评(结果是否满足 gap.acceptance)
    与真实网页抓取属真探索器(P1)。每次尝试入轨迹(B->A 迁移原料)。
    """

    def __init__(self, gate: NetworkGate, router: ToolRouter,
                 depth_max: int = 3, calls_max: int = 15):
        self._gate = gate
        self._router = router
        self._depth_max = depth_max
        self._calls_max = calls_max

    def explore(self, gap: GapStep, hints: list[str], claim_text: str = "") -> dict:
        trajectory: list[TrajectoryStep] = []
        merged: list[EvidenceItemLite] = []
        seen_keys: set[str] = set()
        attempts = 0
        # 预算双层制:单次探索上限 min(自身上限, 闸门剩余)——防一次探索烧穿全局
        budget = min(self._calls_max,
                     max(0, getattr(self._gate, "remaining", self._calls_max)))
        if budget == 0:
            return {"reachable": False, "items": [], "trajectory": [TrajectoryStep(
                desc="全局采集预算不足,探索跳过(预算饥饿,非不可达)",
                depth=0)]}
        for depth, query in candidate_queries(
                hints, gap.gap, claim=claim_text, max_depth=self._depth_max):
            if attempts >= budget:
                trajectory.append(TrajectoryStep(
                    desc=f"探索预算耗尽({budget} 次调用)", depth=depth))
                break
            attempts += 1
            req = self._router.route(gap.gap, [query], claim_text)
            req = req.model_copy(update={"query": query})
            receipt = self._gate.send(req)
            trajectory.append(TrajectoryStep(
                desc=f"d{depth} 查询『{query}』-> "
                     f"{','.join(r.status for r in receipt.source_reports) or receipt.status}"
                     f"({len(receipt.items)} 条)",
                depth=depth))
            if receipt.items:
                for item in receipt.items:
                    key = item.dedup_key or f"{item.source}|{item.title}|{item.content}"[:120]
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    merged.append(item)
                return {
                    "reachable": True, "items": merged,
                    "trajectory": trajectory,
                    "accepted_query": query,
                }
        trajectory.append(TrajectoryStep(
            desc=f"全部 {attempts} 次换词尝试未命中,缺口标记不可达"
                 f"(验收标准: {gap.acceptance[:40]})", depth=self._depth_max))
        return {"reachable": False, "items": [], "trajectory": trajectory}


class Executor:
    def __init__(self, gate: NetworkGate, router: ToolRouter, explorer: Explorer):
        self._gate = gate
        self._router = router
        self._explorer = explorer

    @property
    def collect_calls(self) -> int:
        return self._gate.calls

    def execute(
        self, sheet: DecisionSheet, round_no: int, claim_text: str,
        unreachable_callback=None, search_context: dict | None = None,
    ) -> tuple[ObservationPackage, str]:
        """返回 (观察包, 质量信号 ok|poor|unreachable)。失败回执也入包(不伪装)。

        search_context = 信封精确定位字段(采购人/项目编号/发布日期),下传给
        采集适配器做定向检索;explore 路径不消费(探索器换词自有语义)。
        """
        head = sheet.gap_chain[0]
        if sheet.action.value == "explore":
            outcome = self._explorer.explore(head, sheet.search_hints, claim_text)
            if not outcome.get("reachable"):
                if unreachable_callback:
                    unreachable_callback(sheet.main_target)
                obs = ObservationPackage(
                    round=round_no, main_target=sheet.main_target,
                    action_brief=f"explore 不可达: {head.gap[:60]}",
                    items=[], source_reports=[], trajectory=outcome["trajectory"])
                return obs, "unreachable"
            items = [EvidenceItemLite.model_validate(i) for i in outcome.get("items", [])]
            accepted = outcome.get("accepted_query", "")
            obs = ObservationPackage(
                round=round_no, main_target=sheet.main_target,
                action_brief=f"explore 命中(『{accepted}』): {head.gap[:40]}",
                items=items, source_reports=[SourceReport(
                    platform="explorer", status="success", count=len(items))],
                trajectory=outcome.get("trajectory"))
            return obs, ("ok" if items else "poor")

        # 路径 A:查工具(平台/工具由规则表推导,决策单不带参数)
        req = self._router.route(head.gap, sheet.search_hints, claim_text,
                                 search_context=search_context)
        receipt = self._gate.send(req)
        if not receipt.items and getattr(self._gate, "remaining", 0) >= 3:
            # 一跳扑空且有足够余量 -> 换词重试(探索器 v1,不消耗新轮;
            # 余量不足时放弃探索,把预算留给后续轮次)
            # 一跳扑空 -> 换词重试(探索器 v1,不消耗新轮)
            outcome = self._explorer.explore(head, sheet.search_hints, claim_text)
            if outcome.get("reachable") and outcome.get("items"):
                items = [EvidenceItemLite.model_validate(i)
                         for i in outcome["items"]]
                obs = ObservationPackage(
                    round=round_no, main_target=sheet.main_target,
                    action_brief=f"查工具+换词命中(『{outcome.get('accepted_query', '')}』)"
                                 f": {head.gap[:40]}",
                    items=items, source_reports=[SourceReport(
                        platform="explorer", status="success", count=len(items))],
                    trajectory=outcome.get("trajectory"))
                return obs, "ok"
        obs = self._assemble(sheet, round_no, receipt)
        quality = "ok" if receipt.items or receipt.status == "success" else "poor"
        return obs, quality

    def requery(self, sheet: DecisionSheet, round_no: int, claim_text: str,
                fallback_platforms: list[str],
                search_context: dict | None = None) -> tuple[ObservationPackage, str]:
        """CRAG 回边:质量差 -> 换平台重查一次(不消耗新轮)。"""
        head = sheet.gap_chain[0]
        req = self._router.route(head.gap, sheet.search_hints, claim_text,
                                 search_context=search_context)
        req = req.model_copy(update={
            "platforms": fallback_platforms or req.platforms,
            "query": (req.query + " " + (sheet.search_hints[0]
                        if sheet.search_hints else ""))[:120].strip(),
        })
        receipt = self._gate.send(req)
        return self._assemble(sheet, round_no, receipt, tag="crag_requery"), (
            "ok" if receipt.items else "poor")

    # ------------------------------------------------------------------

    def _assemble(
        self, sheet: DecisionSheet, round_no: int, receipt: CollectReceipt,
        tag: str = "",
    ) -> ObservationPackage:
        """观察包组装(纯代码):去重合并 / 附全部回执(含失败) / 轨迹条件性。"""
        seen: set[str] = set()
        merged: list[EvidenceItemLite] = []
        for item in receipt.items:
            key = normalize_text(item.source + "|" + item.title + "|" + item.content)[:200]
            key = str(hash(key)) if not item.dedup_key else item.dedup_key
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        brief = f"{sheet.action.value} -> {receipt.request.tool}"
        if tag:
            brief += f"({tag})"
        return ObservationPackage(
            round=round_no, main_target=sheet.main_target, action_brief=brief,
            items=merged, source_reports=receipt.source_reports)
