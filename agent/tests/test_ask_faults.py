"""E1 式故障注入(P1-8 澄清链):三类故障必须全防住。

沿 test_e1_faults.py 纪律:每类故障 = 明确的行为契约 + e2e 断言(ToolFailBench
分类学映射写在各用例 docstring)。
- F1 Repeat-Fault(重复问已裁决口径)→ 门1"已裁决分叉不再问"确定性拦截,
  第二次问询不触达用户、不改裁决;
- F2 Drop-Fault(答案不落账)→ 答 Non relevant/拒答/沉默答案虽不能落裁决,
  但必落 adjudication_blocked 审计——答案不静默消失(P0 缺口的回归锁);
- F3 Skip-Fault(裁决后不复验)→ evidence 类裁决登记新证据后,S6 必须在
  同一问人轮内对该 claim 跑复验(判决行落在 ask 轮)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ask_pipeline import (  # noqa: E402
    EID,
    QUESTION,
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


def _s2_ask_evidence(round_no: int) -> dict:
    """evidence 类裁决问单(F3 用):选项映射到同一 claim,答案=登记人答证据。"""
    sheet = _s2_ask(round_no)
    sheet["response"]["questionPackage"]["adjudicationTarget"] = {
        "kind": "evidence",
        "map": [{"option": "甲", "targetId": "C1"},
                {"option": "乙", "targetId": "C1"}]}
    return sheet


def _s6_opinion_support() -> dict:
    """F3 的 s6_opinion 合成响应:对 E6(人答证据)投支持票,quote 为原文连续片段。

    信封 5 条 signal_layer 种子=E1..E5,人答证据轮 1 入账=E6(编号可预测)。
    """
    return {"stage": "s6_opinion", "response": {
        "claimId": f"{EID}:C1",
        "evidenceJudgments": [
            {"evidenceId": f"{EID}:E6", "direction": "支持",
             "quote": "用户裁决:甲", "reason": "委托方证言"}],
        "verdict": "待证实", "reason": "孤证非权威,意见仅供参考"}}


def _run_scenario(tmp: str, entries: list[dict], extra: list[str]) -> dict:
    rec = _write(tmp, "rec.json", entries)
    return _run(tmp, rec, _seed(tmp), extra)


class TestAskFaults(unittest.TestCase):

    def test_f1_repeat_ask_adjudicated_fork_blocked(self):
        """F1:同一分叉第二次问 → 门1 拦(已被人工裁决),不重复触达用户。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_scenario(tmp, [_s1a(), _s1b(), _s2_ask(1), _s2_ask(2)],
                     ["--max-rounds", "2"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            # 裁决只发生一次(r1 的两个候选行);r2 被拦不产生新裁决
            rows = _adjudication_rows(r["db"])
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["round"] for row in rows}, {1})
            modes = _clarify_modes(r["db"])
            self.assertEqual(modes.count("headless_auto"), 1)   # 用户只被打断一次
            self.assertEqual(modes.count("askgate_blocked"), 1)
            led = _ledger(r["db"])   # Windows:连接必须关闭,否则临时目录清理被锁
            try:
                log_rows = led.decision_log_rows(EID)
            finally:
                led.conn.close()
            blocked = [json.loads(x["payload_json"])
                       for x in log_rows if x["kind"] == "澄清"]
            reasons = [p.get("reason", "") for p in blocked
                       if p.get("mode") == "askgate_blocked"]
            self.assertTrue(any("已被人工裁决" in s for s in reasons))

    def test_f2_unusable_answers_never_vanish_silently(self):
        """F2:答非所问/沉默 → 无裁决行,但必落 adjudication_blocked 审计。"""
        for persona in ("irrelevant", "silence"):
            with tempfile.TemporaryDirectory() as tmp:
                r = _run_scenario(tmp, [_s1a(), _s1b(), _s2_ask(1)],
                         ["--max-rounds", "1", "--ask-mode", "simulated",
                          "--sim-persona", persona])
                self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
                self.assertEqual(_adjudication_rows(r["db"]), [], persona)
                modes = _clarify_modes(r["db"])
                self.assertIn("adjudication_blocked", modes, persona)
                self.assertIn(f"simulated_{persona}", modes, persona)

    def test_f3_evidence_adjudication_triggers_reverify_same_round(self):
        """F3:证据登记 → 同一问人轮内出现判决行(S6 复验不被跳过)。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_scenario(tmp, [_s1a(), _s1b(), _s2_ask_evidence(1),
                           _s6_opinion_support()],
                     ["--max-rounds", "1", "--ask-mode", "simulated",
                      "--sim-persona", "cooperative"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-300:])
            led = _ledger(r["db"])
            try:
                verdicts = [x for x in led.decision_log_rows(EID)
                            if x["kind"] == "判决"]
                self.assertEqual(len(verdicts), 1)
                self.assertEqual(verdicts[0]["round"], 1)   # 判决落在 ask 轮内
                # 人答证据确实入账且挂账
                ev = led.conn.execute(
                    "SELECT platform, lineage_root FROM evidence"
                    " WHERE event_id=? AND round=1", (EID,)).fetchone()
                self.assertIsNotNone(ev)
                self.assertEqual(ev["platform"], "human")
            finally:
                led.conn.close()


if __name__ == "__main__":
    unittest.main()
