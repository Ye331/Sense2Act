"""门3 裁决增益(P3):反事实覆写重算单元 + θ_ambig 机械定标矩阵。

定标纪律(方案 §3.3):本文件矩阵是**机械定标**(拦⟺gap≥θ 的重构性验证);
真值标注定标(应拦/应放两簇)协议预注册于方案 §3.3.2,待 live 问询后执行。
"""
from __future__ import annotations

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

EID = "evt_g3"


def _ledger(k_verified: int, h1_members: int = 1):
    """H0 三成员(C1/C3/C4,前 k_verified 条已证实)/ H1 h1_members 个成员(C2…)。"""
    led = make_ledger()
    h1_claims = [ClaimDraft(id=f"C{10 + i}", text=f"B公司虚指标记{i}",
                            category=ClaimCategory.FACT, section_index=2)
                 for i in range(h1_members)]
    h1_ids = (["C2"] + [d.id for d in h1_claims]) if h1_members else []
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, entity_names=["B公司"]),
            ClaimDraft(id="C2", text="B公司正常发薪", category=ClaimCategory.FACT,
                       section_index=2, entity_names=["B公司"]),
            ClaimDraft(id="C3", text="B公司账户冻结", category=ClaimCategory.FACT,
                       section_index=2, entity_names=["B公司"]),
            ClaimDraft(id="C4", text="B公司供应商断供", category=ClaimCategory.FACT,
                       section_index=2, entity_names=["B公司"]),
            *h1_claims,
        ],
        hypotheses=[
            HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1", "C3", "C4"]),
            HypothesisSeed(id="H1", label="正常经营", claim_ids=h1_ids),
        ],
        gaps=[])
    led.register_event(rumor_envelope(EID), "", EID)
    led.install_blueprint(EID, bp, aliases=ALIASES)
    for i, cid in enumerate(("C1", "C3", "C4")):
        if i < k_verified:
            led.update_claim_status(f"{EID}:{cid}", "已证实", None, 1)
    return led


def _pkg(map_h0: str = "H0", map_h1: str = "H1") -> QuestionPackage:
    return QuestionPackage(
        question="按哪个假设继续?", options=["甲", "乙"], evidence_summary="两可",
        adjudication_target=AdjudicationTarget(kind="hypothesis", map=[
            AdjudicationMapping(option="甲", target_id=map_h0),
            AdjudicationMapping(option="乙", target_id=map_h1)]))


def _sheet(pkg: QuestionPackage) -> DecisionSheet:
    return DecisionSheet(round=2, main_target="C1", action=ActionType.ASK,
                         gap_chain=[GapStep(gap="g", acceptance="a")],
                         question_package=pkg)


def _gate_logs(led) -> list[dict]:
    return [json.loads(r["payload_json"]) for r in led.decision_log_rows(EID)
            if r["kind"] == "澄清"
            and json.loads(r["payload_json"]).get("mode") == "askgate_blocked"]


class TestGate3Counterfactual(unittest.TestCase):
    def test_healthy_fork_passes_gate3(self):
        """双候选均有成员:门1 放行后门3 有增益,过门(无 blocked 日志)。"""
        led = _ledger(k_verified=0)
        verdict = AskGate(led, threshold=2).check(EID, 2, _sheet(_pkg()))
        self.assertFalse(verdict.blocked)
        self.assertEqual(_gate_logs(led), [])

    def test_all_memberless_candidates_blocked(self):
        """候选假设全部无成员(悬空):门3 拦——退化问询过滤(a)。

        注意:一有一无的候选不算退化(选有成员方确实能翻出主导,门3 放行);
        只有双候选都无成员时,"怎么选都不变"才成立。
        """
        led = make_ledger()
        bp = AnalysisBlueprint(
            claims=[
                ClaimDraft(id="C1", text="B公司拖欠工资", category=ClaimCategory.FACT,
                           section_index=2, entity_names=["B公司"]),
                ClaimDraft(id="C2", text="B公司正常发薪", category=ClaimCategory.FACT,
                           section_index=2, entity_names=["B公司"]),
            ],
            hypotheses=[
                HypothesisSeed(id="H0", label="暴雷", claim_ids=[]),
                HypothesisSeed(id="H1", label="正常经营", claim_ids=[]),
            ],
            gaps=[])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases=ALIASES)
        verdict = AskGate(led, threshold=2).check(EID, 2, _sheet(_pkg()))
        self.assertTrue(verdict.blocked)
        logs = _gate_logs(led)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["gate"], "门3_裁决增益")
        self.assertIn("都不改变主导集", logs[0]["reason"])

    def test_candidates_excluding_true_dominant_blocked(self):
        """候选不含真正的竞争主导方(台外第三假设压着):门3 拦——过滤(b)。"""
        led = _ledger(k_verified=0, h1_members=1)
        # 台外假设 H2 碾压(成员 C2 全部已证实 → 记分 5,候选怎么翻都动摇不了)
        bp_claims = [ClaimDraft(id=f"C{20 + i}", text=f"锚定事实{i}",
                                category=ClaimCategory.FACT, section_index=2)
                     for i in range(5)]
        led.add_hypothesis_claim = led.add_hypothesis_claim  # noqa: PLW0127 占位无操作
        led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round) VALUES(?,?,?,?,?)",
            (f"{EID}:H2", EID, "台外碾压", "", 0))
        for i, draft in enumerate(bp_claims):
            led.add_claim(EID, {"id": draft.id, "text": draft.text,
                                "category": "事实", "section_index": 2}, 0)
            led.add_hypothesis_claim(EID, "H2", draft.id)
            led.update_claim_status(f"{EID}:{draft.id}", "已证实", None, 1)
        verdict = AskGate(led, threshold=2).check(EID, 2, _sheet(_pkg()))
        self.assertTrue(verdict.blocked)
        self.assertEqual(_gate_logs(led)[0]["gate"], "门3_裁决增益")


class TestThetaCalibrationMatrix(unittest.TestCase):
    """机械定标:拦 ⟺ gap≥θ(门1 按规范);已裁决分叉恒拦(θ 无关)。"""

    def test_blocked_iff_gap_ge_theta(self):
        for k in range(4):                                   # gap = k(H1=0 分)
            for theta in (1, 2, 3, 4):
                led = _ledger(k_verified=k)
                verdict = AskGate(led, threshold=theta).check(
                    EID, 2, _sheet(_pkg()))
                expect_blocked = k >= theta
                self.assertEqual(verdict.blocked, expect_blocked,
                                 f"gap={k} θ={theta}")
                led.conn.close()

    def test_adjudicated_fork_blocked_at_every_theta(self):
        for theta in (1, 2, 3, 4):
            led = _ledger(k_verified=0)
            Adjudicator(led).apply(EID, 1, _pkg(), "甲")
            verdict = AskGate(led, threshold=theta).check(EID, 2, _sheet(_pkg()))
            self.assertTrue(verdict.blocked, f"θ={theta}")
            self.assertIn("已被人工裁决", verdict.reason)
            led.conn.close()


if __name__ == "__main__":
    unittest.main()
