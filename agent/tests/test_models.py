"""模型与 LLM 层:决策单硬校验 / 回放纪律 / 预算护栏。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.llm import (  # noqa: E402
    ModelBudgetExceeded,
    ReplayLLM,
    call_structured,
    generate_json,
    model_call_budget,
    parse_json_object,
)
from sense2act.models.base import ActionType  # noqa: E402
from sense2act.models.decision import (  # noqa: E402
    DecisionSheet,
    GapStep,
    QuestionPackage,
    conservative_sheet,
    validate_decision_sheet,
)
from sense2act.models.intel import QualificationResult  # noqa: E402


def sheet(**kw):
    base = dict(round=1, main_target="C1", action=ActionType.TOOL,
                gap_chain=[GapStep(gap="g", acceptance="a")])
    base.update(kw)
    return DecisionSheet(**base)


class TestDecisionSheet(unittest.TestCase):
    def test_hallucinated_target_rejected(self):
        with self.assertRaises(ValueError):
            validate_decision_sheet(sheet(main_target="C99"), ["C1", "C2"])

    def test_missing_acceptance_rejected(self):
        bad = sheet(gap_chain=[GapStep(gap="g", acceptance=" ")])
        with self.assertRaises(ValueError):
            validate_decision_sheet(bad, ["C1"])

    def test_ask_requires_package(self):
        with self.assertRaises(ValueError):
            validate_decision_sheet(
                sheet(action=ActionType.ASK, main_target="C1"), ["C1"])
        ok = sheet(action=ActionType.ASK, question_package=QuestionPackage(
            question="q", options=["甲", "乙"], evidence_summary="s"))
        validate_decision_sheet(ok, ["C1"])

    def test_stop_requires_reason(self):
        with self.assertRaises(ValueError):
            validate_decision_sheet(
                sheet(action=ActionType.STOP, main_target="C1"), ["C1"])
        validate_decision_sheet(
            sheet(action=ActionType.STOP, main_target="C1", stop_reason="查清了"),
            ["C1"])

    def test_conservative_sheet_is_valid(self):
        s = conservative_sheet(3, "C2", "某断言文本")
        validate_decision_sheet(s, ["C2"])
        self.assertTrue(s.degraded)


class TestReplayLLM(unittest.TestCase):
    def test_strict_order(self):
        r = ReplayLLM([
            {"stage": "s1a", "response": {"signalType": "风险", "reason": "x",
                                          "selectedSeedLabels": [], "basisGaps": []}},
            {"stage": "s1b", "response": {"claims": [], "hypotheses": [],
                                          "gaps": []}}])
        out = call_structured(r, stage="s1a", system="s", user="u",
                              model_cls=QualificationResult)
        self.assertEqual(out.signal_type.value, "风险")

    def test_misalignment_fails_fast(self):
        r = ReplayLLM([{"stage": "s1b", "response": {}}])
        with self.assertRaises(RuntimeError):
            r.generate_json("s", "u", stage="s1a")

    def test_exhausted_raises(self):
        r = ReplayLLM([{"stage": "s1a", "response": {}}])
        r.generate_json("s", "u", stage="s1a")
        with self.assertRaises(RuntimeError):
            r.generate_json("s", "u", stage="s1a")

    def test_retry_with_feedback_consumes(self):
        """call_structured 校验失败重试:每次尝试消耗一条回放。"""
        r = ReplayLLM([
            {"stage": "s1a", "response": {"signalType": "不存在的类型",
                                          "reason": "x", "selectedSeedLabels": [],
                                          "basisGaps": []}},
            {"stage": "s1a", "response": {"signalType": "风险", "reason": "x",
                                          "selectedSeedLabels": [],
                                          "basisGaps": []}},
        ])
        out = call_structured(r, stage="s1a", system="s", user="u",
                              model_cls=QualificationResult)
        self.assertEqual(out.signal_type.value, "风险")


class TestBudgetAndParse(unittest.TestCase):
    def test_budget_guard(self):
        r = ReplayLLM([{"stage": "x", "response": {}}] * 10)
        with model_call_budget(2):
            generate_json(r, "s", "u", stage="x")
            generate_json(r, "s", "u", stage="x")
            with self.assertRaises(ModelBudgetExceeded):
                generate_json(r, "s", "u", stage="x")

    def test_parse_fenced_json(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('前置说明 {"a": {"b": 2}} 后置'),
                         {"a": {"b": 2}})
        with self.assertRaises(ValueError):
            parse_json_object("完全不是 JSON")


if __name__ == "__main__":
    unittest.main()
