"""评测层 e2e:evaluate 子命令走真 CLI(供应商B 金标=e2e 回归基线,应得满分)。

另含预算截断对照:max_llm_calls=2 触发预算护栏优雅停止,budget_stop 仍出分
(部分台账打分,报告标 PARTIAL 语义),不伪装不跳过。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EX = "examples/supplier_b"


class TestEvaluateE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "evaluate",
             "--suite", "eval/cases", "--only", "supplier_b",
             "--out", str(Path(cls.tmp) / "report.md"),
             "--out-json", str(Path(cls.tmp) / "report.json"),
             "--out-dir", str(Path(cls.tmp) / "out")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=300)
        cls.report = json.loads(
            (Path(cls.tmp) / "report.json").read_text(encoding="utf-8"))
        cls.row = cls.report["rows"][0]

    def test_00_exit_ok(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-500:])

    def test_supplier_b_perfect_score(self):
        """回放确定性 => 金标(即 e2e 断言矩阵)逐条一致,A_w=1.0。"""
        self.assertEqual(self.row["case_id"], "supplier_b")
        self.assertEqual(self.row["run_status"], "ok")
        self.assertEqual(self.row["n_golden"], 6)
        self.assertEqual(self.row["correct"], 6)
        self.assertEqual((self.row["miss"], self.row["over"], self.row["flip"]),
                         (0, 0, 0))
        self.assertEqual(self.row["weighted_agreement"], 1.0)
        self.assertEqual(self.row["n_system_extra"], 0)

    def test_costs_and_budget(self):
        """S6 意见数(实/上界)与冻结 responses 对账;预算内。"""
        responses = json.loads(
            (ROOT / EX / "responses.json").read_text(encoding="utf-8"))
        attempted = sum(1 for r in responses if r.get("stage") == "s6_opinion")
        self.assertEqual(self.row["s6_attempted"], attempted)
        self.assertGreater(self.row["s6_opinions"], 0)
        self.assertLessEqual(self.row["s6_opinions"], self.row["s6_attempted"])
        self.assertGreater(self.row["llm_calls"], 0)
        self.assertTrue(self.row["budget_ok"])

    def test_case_json_written(self):
        """每案例明细落盘:6 条 claim 级对齐,全部 draft_id 主对齐。"""
        path = Path(self.tmp) / "out" / "supplier_b.json"
        self.assertTrue(path.exists())
        detail = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(detail["claims"]), 6)
        self.assertTrue(all(c["align_method"] == "draft_id"
                            for c in detail["claims"]))
        self.assertTrue(detail["golden_verified"])

    def test_pooled_row_consistent(self):
        """池化行与案例 JSON 一致(单一数据源)。"""
        pooled = self.report["pooled"]["all"]
        self.assertEqual(pooled["n_cases"], 1)
        self.assertEqual(pooled["n_claims"], self.row["n_golden"])
        self.assertEqual(pooled["weighted_agreement"],
                         self.row["weighted_agreement"])
        self.assertEqual(self.report["pooled"]["verified_only"]["n_cases"], 1)


class TestBudgetStop(unittest.TestCase):

    def test_budget_stop_still_scored(self):
        """max_llm_calls=2 -> 预算护栏优雅停止;budget_stop 照常打分,不伪装。"""
        tmp = tempfile.mkdtemp()
        case_dir = Path(tmp) / "cases" / "sb"
        case_dir.mkdir(parents=True)
        case = {
            "case_id": "sb", "title": "预算截断对照", "source": "offline_tuned",
            "envelope": f"{ROOT / EX / 'envelope.json'}",
            "responses": f"{ROOT / EX / 'responses.json'}",
            "seed": f"{ROOT / EX / 'evidence_seed.json'}",
            "refresh_plan": f"{ROOT / EX / 'refresh_plan.json'}",
            "event_id": "evt_supplier_b",
            "run_args": {"max_rounds": 6, "max_llm_calls": 2},
            "budget": {"s6_opinion_max": 40, "llm_calls_max": 60},
            "enabled": True,
        }
        (case_dir / "case.json").write_text(
            json.dumps(case, ensure_ascii=False), encoding="utf-8")
        golden = json.loads(
            (ROOT / "eval/cases/supplier_b/golden.json").read_text(encoding="utf-8"))
        golden["case_id"] = "sb"
        (case_dir / "golden.json").write_text(
            json.dumps(golden, ensure_ascii=False), encoding="utf-8")
        (Path(tmp) / "cases" / "suite.json").write_text(
            json.dumps({"suite_id": "budget", "cases": ["sb"]},
                       ensure_ascii=False), encoding="utf-8")
        from sense2act.evaluate import evaluate_suite
        code, report = evaluate_suite(Path(tmp) / "cases", timeout=300)
        row = report["rows"][0]
        self.assertEqual(row["run_status"], "budget_stop")
        # 回放下降级单吸收预算异常 => 停止原因呈"轮数上限"形态,llm_calls 顶满上限
        self.assertGreaterEqual(row["llm_calls"], 2)
        self.assertIsNotNone(row["weighted_agreement"])   # 部分台账仍出分
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
