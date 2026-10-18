"""评测层单元:suite 装载 / variant 门禁 / blocked 案例处理 / 报告渲染。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act import evaluate as ev  # noqa: E402
from test_evaluate_score import GOLDEN_DOC, matched_system, synth_outputs  # noqa: E402


def disabled_case(cid: str = "c_disabled") -> dict:
    return {"case_id": cid, "title": "占位", "envelope": "x", "responses": "x",
            "seed": "x", "event_id": "evt_x", "enabled": False,
            "blocked_note": "回放件待冻结"}


def make_suite(tmp: str, cases: list[dict], **suite_kw) -> Path:
    root = Path(tmp) / "cases"
    root.mkdir(parents=True, exist_ok=True)
    ids = []
    for c in cases:
        cid = c["case_id"]
        ids.append(cid)
        d = root / cid
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.json").write_text(json.dumps(c, ensure_ascii=False),
                                     encoding="utf-8")
    suite = {"suite_id": "unit", "variant": "中",
             "weights": {"miss": 1, "over": 2, "flip": 2},
             "default_budget": {"s6_opinion_max": 40, "llm_calls_max": 60},
             "cases": ids}
    suite.update(suite_kw)
    (root / "suite.json").write_text(json.dumps(suite, ensure_ascii=False),
                                     encoding="utf-8")
    return root


def cli_args(tmp: str, suite: Path, variant: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        suite=str(suite), only="", variant=variant,
        out=str(Path(tmp) / "r.md"), out_json=str(Path(tmp) / "r.json"),
        out_dir=str(Path(tmp) / "out"), work_root="", keep_work=False,
        timeout=60)


class TestSuiteLoading(unittest.TestCase):

    def test_suite_loading_defaults(self):
        """suite.json 解析;案例级 budget 覆盖套件默认。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            suite, cases = ev.load_suite(root)
            self.assertEqual(suite["suite_id"], "unit")
            self.assertEqual([c["case_id"] for c in cases], ["c_disabled"])
            merged = ev.case_budget({"budget": {"llm_calls_max": 99}},
                                    suite["default_budget"])
            self.assertEqual((merged.s6_opinion_max, merged.llm_calls_max),
                             (40, 99))

    def test_missing_case_json_raises(self):
        """suite 引用不存在的案例 -> 显式报错,不静默跳过。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            (root / "c_disabled" / "case.json").unlink()
            with self.assertRaises(FileNotFoundError):
                ev.load_suite(root)


class TestVariantGate(unittest.TestCase):
    """variant 校验:非法值显式拒绝(exit 2);粗/细已随 P1-6 粒度指令位启用。"""

    def test_variant_invalid_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = ev.main(cli_args(tmp, root, "x"))
            self.assertIn("非法", err.getvalue())
            self.assertEqual(code, 2)

    def test_variant_coarse_accepted_and_recorded(self):
        """粗档被接受并写进报告元数据(disabled 案例不跑子进程,纯通道验证)。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            code, report = ev.evaluate_suite(root, variant="粗", timeout=60)
            self.assertEqual(code, 0)
            self.assertEqual(report["variant"], "粗")

    def test_variant_fine_accepted_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            code, report = ev.evaluate_suite(root, variant="细", timeout=60)
            self.assertEqual(code, 0)
            self.assertEqual(report["variant"], "细")

    def test_variant_middle_accepted_and_recorded(self):
        """中档(=现状)通过,并写进报告元数据供 P1-6 sweep 分组。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            code, report = ev.evaluate_suite(root, timeout=60)
            self.assertEqual(code, 0)
            self.assertEqual(report["variant"], "中")


class TestBlockedCases(unittest.TestCase):

    def test_blocked_case_listed_not_scored(self):
        """disabled 案例列行不跑不打分,不进池化。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            out_dir = Path(tmp) / "out"
            code, report = ev.evaluate_suite(
                root, out=Path(tmp) / "r.md", out_json=Path(tmp) / "r.json",
                out_dir=out_dir, timeout=60)
            row = report["rows"][0]
            self.assertEqual(row["run_status"], "disabled")
            self.assertIsNone(row.get("weighted_agreement"))
            self.assertEqual(report["pooled"]["all"]["n_cases"], 0)
            self.assertEqual(list(out_dir.glob("*.json")), [])
            self.assertIn("c_disabled", (Path(tmp) / "r.md").read_text(encoding="utf-8"))

    def test_only_unknown_case_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_suite(tmp, [disabled_case()])
            with self.assertRaises(ValueError):
                ev.evaluate_suite(root, only=["nope"])


class TestReportRendering(unittest.TestCase):

    def test_report_md_columns(self):
        """汇总表列名与口径说明齐全(论文取数形态)。"""
        row = ev.score_case(
            case={"case_id": "t", "title": "T", "source": "unit"},
            golden_doc=GOLDEN_DOC, outputs=synth_outputs(matched_system()),
            weights=ev.Weights(), budget=ev.Budget(),
            align_cfg=ev.AlignConfig(), attempted_s6=4,
            run_meta={"run_status": "ok", "variant": "中"})
        md = ev.render_report_md(
            suite_id="unit", variant="中", weights=ev.Weights(),
            align_cfg=ev.AlignConfig(), rows=[row],
            pooled_all=ev.pooled_score([row], ev.Weights()),
            pooled_verified=ev.pooled_score([row], ev.Weights(),
                                            verified_only=True))
        for col in ("加权一致率", "S6意见(实/上界)", "漏报", "过度断言", "反转",
                    "验票失败", "口径说明", "池化(全部已跑)", "池化(仅已核对金标)",
                    "每案例明细"):
            self.assertIn(col, md)
        self.assertIn("A_w = 1 −", md)


if __name__ == "__main__":
    unittest.main()
