# -*- coding: utf-8 -*-
"""方案B双回放(2026-10-25 解禁):门禁语义 + 端到端复现冒烟。

门禁契约:
  --replay × --collect-replay(× --crawler local)= 双回放,放行;
  --replay × --crawler(真实采集)= 仍拒(证据编号不可预测);
  --crawler 无 --live 且非双回放 = 仍拒。

端到端:tender_dalian_police 真实录制件(responses_r1+collection_r1)
双回放 rc=0,回放台账 claims 终态与录制台账(data/rerecord,不入库,
本机开发环境存在)逐条一致。旧录制件早于严格证据门禁时，仅允许下方显式列出的
安全收紧迁移；录制台账缺失时降级为仅 rc=0 断言。
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "eval" / "bench" / "tender_dalian_police"
COLL = ROOT / "eval" / "bench_staging" / "tender_dalian_police" / "collection_r1.json"
REC_DB = ROOT / "data" / "rerecord" / "tender_dalian_police_r1.db"


def _investigate(tmp: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sense2act", "investigate",
         "--envelope", str(CASE / "envelope.json"),
         "--replay", str(CASE / "responses_r1.json"),
         "--assoc", "entity_text",
         "--predicate-form", "on", "--corpus-bound-code", "on",
         "--t1-independence", "on",
         "--event-id", "evt_tender_dalian_police_r1",  # 证据编号前缀须同录制
         "--db", str(Path(tmp) / "probe.db"),
         "--runs-dir", str(Path(tmp) / "runs"),
         *extra],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


@unittest.skipUnless(CASE.exists() and COLL.exists(),
                     "双回放录制件缺失(bench/bench_staging 布局变更)")
class TestDualReplayGate(unittest.TestCase):
    """采集档位门禁的三态契约。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_with_crawler_still_rejected(self):
        """--replay × --crawler(无 collect-replay)→ rc=2 门禁拦截。"""
        proc = _investigate(self.tmp.name, "--crawler", "local")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[门禁]", proc.stderr)

    def test_crawler_without_live_still_rejected(self):
        """--crawler 裸配(无 live 非双回放)→ rc=2 拒绝(argparse 互斥组
        required 拦截,无 --replay/--live 时到不了代码门禁)。"""
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", str(CASE / "envelope.json"),
             "--crawler", "local", "--event-id", "evt_gate_probe",
             "--db", str(Path(self.tmp.name) / "g.db")],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 2)

    def test_dual_replay_end_to_end(self):
        """双回放放行且全链复现:rc=0;有录制台账时逐条 diff 一致。"""
        proc = _investigate(self.tmp.name, "--collect-replay", str(COLL),
                            "--crawler", "local")
        self.assertEqual(proc.returncode, 0,
                         f"双回放失败: {proc.stderr[-400:]}")
        rep_db = Path(self.tmp.name) / "probe.db"
        if not REC_DB.exists():
            self.skipTest("录制台账不在本机(克隆环境),仅断言 rc=0")

        def load(p):
            con = sqlite3.connect(p)
            try:
                return dict(con.execute(
                    "SELECT claim_id, status FROM claims").fetchall())
            finally:
                con.close()
        rec, rep = load(REC_DB), load(rep_db)
        if rec != rep:
            # 2026-11 严格 claim-specific evidence gate 的已审批迁移：这些
            # 结论不再由当前公告替代历史/全局证据；重复的金额子 claim 被去重。
            tightened = {
                f"evt_tender_dalian_police_r1:{cid}"
                for cid in ("c8", "c11", "c16")
            }
            for claim_id in tightened:
                self.assertEqual(rec.pop(claim_id, None), "已证实")
                self.assertEqual(rep.pop(claim_id, None), "待证实")
            self.assertEqual(
                rec.pop("evt_tender_dalian_police_r1:c13a", None), "待证实")
        self.assertEqual(rec, rep,
                         "双回放台账与录制台账 claims 终态不一致")


class TestEvaluateDualReplay(unittest.TestCase):
    """evaluate 双回放主读数:对照函数/路径推导/失败 fallback。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _mini_db(p: Path, rows: dict) -> None:
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE IF NOT EXISTS claims (claim_id TEXT"
                    " PRIMARY KEY, status TEXT NOT NULL)")
        con.execute("DELETE FROM claims")
        con.executemany("INSERT INTO claims VALUES (?,?)", rows.items())
        con.commit()
        con.close()

    def test_replay_matches_recorded(self):
        from sense2act.evaluate import replay_matches_recorded
        a, b = Path(self.tmp.name) / "a.db", Path(self.tmp.name) / "b.db"
        self._mini_db(a, {"x:c1": "已证实", "x:c2": "待证实"})
        self._mini_db(b, {"x:c1": "已证实", "x:c2": "待证实"})
        self.assertEqual(replay_matches_recorded(a, b), (True, 0))
        self._mini_db(b, {"x:c1": "待证实", "x:c2": "待证实", "x:c3": "矛盾"})
        ok, n = replay_matches_recorded(a, b)
        self.assertFalse(ok)
        self.assertEqual(n, 2)   # c1 状态异 + c3 回放多出

    def test_collect_replay_path_precedence(self):
        from sense2act.evaluate import collect_replay_path
        case = {"case_id": "tender_dalian_police"}
        # ①案例目录优先
        case_dir = Path(self.tmp.name)
        (case_dir / "collection_r1.json").write_text("{}", encoding="utf-8")
        self.assertEqual(collect_replay_path(case, case_dir),
                         case_dir / "collection_r1.json")
        # ②bench_staging 兜底(现行布局)
        case_dir2 = Path(self.tmp.name) / "c2"
        case_dir2.mkdir()
        self.assertEqual(collect_replay_path(case, case_dir2),
                         ROOT / "eval" / "bench_staging"
                         / "tender_dalian_police" / "collection_r1.json")
        # ③都无 → None
        self.assertIsNone(collect_replay_path({"case_id": "no_such_case"},
                                              case_dir2))

    @unittest.skipUnless(CASE.exists() and COLL.exists() and REC_DB.exists(),
                         "录制件/录制台账缺失(克隆环境)")
    def test_fallback_to_recorded_on_broken_collection(self):
        """采集录制件损坏(空 records→NotRecordedError)→ fallback 真值口径。"""
        from sense2act.evaluate import (AlignConfig, Weights, run_and_score)
        case_dir = Path(self.tmp.name) / "case"
        case_dir.mkdir(parents=True)
        import shutil
        shutil.copy(CASE / "envelope.json", case_dir / "envelope.json")
        shutil.copy(CASE / "golden.json", case_dir / "golden.json")
        shutil.copy(CASE / "responses_r1.json", case_dir / "responses_r1.json")
        (case_dir / "responses_r1.json.meta.json").write_text(
            (CASE / "responses_r1.json.meta.json").read_text(
                encoding="utf-8-sig"), encoding="utf-8")
        (case_dir / "collection_r1.json").write_text(
            '{"records": {}}', encoding="utf-8")   # 坏件:零录制记录
        shutil.copy(CASE / "case.json", case_dir / "case.json")
        case = json.loads((case_dir / "case.json").read_text(
            encoding="utf-8-sig"))
        row = run_and_score(case, case_dir, Path(self.tmp.name) / "work",
                            300, Weights(), AlignConfig(), {}, "中")
        self.assertEqual(row["run_status"], "ok")
        self.assertIn("fallback 真值口径", row["note"])
        # dalian 真值口径已知读数(bench v1.0):A_w=0.7368
        self.assertAlmostEqual(row["weighted_agreement"], 0.7368, places=3)


if __name__ == "__main__":
    unittest.main()
