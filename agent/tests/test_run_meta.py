"""运行参数旁车文件(.meta.json)单元:录制/回放参数配对契约(P0 防再犯)。

背景(2026-10-05 失真排查):live 用 entity_text 录制、回放走默认 entity 档,
挂账集不同导致判决级回放静默失真。meta 契约=录制写参数、回放硬校验/
评测自动对齐。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.run_meta import (align_from_meta, meta_path, mismatches,  # noqa: E402
                                read_run_meta, write_run_meta)


def args(**kw) -> argparse.Namespace:
    base = dict(variant="中", assoc="entity", max_rounds=6,
                max_llm_calls=None, max_collect_calls=None,
                disc="on", no_interleave=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestRunMeta(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Path(self.tmp.name) / "rec.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_read_roundtrip(self):
        """写读往返:参数快照+时间戳落盘,variant 归一。"""
        write_run_meta(self.rec, args(variant="", assoc="entity_text"))
        meta = read_run_meta(self.rec)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["variant"], "中")        # 空串归一为中
        self.assertEqual(meta["assoc"], "entity_text")
        self.assertEqual(meta["max_rounds"], 6)
        self.assertTrue(meta["interleave"])
        self.assertIn("recorded_at", meta)
        self.assertEqual(meta_path(self.rec), self.rec.parent / "rec.json.meta.json")

    def test_read_missing_returns_none(self):
        """旧录制件无 meta → None(放行兼容存量)。"""
        self.assertIsNone(read_run_meta(self.rec))

    def test_mismatches_detects_assoc(self):
        """assoc 档位不一致被发现(主犯参数)。"""
        write_run_meta(self.rec, args(assoc="entity_text"))
        bad = mismatches(read_run_meta(self.rec), args(assoc="entity"))
        self.assertEqual(bad, {"assoc": ("entity_text", "entity")})

    def test_mismatches_none_keys_skipped(self):
        """meta 里未记录的键(None)跳过,不误报。"""
        meta = {"assoc": "entity_text", "variant": "中"}   # 其余键未记录
        self.assertEqual(mismatches(meta, args(assoc="entity_text")), {})

    def test_mismatches_all_equal_empty(self):
        write_run_meta(self.rec, args())
        self.assertEqual(mismatches(read_run_meta(self.rec), args()), {})

    def test_align_from_meta_fixes_and_reports(self):
        """评测对齐:case.run_args 被纠正为 meta 值,返回留痕文案。"""
        meta = {"assoc": "entity_text", "max_rounds": 6}
        case = {"run_args": {"assoc": "entity", "max_rounds": 6}}
        note = align_from_meta(case, meta)
        self.assertEqual(case["run_args"]["assoc"], "entity_text")
        self.assertIn("assoc:entity->entity_text", note)
        self.assertIn("meta", note)

    def test_align_from_meta_noop(self):
        """已配对 → 空文案(不留痕)。"""
        meta = {"assoc": "entity_text"}
        case = {"run_args": {"assoc": "entity_text"}}
        self.assertEqual(align_from_meta(case, meta), "")

    def test_replay_with_conflicting_meta_hard_fails(self):
        """集成:--replay 参数与 meta 冲突 → 退出码 2,不带病回放。"""
        responses = ROOT / "examples" / "supplier_b" / "responses.json"
        guard = meta_path(responses)
        self.assertFalse(guard.exists(), "存量录制件不应已有 meta(测试前置)")
        try:
            write_run_meta(responses, args(assoc="entity_text"))
            proc = subprocess.run(
                [sys.executable, "-m", "sense2act", "investigate",
                 "--envelope", str(ROOT / "examples/supplier_b/envelope.json"),
                 "--replay", str(responses),
                 "--seed", str(ROOT / "examples/supplier_b/evidence_seed.json"),
                 "--event-id", "evt_meta_probe", "--db",
                 str(Path(self.tmp.name) / "p.db")],
                cwd=str(ROOT), capture_output=True, text=True,
                encoding="utf-8", timeout=120)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("参数配对", proc.stderr)
        finally:
            guard.unlink(missing_ok=True)   # 还原存量状态


class TestAskMetaKeys(unittest.TestCase):
    """P1-8:ask_mode/ask_gate 入参数配对;ask_answers=数据附载不入配对校验。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rec = Path(self.tmp.name) / "rec.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ask_gate_normalized_bool(self):
        """ask_gate on/off 归一为 bool 再比(与 disc/calibration 同款)。"""
        write_run_meta(self.rec, args(ask_gate="off", ask_mode="interactive"))
        meta = read_run_meta(self.rec)
        self.assertFalse(meta["ask_gate"])
        self.assertEqual(meta["ask_mode"], "interactive")
        bad = mismatches(meta, args(ask_gate="on", ask_mode="interactive"))
        self.assertEqual(bad, {"ask_gate": (False, True)})

    def test_legacy_meta_without_ask_keys_passes(self):
        """旧 meta 无 ask_* 键 → None 跳过,不误报(兼容存量)。"""
        meta = {"assoc": "entity", "variant": "中"}
        self.assertEqual(mismatches(meta, args()), {})

    def test_extra_ask_answers_roundtrip_not_in_mismatches(self):
        """ask_answers 落同一 meta json 但 mismatches 永不消费(数据非参数)。"""
        write_run_meta(self.rec, args(ask_mode="interactive"),
                       extra={"ask_answers": [
                           {"round": 1, "question": "q", "answer": "甲"}]})
        meta = read_run_meta(self.rec)
        self.assertEqual(meta["ask_answers"][0]["answer"], "甲")
        self.assertEqual(mismatches(meta, args(ask_mode="interactive")), {})

    def test_align_from_meta_fixes_ask_mode(self):
        """评测对齐:case.run_args 的 ask_mode 被 meta 纠正并留痕。"""
        meta = {"ask_mode": "interactive", "ask_gate": False}
        case = {"run_args": {"ask_mode": "headless"}}
        note = align_from_meta(case, meta)
        self.assertEqual(case["run_args"]["ask_mode"], "interactive")
        self.assertEqual(case["run_args"]["ask_gate"], False)
        self.assertIn("ask_mode", note)


if __name__ == "__main__":
    unittest.main()
