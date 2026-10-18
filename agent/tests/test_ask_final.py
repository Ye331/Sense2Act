"""停前问人兜底(P3,--ask-final):死轮兜底停 × 未裁决分叉 → 停前问一次人。

三臂:开(兜底问询落账+停因追加)/默认关(旧行为逐字不变)/回放(人答 meta 接管)。
问单=确定性构造(候选假设标签做选项),零 LLM 消耗——回放天然兼容。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.run_meta import meta_path  # noqa: E402
from test_ask_pipeline import (  # noqa: E402
    EID,
    _adjudication_rows,
    _clarify_modes,
    _ledger,
    _run,
    _s1a,
    _s1b,
    _s2_tool,
    _seed,
    _write,
)

FINAL_Q = "多假设竞争且证据通道已穷尽,请委托方裁决调查口径倾向:"


def _dry_recording(tmp: str, n_rounds: int = 4) -> tuple[Path, Path]:
    """空种子零采集 → 每轮零增量 → 连续 3 轮触发死轮兜底(不放 s5 条)。"""
    entries = [_s1a(), _s1b()] + [_s2_tool(i) for i in range(1, n_rounds + 1)]
    return _write(tmp, "rec.json", entries), _seed(tmp)


class TestAskFinal(unittest.TestCase):

    def test_on_fires_final_ask_before_dry_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _dry_recording(tmp)
            r = _run(tmp, rec, seed, ["--max-rounds", "6", "--ask-final", "on"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            stop = r["result"].get("stop_reason", "")
            self.assertIn("证据通道已穷尽", stop)
            self.assertIn("代码兜底", stop)
            self.assertIn("停前已发起裁决问询", stop)
            rows = _adjudication_rows(r["db"])
            self.assertEqual(len(rows), 2)                  # 两候选各一行
            selected = {row["target_id"] for row in rows if row["selected"] == 1}
            self.assertEqual(selected, {f"{EID}:H0"})       # headless 首选项=H0 标签
            modes = _clarify_modes(r["db"])
            self.assertIn("headless_auto", modes)
            self.assertIn("human_adjudication", modes)

    def test_off_default_keeps_legacy_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _dry_recording(tmp)
            r = _run(tmp, rec, seed, ["--max-rounds", "6"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            stop = r["result"].get("stop_reason", "")
            self.assertIn("证据通道已穷尽", stop)
            self.assertNotIn("停前已发起裁决问询", stop)
            self.assertEqual(_adjudication_rows(r["db"]), [])

    def test_replay_uses_recorded_final_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _dry_recording(tmp)
            meta = {"variant": "中", "max_rounds": 6,
                    "ask_mode": "headless",
                    "ask_answers": [{"round": 3, "question": FINAL_Q,
                                     "answer": "程序合规"}]}
            meta_path(rec).write_text(json.dumps(meta, ensure_ascii=False),
                                      encoding="utf-8")
            r = _run(tmp, rec, seed, ["--max-rounds", "6", "--ask-final", "on"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            rows = _adjudication_rows(r["db"])
            selected = {row["target_id"] for row in rows if row["selected"] == 1}
            self.assertEqual(selected, {f"{EID}:H1"})       # 录制人答接管:程序合规→H1
            self.assertIn("replay", _clarify_modes(r["db"]))

    def test_no_eligible_fork_skips_asking(self):
        """单假设蓝图(无分叉)→ 不问,停因无后缀(不合格分叉不硬凑)。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _dry_recording(tmp)
            # 换单假设录制:test_dry_stop 的蓝图只有 H0——直接用其信封+自建单假设序列
            entries = [_s1a(),
                       {"stage": "s1b", "response": {
                           "claims": [
                               {"id": "C1", "text": "测试医院采购 AI 辅助诊断系统",
                                "category": "事实", "sectionIndex": 2},
                               {"id": "C2", "text": "该项目预算为 480 万元",
                                "category": "事实", "sectionIndex": 2}],
                           "hypotheses": [{"id": "H0", "label": "正常经营",
                                           "claimIds": ["C1"]}],
                           "gaps": [{"text": "需求细节"}]}}]
            entries += [_s2_tool(i) for i in range(1, 5)]
            rec2 = _write(tmp, "rec2.json", entries)
            r = _run(tmp, rec2, seed, ["--max-rounds", "6", "--ask-final", "on"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            self.assertNotIn("停前已发起裁决问询",
                             r["result"].get("stop_reason", ""))
            self.assertEqual(_adjudication_rows(r["db"]), [])


if __name__ == "__main__":
    unittest.main()
