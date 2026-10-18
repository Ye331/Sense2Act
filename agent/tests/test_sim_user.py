"""模拟用户器(档C):6 人格单元行为 + 全人格全链 e2e。

人格族映射 = AskToAct 模拟评测用户【文献依据】;机械人格零 LLM,
cooperative 离线退化首选项(与 headless 等价)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_ledger  # noqa: E402

from sense2act.models.decision import QuestionPackage  # noqa: E402
from sense2act.stages.sim_user import (  # noqa: E402
    PERSONAS,
    SimChoice,
    SimulatedClarifier,
)
from test_ask_pipeline import (  # noqa: E402
    EID,
    _adjudication_rows,
    _clarify_modes,
    _ledger,
    _run,
    _s1a,
    _s1b,
    _s2_ask,
    _seed,
    _write,
)

PKG = QuestionPackage(
    question="按哪个口径继续?", options=["口径甲", "口径乙", "口径丙"],
    evidence_summary="证据两可")


class _FakeLLM:
    """call_structured 注入:固定返回 SimChoice 形 dict(沿 test_calibrate Fake 手法)。"""

    def __init__(self, choice: int):
        self._choice = choice

    def generate_json(self, system: str, user: str, stage: str = "") -> dict:
        return {"choice": self._choice, "note": "fake"}


def _modes_of(led) -> list[str]:
    out = []
    for r in led.decision_log_rows("evt_sim"):
        if r["kind"] == "澄清":
            out.append(json.loads(r["payload_json"])["mode"])
    return out


class TestPersonaBehaviors(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()

    def _clarifier(self, persona, **kw) -> SimulatedClarifier:
        return SimulatedClarifier(self.led, persona=persona, **kw)

    def test_unknown_persona_rejected(self):
        with self.assertRaises(ValueError):
            self._clarifier("暴君")

    def test_cooperative_offline_degrades_to_first_option(self):
        c = self._clarifier("cooperative")
        self.assertEqual(c.ask("evt_sim", 1, PKG), "口径甲")
        self.assertEqual(_modes_of(self.led), ["simulated_cooperative"])

    def test_cooperative_uses_llm_choice(self):
        c = self._clarifier("cooperative", llm=_FakeLLM(2))
        self.assertEqual(c.ask("evt_sim", 1, PKG), "口径乙")

    def test_cooperative_out_of_range_choice_falls_back(self):
        c = self._clarifier("cooperative", llm=_FakeLLM(9))
        self.assertEqual(c.ask("evt_sim", 1, PKG), "口径甲")

    def test_careless_is_seeded_deterministic(self):
        a = self._clarifier("careless", seed=7)
        b = self._clarifier("careless", seed=7)
        x = a.ask("evt_sim", 1, PKG)
        self.assertEqual(x, b.ask("evt_sim", 1, PKG))       # 同种子同答案
        self.assertIn(x, PKG.options)

    def test_irrelevant_answers_off_option(self):
        c = self._clarifier("irrelevant")
        answer = c.ask("evt_sim", 1, PKG)
        self.assertNotIn(answer, PKG.options)               # → 裁决 blocked 路径
        self.assertEqual(_modes_of(self.led), ["simulated_irrelevant"])

    def test_refuse_answers_canned(self):
        c = self._clarifier("refuse")
        answer = c.ask("evt_sim", 1, PKG)
        self.assertNotIn(answer, PKG.options)
        self.assertIn("不知道", answer)

    def test_regret_switches_on_repeat_question(self):
        c = self._clarifier("regret")
        first = c.ask("evt_sim", 1, PKG)
        second = c.ask("evt_sim", 2, PKG)                   # 同题二问
        self.assertEqual(first, "口径甲")
        self.assertEqual(second, "口径丙")                  # 反悔:换答案(latest-wins)

    def test_silence_returns_empty(self):
        c = self._clarifier("silence")
        self.assertEqual(c.ask("evt_sim", 1, PKG), "")
        self.assertEqual(_modes_of(self.led), ["simulated_silence"])

    def test_answers_collected_for_meta(self):
        c = self._clarifier("silence")
        c.ask("evt_sim", 1, PKG)
        self.assertEqual(c.answers, [{"round": 1, "question": PKG.question,
                                      "answer": ""}])


class TestPersonasEndToEnd(unittest.TestCase):
    """6 人格 × 同一 ask 场景全链:rc=0 + 逐人格落账行为(每格独立临时目录)。"""

    def _run_persona(self, persona: str, extra: list[str]) -> dict:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)   # 保活到断言结束(r["db"] 在断言时仍存在)
        name = tmp.name
        rec = _write(name, "rec.json", [_s1a(), _s1b(), _s2_ask(1)])
        return _run(name, rec, _seed(name),
                    ["--max-rounds", "1", "--ask-mode", "simulated",
                     "--sim-persona", persona, *extra])

    def test_all_six_personas_complete(self):
        for persona in PERSONAS:
            r = self._run_persona(persona, [])
            self.assertEqual(r["returncode"], 0,
                             f"{persona}: {r['stderr'][-300:]}")

    def test_cooperative_adjudicates_first_option(self):
        r = self._run_persona("cooperative", [])
        rows = _adjudication_rows(r["db"])
        selected = {row["target_id"] for row in rows if row["selected"] == 1}
        self.assertEqual(selected, {f"{EID}:H1"})           # 首选项甲→H1
        self.assertIn("simulated_cooperative", _clarify_modes(r["db"]))
        self.assertIn("human_adjudication", _clarify_modes(r["db"]))

    def test_blocked_personas_leave_audit_trail(self):
        """irrelevant/refuse/silence:答案不落裁决,但不静默消失(F2 回归锁)。"""
        for persona in ("irrelevant", "refuse", "silence"):
            r = self._run_persona(persona, [])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            self.assertEqual(_adjudication_rows(r["db"]), [], persona)
            modes = _clarify_modes(r["db"])
            self.assertIn(f"simulated_{persona}", modes, persona)
            self.assertIn("adjudication_blocked", modes, persona)

    def test_regret_rerun_overrides_fork_with_gate_off(self):
        """同题二问(门 off):latest-wins,反悔答案推翻首轮裁决。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec = _write(tmp, "rec.json", [
                _s1a(), _s1b(), _s2_ask(1), _s2_ask(2)])
            r = _run(tmp, rec, _seed(tmp),
                     ["--max-rounds", "2", "--ask-mode", "simulated",
                      "--sim-persona", "regret", "--ask-gate", "off"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            led = _ledger(r["db"])
            try:
                st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
                      for s in led.hypothesis_standings(EID)}
                # 首轮甲→H1 选中;二轮反悔乙→H0;per-hypothesis latest → 仅 H0 dominant
                self.assertEqual(st["H0"]["score"], 2)   # H0 成员 C1/C3 各 +1
                self.assertTrue(st["H0"]["dominant"])
                self.assertEqual(st["H1"]["score"], -1)
                self.assertFalse(st["H1"]["dominant"])
            finally:
                led.conn.close()


if __name__ == "__main__":
    unittest.main()
