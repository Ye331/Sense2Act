"""E1 故障注入(P1-5):采集故障下的行为契约评测——"失败不伪装"的量化固化。

设计借 ToolFailBench 的失败分类学,映射到本项目 8 态回执场景:
- Output-Fabrication(伪造输出) → 注入故障回执后,台账绝不出现该批次的
  evidence、绝不因此翻终态(核心断言:失败不伪装成证据);
- Result-Ignore(结果被忽略) → partial 批次的 items 仍入账、故障回执不阻断
  合法证据(O2 完整性:失败来源以回执入包);
- 全故障降级 → 全部缺口不可达标,claims 诚实停留待证实(0 伪造终态),
  循环不崩溃(rc=0)。

形态:合成 seed(FakeCollector 的 batch.reports 覆盖 8 态)+ 冻结 LLM 回放件,
subprocess 跑 investigate,断言台账状态(与 test_dry_stop 同模式)。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "examples" / "tender_medical_ai" / "envelope.json"
RESP = ROOT / "examples" / "tender_medical_ai" / "responses.json"
SEED = ROOT / "examples" / "tender_medical_ai" / "evidence_seed.json"


def run_with_seed(seed_obj: dict, work: Path, tag: str) -> sqlite3.Connection:
    seed_path = work / f"seed_{tag}.json"
    seed_path.write_text(json.dumps(seed_obj, ensure_ascii=False), encoding="utf-8")
    db = work / f"e1_{tag}.db"
    proc = subprocess.run(
        [sys.executable, "-m", "sense2act", "investigate",
         "--envelope", str(ENV), "--replay", str(RESP), "--seed", str(seed_path),
         "--event-id", f"evt_e1_{tag}", "--db", str(db)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr[-400:]}"
    return sqlite3.connect(db)


def seed_with_reports(base: dict, reports: list[dict], keep_items: bool) -> dict:
    """故障注入覆盖**全部批次**(只改单批会被其余批次的无故障匹配绕过);
    全故障场景同时清空私域 history(私有库不在采集故障范畴,清空以保
    "0 证据入账"断言的纯度)。"""
    obj = json.loads(json.dumps(base, ensure_ascii=False))
    for batch in obj.get("batches", []):
        if not keep_items:
            batch["items"] = []
        batch["reports"] = [dict(r) for r in reports]
    if not keep_items:
        obj["history"] = {}
    return obj


class TestE1FaultInjection(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.tmp.name)
        cls.base_seed = json.loads(SEED.read_text(encoding="utf-8-sig"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _claims(self, conn, tag):
        return conn.execute(
            "SELECT status, COUNT(*) n FROM claims WHERE event_id=?"
            " GROUP BY status", (f"evt_e1_{tag}",)).fetchall()

    def _collected_evidence(self, conn, tag):
        """采集侧证据数(排除 signal_layer 信封证据——信封字段/检测器解释
        是第一批证据的既定设计,不走采集通道,故障注入影响不到它)。"""
        return conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE event_id=? AND platform !="
            " 'signal_layer'", (f"evt_e1_{tag}",)).fetchone()[0]

    def _fabricated(self, conn, tag):
        """伪造终态数:实质断言(corpus_bound=0)在采集全故障下不可能凑齐
        独立根——翻终态即伪造。"""
        return conn.execute(
            "SELECT COUNT(*) FROM claims WHERE event_id=? AND status IN"
            " ('已证实','矛盾') AND corpus_bound=0",
            (f"evt_e1_{tag}",)).fetchone()[0]

    def test_total_failure_no_fabrication(self):
        """全故障(network_error):0 采集证据入账、0 伪造终态、循环不崩。"""
        seed = seed_with_reports(self.base_seed, [
            {"platform": "toutiao", "status": "network_error", "count": 0,
             "retryable": True}], keep_items=False)
        conn = run_with_seed(seed, self.work, "netdown")
        try:
            self.assertEqual(self._collected_evidence(conn, "netdown"), 0,
                             "故障批次不得产生任何采集证据(失败不伪装)")
            self.assertEqual(self._fabricated(conn, "netdown"), 0,
                             "实质断言不得被伪造翻终态")
        finally:
            conn.close()

    def test_parser_changed_reported_not_faked(self):
        """parser_changed 态如实上报:不伪装 success,也不伪装 empty 丢弃回执
        ——批次不可用,台账无采集证据,但循环诚实走完。"""
        seed = seed_with_reports(self.base_seed, [
            {"platform": "toutiao", "status": "parser_changed", "count": 0,
             "retryable": False}], keep_items=False)
        conn = run_with_seed(seed, self.work, "parser")
        try:
            self.assertEqual(self._collected_evidence(conn, "parser"), 0)
            self.assertEqual(self._fabricated(conn, "parser"), 0)
        finally:
            conn.close()

    def test_partial_failure_keeps_valid_items(self):
        """partial(混合 success+故障):合法 items 仍入账(Result-Ignore 防线),
        故障不阻断合法证据;且无伪造终态。"""
        seed = seed_with_reports(self.base_seed, [
            {"platform": "toutiao", "status": "success", "count": 2,
             "retryable": False},
            {"platform": "south_weekend", "status": "network_error", "count": 0,
             "retryable": True}], keep_items=True)
        conn = run_with_seed(seed, self.work, "partial")
        try:
            self.assertGreater(self._collected_evidence(conn, "partial"), 0,
                               "partial 批次的合法 items 仍应入账")
            self.assertEqual(self._fabricated(conn, "partial"), 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
