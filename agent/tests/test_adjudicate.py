"""主动澄清 P0/P1:问题包裁决目标 schema / 裁决器 / 审计落账。

模型组(本文件第 1 节)只测结构校验;裁决器组测台账写行为(见第 2 节起)。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import ValidationError  # noqa: E402

from helpers import ALIASES, install_simple, make_ledger, rumor_envelope  # noqa: E402

from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.decision import (  # noqa: E402
    AdjudicationMapping,
    AdjudicationTarget,
    QuestionPackage,
)
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    HypothesisSeed,
)
from sense2act.stages.adjudicate import Adjudicator  # noqa: E402

EID = "evt_adj"


def _target(kind: str = "hypothesis", **overrides) -> AdjudicationTarget:
    base = dict(kind=kind, map=[
        AdjudicationMapping(option="甲", target_id="H1"),
        AdjudicationMapping(option="乙", target_id="H0"),
    ])
    base.update(overrides)
    return AdjudicationTarget(**base)


def _package(**kw) -> QuestionPackage:
    base = dict(question="按哪个口径继续?",
                options=["甲", "乙"], evidence_summary="两条线索矛盾")
    base.update(kw)
    return QuestionPackage(**base)


class TestAdjudicationTargetSchema(unittest.TestCase):
    """模型组:结构校验分层(结构在模型层,目标存在性在应用时)。"""

    def test_legacy_package_without_target_still_parses(self):
        """旧录制件(无 adjudicationTarget)照常解析——向后兼容锁。"""
        pkg = _package()
        self.assertIsNone(pkg.adjudication_target)

    def test_camel_case_alias_accepted(self):
        """LLM 侧 JSON 字段为 camelCase(adjudicationTarget)。"""
        pkg = QuestionPackage.model_validate({
            "question": "q", "options": ["甲", "乙"], "evidenceSummary": "s",
            "adjudicationTarget": {
                "kind": "hypothesis",
                "map": [{"option": "甲", "targetId": "H1"},
                        {"option": "乙", "targetId": "H0"}],
            },
        })
        assert pkg.adjudication_target is not None
        self.assertEqual(pkg.adjudication_target.kind, "hypothesis")
        self.assertEqual(pkg.adjudication_target.map[0].target_id, "H1")

    def test_map_must_cover_options_exactly(self):
        with self.assertRaises(ValidationError):
            _package(adjudication_target=_target(map=[
                AdjudicationMapping(option="甲", target_id="H1")]))
        with self.assertRaises(ValidationError):
            _package(adjudication_target=_target(map=[
                AdjudicationMapping(option="甲", target_id="H1"),
                AdjudicationMapping(option="丙", target_id="H0")]))

    def test_hypothesis_targets_must_differ(self):
        with self.assertRaises(ValidationError):
            _package(adjudication_target=_target(map=[
                AdjudicationMapping(option="甲", target_id="H1"),
                AdjudicationMapping(option="乙", target_id="H1")]))

    def test_claim_kind_may_repeat_target(self):
        """claim/evidence 类裁决允许多选项指向同一目标(如两个口径选项收窄同一条)。"""
        pkg = _package(adjudication_target=_target(kind="claim", map=[
            AdjudicationMapping(option="甲", target_id="C1"),
            AdjudicationMapping(option="乙", target_id="C1")]))
        self.assertEqual(pkg.adjudication_target.kind, "claim")

    def test_kind_is_restricted(self):
        with self.assertRaises(ValidationError):
            _target(kind="权重")


def _two_hypothesis_ledger():
    """H0(暴雷,成员 C1)/ H1(正常经营,成员 C2)的真分叉台账。"""
    led = make_ledger()
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
    return led


class TestAdjudicatorHypothesis(unittest.TestCase):
    def setUp(self):
        self.led = _two_hypothesis_ledger()
        self.adj = Adjudicator(self.led)
        self.pkg = _package(adjudication_target=_target())

    def test_apply_records_selected_and_rejected(self):
        result = self.adj.apply(EID, 2, self.pkg, "甲")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_claim_ids, [])   # 分权:只钉 standings
        rows = self.led.conn.execute(
            "SELECT * FROM adjudications ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        by_target = {r["target_id"]: r["selected"] for r in rows}
        self.assertEqual(by_target[f"{EID}:H1"], 1)
        self.assertEqual(by_target[f"{EID}:H0"], 0)
        # claims 现态零变化
        for cid in ("C1", "C2"):
            self.assertEqual(
                self.led.claim_row(f"{EID}:{cid}")["status"], "待证实")

    def test_audit_row_logged(self):
        self.adj.apply(EID, 2, self.pkg, "乙")
        rows = self.led.decision_log_rows(EID)
        audits = [r for r in rows if r["kind"] == "澄清"
                  and "adjudication" in r["payload_json"]]
        self.assertEqual(len(audits), 1)
        self.assertIn("human_adjudication", audits[0]["payload_json"])

    def test_unknown_target_blocked_without_partial_write(self):
        pkg = _package(adjudication_target=_target(map=[
            AdjudicationMapping(option="甲", target_id="H1"),
            AdjudicationMapping(option="乙", target_id="H99")]))
        result = self.adj.apply(EID, 2, pkg, "乙")
        self.assertFalse(result.ok)
        self.assertIn("H99", result.blocked_reason)
        self.assertEqual(self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM adjudications").fetchone()["n"], 0)
        self.assertIn("adjudication_blocked", self.led.decision_log_rows(EID)[-1]["payload_json"])

    def test_answer_outside_mapping_blocked(self):
        result = self.adj.apply(EID, 2, self.pkg, "丙")
        self.assertFalse(result.ok)
        self.assertEqual(self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM adjudications").fetchone()["n"], 0)


class TestAdjudicatorClaim(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led, EID)
        self.adj = Adjudicator(self.led)
        self.pkg = _package(adjudication_target=_target(kind="claim", map=[
            AdjudicationMapping(option="甲", target_id="C1"),
            AdjudicationMapping(option="乙", target_id="C1")]))

    def test_restate_creates_derived_claim(self):
        result = self.adj.apply(EID, 2, self.pkg, "甲")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_claim_ids, [f"{EID}:C1R"])
        new = self.led.claim_row(f"{EID}:C1R")
        orig = self.led.claim_row(f"{EID}:C1")
        self.assertIn("(口径:甲)", new["text"])
        self.assertEqual(new["parent_claim_id"], orig["claim_id"])
        self.assertEqual(new["section_id"], orig["section_id"])
        self.assertEqual(new["category"], orig["category"])
        self.assertEqual(new["status"], "待证实")
        # 原条纹丝不动
        self.assertEqual(orig["text"], "B公司拖欠工资")
        self.assertEqual(orig["status"], "待证实")
        # 实体链接复制(B公司)
        links = self.led.conn.execute(
            "SELECT entity_id FROM claim_entities WHERE claim_id=?",
            (f"{EID}:C1R",)).fetchall()
        self.assertEqual(len(links), 1)
        # 裁决行 target=新条
        row = self.led.conn.execute(
            "SELECT * FROM adjudications").fetchone()
        self.assertEqual(row["target_id"], f"{EID}:C1R")

    def test_restate_twice_id_deterministic(self):
        self.adj.apply(EID, 2, self.pkg, "甲")
        self.adj.apply(EID, 3, self.pkg, "乙")
        texts = {r["draft_id"] for r in self.led.all_claims(EID)}
        self.assertEqual(texts, {"C1", "C2", "C3", "C1R", "C1RR"})


class TestAdjudicatorEvidence(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led, EID)
        self.adj = Adjudicator(self.led)
        self.pkg = _package(adjudication_target=_target(kind="evidence", map=[
            AdjudicationMapping(option="甲", target_id="C1"),
            AdjudicationMapping(option="乙", target_id="C1")]))

    def test_register_human_evidence_single_root(self):
        result = self.adj.apply(EID, 2, self.pkg, "甲")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_claim_ids, [f"{EID}:C1"])
        ev = self.led.conn.execute(
            "SELECT * FROM evidence WHERE event_id=?", (EID,)).fetchall()
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["platform"], "human")
        self.assertEqual(ev[0]["lineage_root"], "human")
        self.assertEqual(ev[0]["author_type"], "用户裁决")
        self.assertEqual(ev[0]["round"], 2)
        link = self.led.conn.execute(
            "SELECT * FROM claim_evidence WHERE claim_id=?",
            (f"{EID}:C1",)).fetchone()
        self.assertIsNotNone(link)
        self.assertEqual(link["round"], 2)

    def test_duplicate_answer_deduped(self):
        self.adj.apply(EID, 2, self.pkg, "甲")
        result = self.adj.apply(EID, 3, self.pkg, "甲")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_claim_ids, [])
        self.assertIn("重复", result.applied[0])
        self.assertEqual(self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM evidence WHERE event_id=?",
            (EID,)).fetchone()["n"], 1)


class TestAdjudicatorNoTarget(unittest.TestCase):
    def test_legacy_package_noop_without_audit(self):
        led = make_ledger()
        install_simple(led, EID)
        before = len(led.decision_log_rows(EID))
        result = Adjudicator(led).apply(EID, 2, _package(), "甲")
        self.assertTrue(result.ok)
        self.assertEqual(result.affected_claim_ids, [])
        self.assertEqual(len(led.decision_log_rows(EID)), before)  # 零审计行(旧件路径)


if __name__ == "__main__":
    unittest.main()
