"""S1b 粒度指令位单元:三档 rubric / 反漂白基座 / variant 全链(分析器+CLI)。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act import prompts  # noqa: E402
from sense2act.stages import s1_signal  # noqa: E402
from sense2act.stages.s1_signal import LLMSignalAnalyzer  # noqa: E402
from helpers import rumor_envelope  # noqa: E402


class _FakeQual:
    """鸭子类型替身:analyze_deep 只对它调 model_dump(by_alias=True)。"""

    def model_dump(self, by_alias: bool = True) -> dict:
        return {"signalType": "竞情", "reason": "测试", "selectedSeedLabels": []}


class TestS1BPromptVariants(unittest.TestCase):

    def test_middle_is_base_unchanged(self):
        """中档 = 基座原样(回放基线档,rubric 为空串)。"""
        self.assertEqual(prompts.S1B_VARIANT_RUBRIC["中"], "")
        self.assertEqual(prompts.s1b_prompt("中"), prompts.S1B_PROMPT)

    def test_coarse_and_fine_extend_base(self):
        for v, marker in (("粗", "粗档"), ("细", "细档")):
            p = prompts.s1b_prompt(v)
            self.assertTrue(p.startswith(prompts.S1B_PROMPT))
            self.assertIn(marker, p)
            self.assertGreater(len(p), len(prompts.S1B_PROMPT))

    def test_invalid_variant_raises(self):
        with self.assertRaises(ValueError):
            prompts.s1b_prompt("x")

    def test_anti_bleach_in_base_applies_to_all_variants(self):
        """反漂白 rubric(10-01 启发④)在基座:三档都带非平凡谓词纪律。"""
        self.assertIn("非平凡谓词", prompts.S1B_PROMPT)
        for v in ("粗", "中", "细"):
            self.assertIn("非平凡谓词", prompts.s1b_prompt(v))


class TestAnalyzerWiring(unittest.TestCase):

    @staticmethod
    def _capture(variant: str) -> tuple[object, str]:
        captured: dict = {}

        def fake_call(llm, stage, system, user, model_cls, retry):
            captured["stage"] = stage
            captured["system"] = system
            return "BLUEPRINT_SENTINEL"

        orig = s1_signal.call_structured
        s1_signal.call_structured = fake_call
        try:
            analyzer = LLMSignalAnalyzer(llm=None, variant=variant)
            out = analyzer.analyze_deep(rumor_envelope(), _FakeQual(), [])
        finally:
            s1_signal.call_structured = orig
        return out, captured

    def test_analyze_deep_uses_variant_system_prompt(self):
        out, cap = self._capture("细")
        self.assertEqual(out, "BLUEPRINT_SENTINEL")
        self.assertEqual(cap["stage"], "s1b")
        self.assertTrue(cap["system"].startswith(prompts.S1B_PROMPT))
        self.assertIn("细档", cap["system"])

    def test_analyzer_default_variant_is_middle(self):
        _, cap = self._capture("中")
        self.assertEqual(cap["system"], prompts.S1B_PROMPT)

    def test_analyzer_invalid_variant_raises_at_construction(self):
        """未知档位构造即报错,不延迟到首轮 LLM 调用。"""
        with self.assertRaises(ValueError):
            LLMSignalAnalyzer(llm=None, variant="x")


class TestInvestigateVariantFlag(unittest.TestCase):
    """investigate --variant 通道:argparse 接受三档,非法值 exit 2。"""

    @staticmethod
    def _run_cli(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate", *extra],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)

    def test_variant_flag_accepted_offline(self):
        """--variant 粗 被解析并建流水线:空回放在 s1a fail-fast(全链通)。"""
        with tempfile.TemporaryDirectory() as tmp:
            replay = Path(tmp) / "empty.json"
            replay.write_text("[]", encoding="utf-8")
            r = self._run_cli("--envelope", "examples/tender_medical_ai/envelope.json",
                              "--replay", str(replay), "--variant", "粗",
                              "--db", str(Path(tmp) / "t.db"))
            self.assertIn("回放响应耗尽(stage=s1a)", r.stderr)

    def test_variant_flag_invalid_rejected(self):
        r = self._run_cli("--envelope", "examples/tender_medical_ai/envelope.json",
                          "--variant", "x", "--replay", "nonexistent.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--variant", r.stderr)


if __name__ == "__main__":
    unittest.main()
