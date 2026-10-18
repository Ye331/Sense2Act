"""评测层不变量回归套件 e2e(P0-2,2026-10-05):认识论纪律的回归门。

背景(2026-10-04 教训):sweep2 修好的复述型翻正,在 sweep3 换管道后回潮
(0 根 claim 连自证例外门槛都够不着)——当时没有任何回归 case 把它锁住。
本套件把"永不许违反"的行为固化为固定断言:管道任何改动后跑一遍,回潮当场红。

锁定的不变量:
- 套件级 require={"over":0,"flip":0}:0 过度断言/0 反转贯穿全部案例
  (保守认识论的量化底线,sweep/eval 历史上从未破例);
- 离线两套 A_w=1.0:金标与 e2e 回归断言同源,恒为满分(互证回归基线);
- 孤证不立:ai_model_release 实质断言(4 转载=1 根)金标待证实,
  系统若断成终态即触发 over>0 → require 拦截。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sense2act.evaluate import evaluate_suite  # noqa: E402


class TestInvariants(unittest.TestCase):

    def test_epistemic_invariants_hold(self):
        code, report = evaluate_suite("eval/invariants", timeout=180)
        self.assertEqual(
            code, 0,
            f"退出码={code}, invariant={report.get('invariant')}")
        self.assertIsNotNone(report["invariant"])
        self.assertTrue(
            report["invariant"]["passed"],
            f"不变量违反:{report['invariant']['violations']}")
        by = {r["case_id"]: r for r in report["rows"]}
        # 离线两套:金标=e2e 断言,恒满分(回归基线互证)
        self.assertEqual(by["supplier_b"]["weighted_agreement"], 1.0)
        self.assertEqual(by["tender_medical_ai"]["weighted_agreement"], 1.0)
        # 分列指标(P0-1)可计算且取值合理:对齐率≤1,条件一致率∈[0,1]
        for cid in ("supplier_b", "tender_medical_ai"):
            self.assertLessEqual(by[cid]["align_rate"], 1.0)
            self.assertLessEqual(by[cid]["conditional_agreement"], 1.0)


if __name__ == "__main__":
    unittest.main()
