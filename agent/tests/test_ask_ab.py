"""AskGate on/off A/B 消融(P3):重复问询场景 + 置换检验。

场景:模糊分叉(金标=H1)+ 同题二问,regret 人格(首答甲=对,再答乙=翻悔):
  gate on 臂:二问被"已裁决不再问"拦 → 正确裁决保持,IR=1;
  gate off 臂:二问放行 → latest-wins,翻悔答案推翻正确裁决,IR=2。
读数纪律:regret 确定性人格零方差 → 每臂 n=8 即饱和(精确置换 p 下限
2/C(16,8)≈0.00016 < 0.001;n=5 时下限仅 0.008 不够)。与 careless i.i.d.
场景的对照结论(答案可交换时 gate 不改变正确率——它防的是"重复问询推翻
裁决",不是答案本身)写方案文档 §3.3.2。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.ask_metrics import extract_from_db, permutation_test  # noqa: E402
from test_ask_pipeline import (  # noqa: E402
    EID,
    _ledger,
    _run,
    _s1a,
    _s1b,
    _s2_ask,
    _seed,
    _write,
)

N_PER_ARM = 8
GOLDEN = "H1"


class TestAskGateAblation(unittest.TestCase):

    def _cell(self, tmp: str, gate: str) -> dict:
        rec = _write(tmp, "rec.json", [
            _s1a(), _s1b(), _s2_ask(1), _s2_ask(2)])
        r = _run(tmp, rec, _seed(tmp),
                 ["--max-rounds", "2", "--ask-mode", "simulated",
                  "--sim-persona", "regret", "--ask-gate", gate])
        assert r["returncode"] == 0, r["stderr"][-300:]
        m = extract_from_db(str(r["db"]), EID)
        led = _ledger(r["db"])
        try:
            dominant = {s["hypothesis_id"].rsplit(":", 1)[-1]
                        for s in led.hypothesis_standings(EID)
                        if s.get("dominant")}
        finally:
            led.conn.close()
        return {"correct": dominant == {GOLDEN}, "ir": m["ir"]}

    @classmethod
    def setUpClass(cls):
        cls.cells = {"on": [], "off": []}
        for gate in ("on", "off"):
            for _ in range(N_PER_ARM):
                with tempfile.TemporaryDirectory() as tmp:
                    cls.cells[gate].append(cls._cell(cls, tmp, gate))

    def test_gate_on_preserves_correct_adjudication(self):
        self.assertTrue(all(c["correct"] for c in self.cells["on"]))
        self.assertEqual({c["ir"] for c in self.cells["on"]}, {1})

    def test_gate_off_lets_regret_overturn(self):
        self.assertFalse(any(c["correct"] for c in self.cells["off"]))
        self.assertEqual({c["ir"] for c in self.cells["off"]}, {2})

    def test_permutation_test_separates_arms(self):
        on = [1.0 if c["correct"] else 0.0 for c in self.cells["on"]]
        off = [1.0 if c["correct"] else 0.0 for c in self.cells["off"]]
        r = permutation_test(on, off, n_perm=5000, seed=20261010)
        self.assertEqual(r["diff"], 1.0)
        self.assertLessEqual(r["p_two_sided"], 0.001)


if __name__ == "__main__":
    unittest.main()
