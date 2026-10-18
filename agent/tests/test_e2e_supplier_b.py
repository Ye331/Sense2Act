"""端到端:供应商 B 风险事件(架构文档第八节的认识论核心场景)。

走查断言:claim 终态矩阵 / 三成因翻转 / 同源折算 / relations 长边消边 /
H0->H1 假设翻转 / 交错起草状态机 / OQ 纪律 / 全链留痕 / 确定性重跑 / 对照模式。
"""
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
EX = "examples/supplier_b"


def run_cli(extra: list[str], workdir: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sense2act", "investigate",
         "--envelope", f"{EX}/envelope.json",
         "--replay", f"{EX}/responses.json",
         "--seed", f"{EX}/evidence_seed.json",
         "--refresh-plan", f"{EX}/refresh_plan.json",
         "--event-id", "evt_supplier_b",
         "--config-dir", "data/config"] + extra,
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


class TestSupplierBEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = str(Path(cls.tmp) / "e2e.db")
        cls.runs = str(Path(cls.tmp) / "runs")
        cls.proc = run_cli(["--db", cls.db, "--runs-dir", cls.runs], cls.tmp)
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_00_exit_ok(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-500:])

    def test_claim_final_matrix(self):
        rows = {r["draft_id"]: r["status"]
                for r in self.conn.execute("SELECT draft_id,status FROM claims")}
        self.assertEqual(rows, {
            "C1": "矛盾", "C2": "待证实", "C3": "已证实",
            "C4": "矛盾", "C5": "已证实", "C6": "矛盾"})

    def test_transitions_with_causes(self):
        rows = [(r["claim_id"].split(":")[-1], r["old_status"], r["new_status"],
                 r["cause"]) for r in self.conn.execute(
                     "SELECT claim_id,old_status,new_status,cause FROM"
                     " claim_transitions ORDER BY id")]
        self.assertEqual(rows, [
            ("C1", "待证实", "矛盾", "矛盾翻转"),
            ("C3", "待证实", "已证实", None),
            ("C4", "待证实", "已证实", None),
            ("C5", "待证实", "已证实", None),
            ("C6", "待证实", "矛盾", "时效翻转"),
            ("C4", "已证实", "矛盾", "状态刷新")])

    def test_lineage_folding_and_relations(self):
        """企查查折叠为 gsxt 根;C5 证实长边,C4 翻转后供应边消失。"""
        e6 = self.conn.execute(
            "SELECT lineage_root FROM evidence WHERE title LIKE '企查查%'"
        ).fetchone()
        self.assertEqual(e6["lineage_root"], "gsxt")
        edges = [(r["head_name"], r["relation_type"], r["tail_name"])
                 for r in self.conn.execute(
                     "SELECT * FROM relations WHERE event_id='evt_supplier_b'")]
        self.assertEqual(edges, [
            ("产业内幕君2026", "协同", "C汽车销售集团有限公司")])

    def test_hypothesis_flip_to_h1(self):
        summary = json.loads((Path(self.runs) / "evt_supplier_b" /
                              "result.json").read_text(encoding="utf-8"))
        dominant = [s["label"] for s in summary["standings"] if s.get("dominant")]
        self.assertEqual(dominant, ["竞对放风"])

    def test_interleave_state_machine(self):
        """核心事实 R2 提前变现;关联方 R4 起草 -> R5 漂移重写(REDRAFTED)。"""
        rows = {r["title"]: (r["state"], r["last_draft_round"], r["snapshot_version"])
                for r in self.conn.execute(
                    "SELECT title,state,last_draft_round,snapshot_version FROM"
                    " sections WHERE event_id='evt_supplier_b'")}
        self.assertEqual(rows["核心事实"], ("STABLE_DRAFTED", 2, 1))
        self.assertEqual(rows["动态时间线与关联方"], ("REDRAFTED", 5, 2))

    def test_oq_keeps_unresolved(self):
        md = (Path(self.runs) / "evt_supplier_b" / "report.md").read_text(
            encoding="utf-8")
        start = md.index("### Open Questions")
        end = md.index("### 行动建议")
        oq_block = md[start:end]
        self.assertIn("C2", oq_block)          # 未决项如实保留在 OQ 节
        self.assertNotIn("C1", oq_block)       # 已裁定项不混入

    def test_decision_log_covers_all_kinds(self):
        kinds = {r["kind"] for r in self.conn.execute(
            "SELECT DISTINCT kind FROM decision_log")}
        self.assertTrue({"决策单", "卷宗", "意见", "判决", "钩子", "入账",
                         "刷新"} <= kinds)
        # 无降级决策单(回放全对齐)
        degraded = self.conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE degraded=1").fetchone()
        self.assertEqual(degraded["n"], 0)

    def test_deterministic_rerun(self):
        tmp2 = tempfile.mkdtemp()
        try:
            run_cli(["--db", str(Path(tmp2) / "d.db"),
                     "--runs-dir", str(Path(tmp2) / "runs")], tmp2)
            r1 = (Path(self.runs) / "evt_supplier_b" / "report.md").read_bytes()
            r2 = (Path(tmp2) / "runs" / "evt_supplier_b" / "report.md").read_bytes()
            self.assertEqual(r1, r2)   # 温度0+回放+确定性钩子 = 字节级可复现
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)


class TestSupplierBNoInterleave(unittest.TestCase):
    """对照模式(interleave=false):退化为查完再写,全部节在 finalize 补齐。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = str(Path(cls.tmp) / "noint.db")
        cls.runs = str(Path(cls.tmp) / "runs")
        cls.proc = run_cli(["--db", cls.db, "--runs-dir", cls.runs,
                            "--no-interleave"], cls.tmp)
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_sections_drafted_at_finalize(self):
        self.assertEqual(self.proc.returncode, 0)
        rows = {r["title"]: (r["state"], r["last_draft_round"])
                for r in self.conn.execute(
                    "SELECT title,state,last_draft_round FROM sections"
                    " WHERE event_id='evt_supplier_b'")}
        for title, (state, rnd) in rows.items():
            self.assertEqual(state, "STABLE_DRAFTED", title)
            self.assertEqual(rnd, 6, title)   # 循环结束后的统一轮次补齐
        # 认识论结果与交错模式一致(两模式共用同一台账语义)
        counts = {r["status"]: r["n"] for r in self.conn.execute(
            "SELECT status,COUNT(*) n FROM claims"
            " WHERE event_id='evt_supplier_b' GROUP BY status")}
        self.assertEqual(counts, {"已证实": 2, "待证实": 1, "矛盾": 3})


if __name__ == "__main__":
    unittest.main()
