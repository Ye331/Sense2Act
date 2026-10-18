"""指标四件套单元:IR/ICR/CE/CPS 提取、AUROC(rank AUC 带并列)、预算内增益、db 直读。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_ledger  # noqa: E402

from sense2act.ask_metrics import (  # noqa: E402
    auroc,
    extract_ask_metrics,
    extract_from_db,
    gate_realism_samples,
    paired_gain,
    permutation_test,
)


def _row(kind: str, payload: dict) -> dict:
    return {"round": 1, "kind": kind, "payload_json": json.dumps(payload)}


def _ask(mode: str = "headless_auto") -> dict:
    return _row("澄清", {"mode": mode, "question": "q", "answer": "甲"})


class TestExtractAskMetrics(unittest.TestCase):
    def test_cooperative_cell(self):
        """1 问 1 应用 / 2 轮:IR=1,ICR=1.0,CE=0.5,CPS=2·1·0.5/1.5≈0.667。"""
        rows = [_ask(), _row("澄清", {"mode": "human_adjudication"})]
        m = extract_ask_metrics(rows, total_rounds=2)
        self.assertEqual(m["ir"], 1)
        self.assertEqual(m["icr"], 1.0)
        self.assertEqual(m["ce"], 0.5)
        self.assertEqual(m["cps"], 0.6667)

    def test_silence_cell_counts_blocked(self):
        rows = [_ask("simulated_silence"),
                _row("澄清", {"mode": "adjudication_blocked"})]
        m = extract_ask_metrics(rows, total_rounds=1)
        self.assertEqual(m["asks"], 1)
        self.assertEqual(m["applied"], 0)
        self.assertEqual(m["adjudication_blocked"], 1)
        self.assertEqual(m["icr"], 0.0)
        self.assertEqual(m["cps"], 0.0)

    def test_gate_rows_are_not_asks(self):
        """askgate_passed/blocked 是审计行,不计入问人次。"""
        rows = [_row("澄清", {"mode": "askgate_passed", "realism": 1}),
                _row("澄清", {"mode": "askgate_blocked", "realism": 0}),
                _ask()]
        m = extract_ask_metrics(rows, total_rounds=3)
        self.assertEqual(m["asks"], 1)
        self.assertEqual(m["askgate_passed"], 1)
        self.assertEqual(m["askgate_blocked"], 1)

    def test_no_ask_zero_everything(self):
        m = extract_ask_metrics([_row("决策单", {"action": "查工具"})], total_rounds=5)
        self.assertEqual(m["ir"], 0)
        self.assertEqual(m["icr"], 0.0)
        self.assertEqual(m["cps"], 0.0)


class TestAuroc(unittest.TestCase):
    def test_perfect_separation(self):
        self.assertEqual(auroc([(1, 0), (2, 1), (3, 1)]), 1.0)

    def test_reversed(self):
        self.assertEqual(auroc([(3, 0), (2, 0), (1, 1)]), 0.0)

    def test_ties_take_average_rank(self):
        # pos {2, 2}, neg {2, 2} 全并列 → AUC=0.5
        self.assertEqual(auroc([(2, 1), (2, 1), (2, 0), (2, 0)]), 0.5)

    def test_empty_class_returns_none(self):
        self.assertIsNone(auroc([(1, 1), (2, 1)]))
        self.assertIsNone(auroc([]))


class TestPairedGain(unittest.TestCase):
    def test_gain_rates(self):
        g = paired_gain([True, True, False], [False, False, True, False])
        self.assertEqual(g["treated_rate"], 0.6667)   # 输出统一 round(…,4)
        self.assertEqual(g["control_rate"], 0.25)
        self.assertEqual(g["gain"], round(2 / 3 - 0.25, 4))

    def test_empty_arm_zero(self):
        g = paired_gain([], [True])
        self.assertEqual(g["treated_rate"], 0.0)
        self.assertEqual(g["gain"], -1.0)


class TestPermutation(unittest.TestCase):
    def test_perfect_separation(self):
        """gate on 20/20 对 vs gate off 20/20 错:完美分离,p 达分辨率下限。"""
        r = permutation_test([1.0] * 20, [0.0] * 20, n_perm=2000, seed=1)
        self.assertEqual(r["diff"], 1.0)
        self.assertIsNotNone(r["p_two_sided"])
        self.assertLessEqual(r["p_two_sided"], 0.001)

    def test_no_difference_gives_p_one(self):
        r = permutation_test([0.0, 1.0, 0.0, 1.0], [1.0, 0.0, 1.0, 0.0])
        self.assertEqual(r["diff"], 0.0)
        self.assertEqual(r["p_two_sided"], 1.0)

    def test_deterministic_under_seed(self):
        a = [1, 0, 1, 1, 0, 1, 0, 1] * 3
        b = [0, 0, 1, 0, 0, 1, 0, 0] * 3
        self.assertEqual(permutation_test(a, b, seed=7),
                         permutation_test(a, b, seed=7))

    def test_empty_arm_not_testable(self):
        self.assertIsNone(permutation_test([], [1.0])["p_two_sided"])


class TestExtractFromDb(unittest.TestCase):
    def test_end_to_end_on_real_ledger(self):
        led = make_ledger()
        try:
            led.log("evt_m", 1, "决策单", {"action": "问人"})
            led.log("evt_m", 1, "澄清", {"mode": "headless_auto", "answer": "甲"})
            led.log("evt_m", 1, "澄清", {"mode": "human_adjudication"})
            led.log("evt_m", 2, "澄清",
                    {"mode": "askgate_passed", "realism": 2, "gap": 0})
            m = extract_from_db(_path_of(led), "evt_m")
            self.assertEqual(m["ir"], 1)
            self.assertEqual(m["icr"], 1.0)
            self.assertEqual(m["total_rounds"], 2)
            self.assertEqual(len(m["gate_samples"]), 1)
            self.assertEqual(m["gate_samples"][0]["realism"], 2)
        finally:
            led.conn.close()


def _path_of(led) -> str:
    """make_ledger 用临时目录;这里借其 conn 拿库路径。"""
    row = led.conn.execute("PRAGMA database_list").fetchone()
    return row[2]


if __name__ == "__main__":
    unittest.main()
