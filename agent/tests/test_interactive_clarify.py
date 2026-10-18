"""档A 交互终端:InteractiveClarifier 三档行为 + ReplayClarifier 配对纪律。"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_ledger  # noqa: E402

from sense2act.models.decision import QuestionPackage  # noqa: E402
from sense2act.stages.clarify import (  # noqa: E402
    InteractiveClarifier,
    ReplayClarifier,
)

EID = "evt_ask"


def _pkg() -> QuestionPackage:
    return QuestionPackage(
        question="按哪个口径继续?", options=["口径甲", "口径乙"],
        evidence_summary="两条线索矛盾")


def _mode_of(led) -> str:
    rows = led.decision_log_rows(EID)
    import json
    return json.loads(rows[-1]["payload_json"])["mode"]


class _TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class TestInteractiveClarifier(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()

    def test_interactive_numeric_choice(self):
        c = InteractiveClarifier(self.led, mode="interactive",
                                 in_stream=io.StringIO("2\n"),
                                 out_stream=io.StringIO())
        answer = c.ask(EID, 2, _pkg())
        self.assertEqual(answer, "口径乙")
        self.assertEqual(_mode_of(self.led), "interactive")
        self.assertEqual(c.answers, [{"round": 2, "question": "按哪个口径继续?",
                                      "answer": "口径乙"}])

    def test_invalid_then_valid_converges(self):
        c = InteractiveClarifier(self.led, mode="interactive",
                                 in_stream=io.StringIO("abc\n9\n2\n"),
                                 out_stream=io.StringIO())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径乙")

    def test_exact_text_also_accepted(self):
        c = InteractiveClarifier(self.led, mode="interactive",
                                 in_stream=io.StringIO("口径乙\n"),
                                 out_stream=io.StringIO())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径乙")

    def test_three_invalid_inputs_fall_back_deterministically(self):
        c = InteractiveClarifier(self.led, mode="interactive",
                                 in_stream=io.StringIO("x\ny\nz\n"),
                                 out_stream=io.StringIO())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径甲")
        self.assertEqual(_mode_of(self.led), "interactive_eof_fallback")

    def test_eof_falls_back(self):
        c = InteractiveClarifier(self.led, mode="interactive",
                                 in_stream=io.StringIO(""),
                                 out_stream=io.StringIO())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径甲")
        self.assertEqual(_mode_of(self.led), "interactive_eof_fallback")

    def test_mixed_non_tty_falls_back_to_headless(self):
        """StringIO isatty()=False(子进程/CI)→ mixed 自动回退首选项。"""
        c = InteractiveClarifier(self.led, mode="mixed",
                                 in_stream=io.StringIO("2\n"),
                                 out_stream=io.StringIO())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径甲")
        self.assertEqual(_mode_of(self.led), "mixed_fallback")

    def test_mixed_tty_is_interactive(self):
        c = InteractiveClarifier(self.led, mode="mixed",
                                 in_stream=_TTY("2\n"), out_stream=_TTY())
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径乙")
        self.assertEqual(_mode_of(self.led), "interactive")


class TestReplayClarifier(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()

    def test_answers_replayed_in_order(self):
        c = ReplayClarifier(self.led, [
            {"round": 1, "question": "按哪个口径继续?", "answer": "口径乙"}])
        self.assertEqual(c.ask(EID, 1, _pkg()), "口径乙")
        self.assertEqual(_mode_of(self.led), "replay")

    def test_question_mismatch_fails_fast(self):
        c = ReplayClarifier(self.led, [
            {"round": 1, "question": "另一个问题?", "answer": "口径甲"}])
        with self.assertRaises(RuntimeError) as ctx:
            c.ask(EID, 1, _pkg())
        self.assertIn("人答回放错位", str(ctx.exception))

    def test_exhausted_fails_fast(self):
        c = ReplayClarifier(self.led, [])
        with self.assertRaises(RuntimeError) as ctx:
            c.ask(EID, 1, _pkg())
        self.assertIn("人答回放耗尽", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
