"""端到端:医疗 AI 招标事件(多跳/扩圈/建议 + 置信贯通 + 私域历史基线)。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = "examples/tender_medical_ai"


def run_cli(extra: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sense2act", "investigate",
         "--envelope", f"{EX}/envelope.json",
         "--replay", f"{EX}/responses.json",
         "--seed", f"{EX}/evidence_seed.json",
         "--event-id", "evt_tender_medai",
         "--config-dir", "data/config"] + extra,
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


class TestTenderEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = str(Path(cls.tmp) / "e2e.db")
        cls.runs = str(Path(cls.tmp) / "runs")
        cls.proc = run_cli(["--db", cls.db, "--runs-dir", cls.runs])
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row
        cls.summary = json.loads(
            (Path(cls.runs) / "evt_tender_medai" / "result.json").read_text(
                encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_00_exit_ok(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-500:])

    def test_all_clarified_stop(self):
        self.assertEqual(self.summary["stop_reason"], "全部澄清")
        self.assertEqual(self.summary["status_counts"], {"已证实": 4})

    def test_confidence_throughput(self):
        """检测-调查置信贯通:检测器证据(signal_layer)与私域历史库、政采网
        在同一本台账折算独立根,共同证实金额异常 claim。"""
        row = self.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='判决'"
            " AND payload_json LIKE '%evt_tender_medai:C1%'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        payload = json.loads(row[0])
        roots = payload["support_roots"]
        self.assertIn("signal_layer", roots)
        self.assertIn("internal_history", roots)
        self.assertIn("ccgp", roots)

    def test_private_history_tool_used(self):
        rows = self.conn.execute(
            "SELECT source FROM evidence WHERE event_id='evt_tender_medai'"
            " AND source='internal_history'").fetchall()
        self.assertEqual(len(rows), 1)   # 私域历史基线作为一等工具入账

    def test_cohort_registered(self):
        rows = [r["name"] for r in self.conn.execute(
            "SELECT name FROM cohorts WHERE event_id='evt_tender_medai'")]
        self.assertEqual(rows, ["同市三级医院"])   # 调查圈派生

    def test_h1_dominant_and_recommendation(self):
        dominant = [s["label"] for s in self.summary["standings"]
                    if s.get("dominant")]
        self.assertEqual(dominant, ["新方向扩张"])   # 建设潮假设胜出
        self.assertTrue(self.summary["recommendations"])
        self.assertTrue(all(r["basisClaimIds"] for r in
                            self.summary["recommendations"]))

    def test_early_drafting_background(self):
        """交错起草:背景节在 R1 就提前变现(规划 claim 当轮即证实)。"""
        row = self.conn.execute(
            "SELECT state,last_draft_round FROM sections"
            " WHERE event_id='evt_tender_medai' AND title='主体与事件背景'"
        ).fetchone()
        self.assertEqual((row["state"], row["last_draft_round"]),
                         ("STABLE_DRAFTED", 1))

    def test_report_carries_citations(self):
        md = (Path(self.runs) / "evt_tender_medai" / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("[E1]", md)            # 引用编号
        self.assertIn("判决核对证书", md)
        self.assertIn("行动建议", md)


if __name__ == "__main__":
    unittest.main()
