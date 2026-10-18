"""AskGate 门1(分叉实在性):纯代码零 LLM;只拦"已有主导的假设分叉问询"。"""
from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ALIASES, make_ledger, rumor_envelope  # noqa: E402

from sense2act.models.base import ActionType, ClaimCategory  # noqa: E402
from sense2act.models.decision import (  # noqa: E402
    AdjudicationMapping,
    AdjudicationTarget,
    DecisionSheet,
    GapStep,
    QuestionPackage,
)
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    HypothesisSeed,
)
from sense2act.stages.adjudicate import Adjudicator  # noqa: E402
from sense2act.stages.askgate import AskGate  # noqa: E402

EID = "evt_gate"


def _ledger_with_gap(h0_verified: int) -> object:
    """H0 成员 C1/C2、H1 成员 C3;前 h0_verified 条置已证实 → H0 分数=h0_verified。"""
    led = make_ledger()
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C2", text="B公司账户被冻结", category=ClaimCategory.FACT,
                       section_index=2, priority=4, entity_names=["B公司"]),
            ClaimDraft(id="C3", text="B公司正常发薪", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
        ],
        hypotheses=[
            HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1", "C2"]),
            HypothesisSeed(id="H1", label="正常经营", claim_ids=["C3"]),
        ],
        gaps=[])
    led.register_event(rumor_envelope(EID), "", EID)
    led.install_blueprint(EID, bp, aliases=ALIASES)
    for i, cid in enumerate(("C1", "C2")):
        if i < h0_verified:
            led.update_claim_status(f"{EID}:{cid}", "已证实", None, 1)
    return led


def _sheet(pkg: QuestionPackage) -> DecisionSheet:
    return DecisionSheet(
        round=2, main_target="C1", action=ActionType.ASK,
        gap_chain=[GapStep(gap="g", acceptance="a")],
        question_package=pkg)


def _pkg(kind: str = "hypothesis") -> QuestionPackage:
    target = None if kind is None else AdjudicationTarget(kind=kind, map=[
        AdjudicationMapping(option="甲", target_id="H0"),
        AdjudicationMapping(option="乙", target_id="H1")])
    return QuestionPackage(question="按哪个假设继续?", options=["甲", "乙"],
                           evidence_summary="证据两可", adjudication_target=target)


class TestAskGate(unittest.TestCase):
    def test_dominant_gap_blocks_and_rewrites_to_tool(self):
        led = _ledger_with_gap(h0_verified=2)          # H0=2, H1=0 → gap=2
        gate = AskGate(led, threshold=2)
        verdict = gate.check(EID, 2, _sheet(_pkg()))
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.sheet.action, ActionType.TOOL)
        self.assertEqual(verdict.sheet.main_target, "C1")   # 改写保主目标
        self.assertEqual(len(verdict.sheet.gap_chain), 1)
        audits = [r for r in led.decision_log_rows(EID)
                  if "askgate_blocked" in r["payload_json"]]
        self.assertEqual(len(audits), 1)

    def test_small_gap_passes_with_audit(self):
        # P2 契约更新:评估过分的放行落恰一条 askgate_passed(AUROC 原料:
        # realism=θ-gap 越高越该问);非 hypothesis 问询仍恒零日志(见下)。
        led = _ledger_with_gap(h0_verified=1)          # gap=1 < θ
        before = len(led.decision_log_rows(EID))
        verdict = AskGate(led, threshold=2).check(EID, 2, _sheet(_pkg()))
        self.assertFalse(verdict.blocked)
        payloads = [json.loads(r["payload_json"])
                    for r in led.decision_log_rows(EID)][before:]
        passed = [p for p in payloads if p.get("mode") == "askgate_passed"]
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0]["gap"], 1)
        self.assertEqual(passed[0]["realism"], 1)      # realism = θ - gap
        self.assertEqual(sorted(passed[0]["candidates"]), ["H0", "H1"])
        self.assertEqual(passed[0]["scores"], [1, 0])

    def test_no_positive_leader_passes(self):
        led = _ledger_with_gap(h0_verified=0)          # 全待证实:无正分主导
        verdict = AskGate(led, threshold=2).check(EID, 2, _sheet(_pkg()))
        self.assertFalse(verdict.blocked)

    def test_non_hypothesis_ask_passes(self):
        led = _ledger_with_gap(h0_verified=2)
        before = len(led.decision_log_rows(EID))
        for kind in (None, "claim", "evidence"):
            verdict = AskGate(led).check(EID, 2, _sheet(_pkg(kind)))
            self.assertFalse(verdict.blocked, kind)
        self.assertEqual(len(led.decision_log_rows(EID)), before)

    def test_already_adjudicated_fork_not_repeated(self):
        led = _ledger_with_gap(h0_verified=0)          # 无主导本应放行…
        Adjudicator(led).apply(EID, 2, _pkg(), "甲")   # …但已人工裁决过
        verdict = AskGate(led, threshold=2).check(EID, 3, _sheet(_pkg()))
        self.assertTrue(verdict.blocked)
        self.assertIn("已", verdict.reason)

    def test_gate_is_pure_code_no_llm(self):
        """结构性证明:构造参数只有 ledger/threshold,零 LLM 依赖。"""
        params = list(inspect.signature(AskGate.__init__).parameters)
        self.assertEqual(params, ["self", "ledger", "threshold"])
        params = list(inspect.signature(AskGate.check).parameters)
        self.assertEqual(params, ["self", "event_id", "round_no", "sheet"])


if __name__ == "__main__":
    unittest.main()
