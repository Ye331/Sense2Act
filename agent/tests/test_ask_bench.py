"""受控合成澄清 bench(P1-8 档C):场景族 × 人格 × 指标四件套。

⚠ 读数纪律:本 bench 产物是**机械验证数**(管道 shakeout:机制通、方向对、
指标可算),不是科学读数——科学读数属 P3(live 流量+预注册+金标按 run 派生)。

场景族(金标=受控合成:哪个假设为真由构造声明):
  A 模糊分叉(全待证实,gate 放行):问了对=翻到金标;
  B 已主导(refresh 预置 H0=2,gate 拦截):B1 主导=金标(label 0,问不问都对)
    / B2 主导≠金标(label 1 纠正型——门1 拦下纠正,暴露 gate 盲区=门3 动机);
  B2off:B2 的 --ask-gate off 对照臂(纠正应成功)。
人格:6 种 stub(机械人格零 LLM;cooperative 离线=首选项)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.ask_metrics import (  # noqa: E402
    auroc,
    extract_from_db,
    paired_gain,
)
from sense2act.stages.sim_user import PERSONAS  # noqa: E402
from test_ask_pipeline import (  # noqa: E402
    EID,
    _ledger,
    _run,
    _s1a,
    _s1b,
    _s2_ask,
    _s2_tool,
    _seed,
    _write,
)

REFRESH = {"1": [{"draft_id": "C1", "new_status": "已证实"},
                 {"draft_id": "C3", "new_status": "已证实"}]}   # H0=2 主导


def _entries(kind: str) -> list[dict]:
    if kind == "A":
        return [_s1a(), _s1b(), _s2_ask(1)]
    return [_s1a(), _s1b(), _s2_tool(1), _s2_ask(2)]           # B/B2off


def run_cell(kind: str, persona: str, gate: str = "on") -> dict:
    """跑一格,返回 {metrics, dominant(set), winner(裁决选中假设|None)}。"""
    tmp = tempfile.TemporaryDirectory()
    try:
        name = tmp.name
        rec = _write(name, "rec.json", _entries(kind))
        extra = ["--max-rounds", "2" if kind != "A" else "1",
                 "--ask-mode", "simulated", "--sim-persona", persona,
                 "--ask-gate", gate]
        if kind != "A":
            refresh = Path(name) / "refresh.json"
            refresh.write_text(json.dumps(REFRESH, ensure_ascii=False),
                               encoding="utf-8")
            extra += ["--refresh-plan", str(refresh)]
        r = _run(name, rec, _seed(name), extra)
        assert r["returncode"] == 0, f"{kind}/{persona}/gate={gate}: {r['stderr'][-300:]}"
        m = extract_from_db(str(r["db"]), EID)
        led = _ledger(r["db"])
        try:
            standings = led.hypothesis_standings(EID)
            dominant = {s["hypothesis_id"].rsplit(":", 1)[-1]
                        for s in standings if s.get("dominant")}
            selected = [row["target_id"].rsplit(":", 1)[-1]
                        for row in led.conn.execute(
                            "SELECT target_id FROM adjudications"
                            " WHERE selected=1 ORDER BY id DESC LIMIT 1")]
            winner = selected[0] if selected else None
        finally:
            led.conn.close()
        return {"metrics": m, "dominant": dominant, "winner": winner}
    finally:
        tmp.cleanup()


class TestAskBench(unittest.TestCase):
    """机械验证:全格跑通 + 方向断言 + 指标可算 + AUROC 出数。"""

    @classmethod
    def setUpClass(cls):
        cls.cells = {(kind, p): run_cell(kind, p, gate)
                     for kind, gate in (("A", "on"), ("B", "on"), ("B2off", "off"))
                     for p in PERSONAS}

    def test_all_cells_complete_with_metrics(self):
        for key, cell in self.cells.items():
            m = cell["metrics"]
            for field in ("ir", "icr", "ce", "cps", "asks", "applied"):
                self.assertIn(field, m, key)

    def test_cooperative_flips_to_golden_on_ambiguous_fork(self):
        cell = self.cells[("A", "cooperative")]
        self.assertEqual(cell["winner"], "H1")              # 首选项甲→H1
        self.assertEqual(cell["dominant"], {"H1"})
        self.assertGreater(cell["metrics"]["cps"], 0)

    def test_silence_never_adjudicates(self):
        for kind in ("A", "B2off"):
            cell = self.cells[(kind, "silence")]
            self.assertIsNone(cell["winner"], kind)
            self.assertEqual(cell["metrics"]["icr"], 0.0, kind)

    def test_gate_blocks_dominant_fork_for_every_persona(self):
        """B(已主导):门1 对全部人格拦截——问询不发生,格局不变。"""
        for p in PERSONAS:
            cell = self.cells[("B", p)]
            self.assertEqual(cell["metrics"]["asks"], 0, p)
            self.assertGreaterEqual(cell["metrics"]["askgate_blocked"], 1, p)
            self.assertEqual(cell["dominant"], {"H0"}, p)   # 既有主导保持

    def test_gate_off_allows_correction_on_wrong_dominant(self):
        """B2off(主导错误+门关):纠正型问询翻到金标——门1 盲区的对照证据。"""
        cell = self.cells[("B2off", "cooperative")]
        self.assertEqual(cell["winner"], "H1")
        self.assertEqual(cell["dominant"], {"H1"})

    def test_budget_gain_cooperative_vs_silence(self):
        """预算内增益(机械数):问对人(b=2 内)vs 问而无效。"""
        gain = paired_gain(
            [self.cells[("A", "cooperative")]["winner"] == "H1"],
            [self.cells[("A", "silence")]["winner"] == "H1"])
        self.assertEqual(gain, {"treated_rate": 1.0, "control_rate": 0.0,
                                "gain": 1.0})

    def test_gate_auroc_over_scenario_family(self):
        """AUROC(机械数):A 放行 realism=2(label 1)/B1 拦 realism=0(label 0)
        /B2 拦 realism=0(label 1)→ AUC=0.75——门1 分数把"主导错误"(B2)
        误判为不必问,正是门3(反事实增益)要补的盲区。"""
        a = self.cells[("A", "cooperative")]["metrics"]["gate_samples"]
        b = self.cells[("B", "cooperative")]["metrics"]["gate_samples"]
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        samples = [(a[0]["realism"], 1),      # A:模糊分叉,问了有用
                   (b[0]["realism"], 0),      # B1 视角:主导=金标,问了没用
                   (b[0]["realism"], 1)]      # B2 视角:主导≠金标,问了有用
        self.assertEqual(auroc(samples), 0.75)


if __name__ == "__main__":
    unittest.main()
