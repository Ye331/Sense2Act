"""AskGate 门1:分叉实在性(P1-8,2026-10-09 方案 §3.1)。纯代码零 LLM。

两层防线的第二道(经验法则:prompt 只能当第一道,代码兜底才是第二道):
S2 说"该问人"只是意见;若台账记分已给出明显主导假设,分叉已不存在,问人纯属
浪费一次人审——拦下,决策单改写为查工具(保主目标/缺口链),当轮照常 S3。

门 2(可裁决性:选项≥2+证据摘要)与门 4(预算:上限 2 次)为既有硬校验/上限,
不在此重复实现;本模块只做门 1 + "已裁决分叉不再问"(顺带防住 E1 式
"重复问已裁决口径"故障)。

只作用于"声明了 adjudicationTarget 且 kind=hypothesis"的问询;其余(含全部旧
录制件)恒放行且零日志——放行路径不落任何 decision_log 行,保旧件字节不变。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models.base import ActionType
from ..models.decision import DecisionSheet


@dataclass
class GateVerdict:
    blocked: bool
    reason: str = ""
    sheet: DecisionSheet | None = None   # 拦截时的改写单(action=查工具,其余字段原样)


class AskGate:
    """门1 分叉实在性:候选假设分差 ≥ θ 且最高分 > 0 → 已有主导,拦问。"""

    def __init__(self, ledger, threshold: int = 2):
        self._ledger = ledger
        self._theta = threshold

    def check(self, event_id: str, round_no: int, sheet: DecisionSheet) -> GateVerdict:
        pkg = sheet.question_package
        if (pkg is None or pkg.adjudication_target is None
                or pkg.adjudication_target.kind != "hypothesis"):
            return GateVerdict(False)            # 非假设分叉问询:门不越权,放行
        candidates = [m.target_id for m in pkg.adjudication_target.map]

        # 已裁决分叉不再问(E1 式"重复问已裁决口径"的确定性防线)
        asked_before = {r["target_id"] for r in self._ledger.conn.execute(
            "SELECT DISTINCT target_id FROM adjudications"
            " WHERE event_id=? AND target_kind='hypothesis'", (event_id,))}
        repeat = [c for c in candidates if f"{event_id}:{c}" in asked_before]
        if repeat:
            return self._block(event_id, round_no, sheet, pkg,
                               f"候选假设 {','.join(repeat)} 已被人工裁决过,不再重复问")

        standings = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
                     for s in self._ledger.hypothesis_standings(event_id)}
        scores = [standings[c]["score"] for c in candidates if c in standings]
        if len(scores) < 2:
            return GateVerdict(False)            # 候选不可映射:不猜,放行(S2 已过格式硬校验)
        ordered = sorted(scores, reverse=True)
        best, gap = ordered[0], ordered[0] - ordered[1]
        if best <= 0:
            self._log_pass(event_id, round_no, pkg, candidates, scores, best, gap,
                           "无正分主导,分叉实在,放行问询")
            return self._gate3(event_id, round_no, sheet, pkg, candidates)
        if gap >= self._theta:
            return self._block(
                event_id, round_no, sheet, pkg,
                f"候选假设分差 {gap} ≥ {self._theta}(最高分 {best}),已有主导,不必问人",
                candidates=candidates, scores=scores)
        self._log_pass(event_id, round_no, pkg, candidates, scores, best, gap,
                       f"分差 {gap} < {self._theta},分叉实在,放行问询")
        return self._gate3(event_id, round_no, sheet, pkg, candidates)

    def _gate3(self, event_id: str, round_no: int, sheet: DecisionSheet,
               pkg, candidates: list[str]) -> GateVerdict:
        """门3 裁决增益(P3):反事实覆写重算——任一候选被选中会改变主导集 →
        过门;全部候选都不变 → 拦(改写查工具,落 askgate_blocked)。

        与门顺序:门1 拦截路径不进本门(否则门1 被架空)。诚实语义:绝对覆写
        之下有成员的双候选分叉几乎总能翻,本门的真实作用=过滤退化问询——
        (a)候选全部无成员(悬空假设);(b)候选不含真正的竞争主导方
        (选中任一候选都动摇不了台外的第三假设)。B1/B2(主导对错)不可分是
        standings-only 分数的本性(AUROC=0.75 已证),真值定标属 live 阶段。
        """
        standings = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
                     for s in self._ledger.hypothesis_standings(event_id)}
        members = self._member_counts(event_id)

        def dominant_after(chosen: str) -> set[str]:
            scores = {hyp: (members.get(chosen, 0) if hyp == chosen else
                            (-members.get(hyp, 0) if hyp in candidates
                             else s["score"]))
                      for hyp, s in standings.items()}
            top = max(scores.values(), default=0)
            return {h for h, v in scores.items() if v == top and top > 0}

        current = {h for h, s in standings.items() if s.get("dominant")}
        for cand in candidates:
            if dominant_after(cand) != current:
                return GateVerdict(False)            # 有裁决增益,过门
        reason = ("反事实重算:任一候选被选中都不改变主导集 "
                  f"{sorted(current) if current else '(无)'}"
                  "(候选无成员或不含真正的竞争主导方),问人无裁决增益")
        rewritten = sheet.model_copy(update={"action": ActionType.TOOL})
        self._ledger.log(event_id, round_no, "澄清", {
            "mode": "askgate_blocked",
            "gate": "门3_裁决增益",
            "reason": reason,
            "question": pkg.question,
            "candidates": candidates,
            "current_dominant": sorted(current),
            "realism": 0,
            "rewritten_action": "查工具",
            "main_target": sheet.main_target,
        })
        return GateVerdict(True, reason, rewritten)

    def _member_counts(self, event_id: str) -> dict[str, int]:
        """{假设草案id: 成员 claim 数}(含无成员假设=0;raw SQL 零台账改动)。"""
        rows = self._ledger.conn.execute(
            "SELECT h.hypothesis_id AS hid, COUNT(hc.claim_id) AS n"
            " FROM hypotheses h LEFT JOIN hypothesis_claims hc"
            " ON hc.hypothesis_id=h.hypothesis_id"
            " WHERE h.event_id=? GROUP BY h.hypothesis_id", (event_id,)).fetchall()
        return {r["hid"].rsplit(":", 1)[-1]: r["n"] for r in rows}

    def _log_pass(self, event_id: str, round_no: int, pkg,
                  candidates: list[str], scores: list[int],
                  best: int, gap: int, reason: str) -> None:
        """放行审计(P2):AUROC 需要 (gate 分数, 问了会否翻正) 对——只对新流
        (声明 hypothesis 目标且可映射)的 ask 落一条;旧件无该声明恒零日志。
        realism = max(0, θ-ω):分叉实在度分数,越高越该问(拦下行 realism=0)。"""
        self._ledger.log(event_id, round_no, "澄清", {
            "mode": "askgate_passed",
            "gate": "门1_分叉实在性",
            "reason": reason,
            "question": pkg.question,
            "candidates": candidates,
            "scores": scores,
            "best": best,
            "gap": gap,
            "realism": max(0, self._theta - gap),
        })

    # ------------------------------------------------------------------

    def _block(self, event_id: str, round_no: int, sheet: DecisionSheet,
               pkg, reason: str, candidates: list[str] | None = None,
               scores: list[int] | None = None) -> GateVerdict:
        rewritten = sheet.model_copy(update={"action": ActionType.TOOL})
        self._ledger.log(event_id, round_no, "澄清", {
            "mode": "askgate_blocked",
            "gate": "门1_分叉实在性",
            "reason": reason,
            "question": pkg.question,
            "candidates": candidates or [],
            "scores": scores or [],
            "realism": 0,                        # 拦下行:分叉已实在性耗尽(AUROC 抽取统一口径)
            "rewritten_action": "查工具",
            "main_target": sheet.main_target,
        })
        return GateVerdict(True, reason, rewritten)
