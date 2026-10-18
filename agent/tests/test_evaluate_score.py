"""评测层单元:三分类极性表 / 加权一致率精确值 / 台账取数计数 / 预算检查。

打分公式:A_w = 1 - (W_漏*n_漏 + W_过*n_过 + W_反*n_反) / (N * max(W))。
取数计数对真实 schema 验证(经 store 建库,评测器只读副本)。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import install_simple  # noqa: E402
from sense2act import evaluate as ev  # noqa: E402
from sense2act.store import LedgerRepository, connect  # noqa: E402

GOLDEN_DOC = {
    "case_id": "t", "event_id": "evt_t", "verified": True, "source": "unit",
    "claims": [
        {"draft_id": "G1", "text": "甲公司发布了新型芯片", "golden_status": "已证实"},
        {"draft_id": "G2", "text": "乙公司在海外开源了内置大模型",
         "golden_status": "矛盾"},
        {"draft_id": "G3", "text": "丙院三期采购金额达历史中位数四倍",
         "golden_status": "待证实"},
        {"draft_id": "G4", "text": "丁账号与戊集团存在协同关系",
         "golden_status": "已证实"},
    ],
}


def sys_claim(did: str, text: str, status: str, unreachable: int = 0) -> dict:
    return {"draft_id": did, "text": text, "category": "事实",
            "status": status, "unreachable": unreachable}


def matched_system() -> list[dict]:
    """构造系统侧:G1 一致 / G3 过度 / G4 反转 / G2 无对应(unmatched)/ c9 额外。"""
    return [
        sys_claim("G1", "甲公司发布了新型芯片", "已证实"),
        sys_claim("G3", "丙院三期采购金额达历史中位数的四倍", "已证实"),
        sys_claim("G4", "丁账号与戊集团存在协同关系", "矛盾"),
        sys_claim("c9", "一条完全不同的派生断言关于己公司", "待证实"),
    ]


def synth_outputs(claims: list[dict], llm_calls: int = 5,
                  stop: str = "轮数上限") -> dict:
    return {"claims": claims, "s6_opinions": 3, "ballot_fail": 1,
            "overridden": 0, "transitions": {"矛盾翻转": 1},
            "result": {"costs": {"llm_calls": llm_calls, "collect_calls": 1},
                       "stop_reason": stop}}


def score(**kw) -> dict:
    params = dict(
        case={"case_id": "t"}, golden_doc=GOLDEN_DOC,
        outputs=synth_outputs(matched_system()),
        weights=ev.Weights(miss=1, over=3, flip=2),
        budget=ev.Budget(s6_opinion_max=10, llm_calls_max=50),
        align_cfg=ev.AlignConfig(), attempted_s6=4,
        run_meta={"run_status": "ok", "variant": "中"})
    params.update(kw)
    return ev.score_case(**params)


class TestClassifyPolarity(unittest.TestCase):
    """三分类极性表:覆盖 3x3 全组合 + 未对齐。"""

    def test_correct_pair(self):
        self.assertEqual(ev.classify("矛盾", "矛盾"), ev.CONSISTENT)
        self.assertEqual(ev.classify("已证实", "已证实"), ev.CONSISTENT)
        self.assertEqual(ev.classify("待证实", "待证实"), ev.CONSISTENT)

    def test_miss(self):
        """金标终态 / 系统待证实 = 漏报;未对齐同漏报。"""
        self.assertEqual(ev.classify("已证实", "待证实"), ev.MISS)
        self.assertEqual(ev.classify("矛盾", "待证实"), ev.MISS)
        self.assertEqual(ev.classify("已证实", None), ev.MISS)

    def test_overassert(self):
        """金标待证实 / 系统终态 = 过度断言(两个方向都算)。"""
        self.assertEqual(ev.classify("待证实", "已证实"), ev.OVER)
        self.assertEqual(ev.classify("待证实", "矛盾"), ev.OVER)

    def test_flip_both_directions(self):
        self.assertEqual(ev.classify("已证实", "矛盾"), ev.FLIP)
        self.assertEqual(ev.classify("矛盾", "已证实"), ev.FLIP)

    def test_full_matrix_exhaustive(self):
        states = ["已证实", "矛盾", "待证实"]
        for gp in states:
            self.assertEqual(ev.classify(gp, None), ev.MISS)
            for sp in states:
                want = ev.classify(gp, sp)     # 与单点断言一致性由上四例锚定
                self.assertIn(want, (ev.CONSISTENT, ev.MISS, ev.OVER, ev.FLIP))
                if gp == sp:
                    self.assertEqual(want, ev.CONSISTENT)


class TestScoring(unittest.TestCase):

    def test_unmatched_golden_scored_as_miss(self):
        """金标有、系统无(且文本不相似)-> 计漏报并出现在逐条明细。"""
        row = score()
        self.assertEqual(row["miss"], 1)
        g2 = [c for c in row["claims"] if c["draft_id"] == "G2"][0]
        self.assertIsNone(g2["system_status"])
        self.assertEqual(g2["verdict"], ev.MISS)
        self.assertEqual(row["n_system_extra"], 1)
        self.assertEqual(row["system_extra_ids"], ["c9"])

    def test_weighted_agreement_exact_value(self):
        """自定义权重 (1,3,2):一致/漏报/过度/反转各一条 => A_w = 1 - 6/12 = 0.5。"""
        row = score()
        self.assertEqual(row["correct"], 1)
        self.assertEqual((row["miss"], row["over"], row["flip"]), (1, 1, 1))
        self.assertEqual(row["weighted_error"], 6)
        self.assertEqual(row["weighted_agreement"], 0.5)

    def test_budget_check(self):
        """超预算 -> budget_ok=False;未知 llm_calls 保守判 False;正常 -> True。"""
        row_over = score(outputs=synth_outputs(matched_system(), llm_calls=100))
        self.assertFalse(row_over["budget_ok"])
        no_cost = synth_outputs(matched_system())
        no_cost["result"] = {}
        self.assertFalse(score(outputs=no_cost)["budget_ok"])
        self.assertTrue(score()["budget_ok"])

    def test_run_status_and_variant_recorded(self):
        row = score(run_meta={"run_status": "budget_stop", "variant": "中",
                              "note": "预算护栏"})
        self.assertEqual(row["run_status"], "budget_stop")
        self.assertEqual(row["variant"], "中")
        self.assertEqual(row["s6_attempted"], 4)
        self.assertEqual(row["flips_total"], 1)


class TestReadOutputs(unittest.TestCase):
    """取数计数对真实 schema 验证(store 建库;评测器读副本,原库不动)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.conn = connect(self.db)
        cfg = Path(__file__).resolve().parent.parent / "data" / "config"
        self.led = LedgerRepository(self.conn, cfg)
        install_simple(self.led, "evt_t")

    def tearDown(self):
        self.conn.close()

    def test_s6_and_ballot_counts(self):
        """S6 意见数 / 验票失败数从 decision_log 确定性计数。"""
        self.led.log("evt_t", 1, "意见", {
            "claim_id": "evt_t:C1", "verdict": "已证实", "judgments": []})
        self.led.log("evt_t", 1, "判决", {
            "claim_id": "evt_t:C1", "final": "待证实", "skipped": True,
            "overridden": False, "note": "验票失败维持原态: quote 不连续"})
        out = ev.read_case_outputs(
            self.db, os.path.join(self.tmp, "absent.json"), "evt_t")
        self.assertEqual([c["draft_id"] for c in out["claims"]],
                         ["C1", "C2", "C3"])
        self.assertEqual(out["s6_opinions"], 1)
        self.assertEqual(out["ballot_fail"], 1)
        self.assertEqual(out["overridden"], 0)
        self.assertIsNone(out["result"])

    def test_flips_cause_breakdown(self):
        """翻转按三成因分组;flips_total 只数三种翻转(澄清不计)。"""
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)          # 澄清
        self.led.update_claim_status("evt_t:C2", "矛盾", "矛盾翻转", 2)
        self.led.update_claim_status("evt_t:C3", "矛盾", "时效翻转", 3)
        out = ev.read_case_outputs(
            self.db, os.path.join(self.tmp, "absent.json"), "evt_t")
        self.assertEqual(out["transitions"].get("澄清"), 1)
        self.assertEqual(out["transitions"].get("矛盾翻转"), 1)
        self.assertEqual(out["transitions"].get("时效翻转"), 1)
        merged = synth_outputs(matched_system())
        merged["transitions"] = out["transitions"]
        row = score(outputs=merged)
        self.assertEqual(row["flips_total"], 2)


class TestFailureClassification(unittest.TestCase):

    def test_classify_failure_by_stderr_marker(self):
        """失败归因靠 ReplayLLM 的显式报错文案,不静默。"""
        self.assertEqual(
            ev.classify_failure("RuntimeError: 回放错位: 期望 stage=s2, 下一条是 s5"),
            "replay_mismatch")
        self.assertEqual(
            ev.classify_failure("RuntimeError: 回放响应耗尽(stage=s6_opinion)"),
            "replay_exhausted")
        self.assertEqual(ev.classify_failure("KeyError: 'x'"), "error")


if __name__ == "__main__":
    unittest.main()
