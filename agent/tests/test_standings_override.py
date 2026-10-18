"""人答裁决的 standings 覆写(P1-8):只钉记分不钉 claim;空表与旧公式逐字段等价。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ALIASES, make_ledger, rumor_envelope  # noqa: E402

from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    HypothesisSeed,
)
from sense2act.models.base import ClaimCategory  # noqa: E402

EID = "evt_ov"


def _install_two_hypotheses(led) -> None:
    """H0(暴雷,成员 C1)/ H1(正常经营,成员 C2)的真分叉台账。"""
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C2", text="B公司正常发薪", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
        ],
        hypotheses=[
            HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"]),
            HypothesisSeed(id="H1", label="正常经营", claim_ids=["C2"]),
        ],
        gaps=[])
    led.register_event(rumor_envelope(EID), "", EID)
    led.install_blueprint(EID, bp, aliases=ALIASES)


class TestStandingsOverride(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        _install_two_hypotheses(self.led)

    def test_no_adjudication_matches_legacy_formula(self):
        """无裁决:输出与旧式手算全等(全待证实→0 分;锁零扰动)。"""
        st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
              for s in self.led.hypothesis_standings(EID)}
        self.assertEqual(st["H0"]["score"], 0)
        self.assertEqual(st["H0"]["pending"], 1)
        self.assertFalse(st["H0"]["dominant"])
        self.assertFalse(st["H0"]["adjudicated"])
        self.assertFalse(st["H1"]["dominant"])

    def test_adjudication_pins_scores_without_touching_claims(self):
        """裁决 H1:选中方成员按+1、未中方按-1 记分;claims 现态零变化。"""
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H1", "正常发薪")
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H0", "拖欠工资",
                                     selected=False)
        st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
              for s in self.led.hypothesis_standings(EID)}
        self.assertEqual(st["H1"]["score"], 1)
        self.assertTrue(st["H1"]["dominant"])
        self.assertTrue(st["H1"]["adjudicated"])
        self.assertEqual(st["H0"]["score"], -1)
        self.assertFalse(st["H0"]["dominant"])
        # 分权:claims/transitions 不受人裁影响
        c1 = self.led.claim_row(f"{EID}:C1")
        c2 = self.led.claim_row(f"{EID}:C2")
        self.assertEqual(c1["status"], "待证实")
        self.assertEqual(c2["status"], "待证实")
        self.assertEqual(self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM claim_transitions WHERE claim_id=?",
            (f"{EID}:C1",)).fetchone()["n"], 0)

    def test_override_beats_claim_status(self):
        """已证实成员所在假设被否→按矛盾计(覆写压过台账现态)。"""
        self.led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
        st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
              for s in self.led.hypothesis_standings(EID)}
        self.assertEqual(st["H0"]["score"], 1)          # 未裁:台账现态
        self.assertTrue(st["H0"]["dominant"])
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H1", "正常发薪")
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H0", "拖欠工资",
                                     selected=False)
        st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
              for s in self.led.hypothesis_standings(EID)}
        self.assertEqual(st["H0"]["score"], -1)         # 裁后:覆写生效
        self.assertTrue(st["H1"]["dominant"])
        self.assertEqual(self.led.claim_row(f"{EID}:C1")["status"], "已证实")

    def test_latest_wins(self):
        """同假设两次裁决:以最新一次为准。"""
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H1", "正常发薪")
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H0", "拖欠工资",
                                     selected=False)
        self.led.record_adjudication(EID, 3, "hypothesis", f"{EID}:H0", "拖欠工资")
        self.led.record_adjudication(EID, 3, "hypothesis", f"{EID}:H1", "正常发薪",
                                     selected=False)
        st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
              for s in self.led.hypothesis_standings(EID)}
        self.assertEqual(st["H0"]["score"], 1)
        self.assertTrue(st["H0"]["dominant"])
        self.assertEqual(st["H1"]["score"], -1)

    def test_event_summary_adjudications_key_omitted_when_empty(self):
        """无裁决:summary 无 adjudications 键(旧 prompt 字节不变)。"""
        summary = self.led.event_summary(EID)
        self.assertNotIn("adjudications", summary)
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H1", "正常发薪")
        summary = self.led.event_summary(EID)
        self.assertEqual(summary["adjudications"], [
            {"round": 2, "kind": "hypothesis", "target": "H1", "choice": "正常发薪"}])

    def test_event_summary_adjudications_latest_only(self):
        """summary 只列最新裁决(winners),否决行不出现。"""
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H1", "正常发薪")
        self.led.record_adjudication(EID, 2, "hypothesis", f"{EID}:H0", "拖欠工资",
                                     selected=False)
        summary = self.led.event_summary(EID)
        self.assertEqual(len(summary["adjudications"]), 1)
        self.assertEqual(summary["adjudications"][0]["target"], "H1")


if __name__ == "__main__":
    unittest.main()
