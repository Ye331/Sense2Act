"""评测层 P1 增补单元:假设级研判传导一致性 + bootstrap CI(2026-10-05)。

评估层级上移(效度锚点):claim 级 A_w 是中间指标,主导假设是否被判决误差
带偏才是终态;统计效力(bootstrap CI)印在报告里,不靠口头声明。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.evaluate import (bootstrap_ci_agreement,       # noqa: E402
                                hypothesis_concordance)


def claim(cid: str, did: str, status: str) -> dict:
    return {"claim_id": cid, "draft_id": did, "status": status}


def hyp(hid: str, label: str, claim_id: str | None) -> dict:
    return {"hypothesis_id": hid, "label": label, "claim_id": claim_id}


class TestHypothesisConcordance(unittest.TestCase):
    """研判传导:金标覆写后主导假设是否与系统现状一致。"""

    HYP_ROWS = [
        hyp("e:H1", "真实经营动态", "e:c1"),
        hyp("e:H1", "真实经营动态", "e:c2"),
        hyp("e:H2", "媒体转述偏差", "e:c3"),
    ]

    def test_concordant_when_overrides_do_not_flip_dominant(self):
        """系统 H1 主导(H1 两证),金标覆写不改变格局 -> 一致。"""
        claims = [claim("e:c1", "c1", "已证实"),
                  claim("e:c2", "c2", "已证实"),
                  claim("e:c3", "c3", "待证实")]
        golden = {"c1": "已证实", "c2": "已证实", "c3": "待证实"}
        h = hypothesis_concordance(claims, self.HYP_ROWS, golden)
        self.assertTrue(h["dominant_agree"])
        self.assertEqual(h["system_dominant"], ["真实经营动态"])
        self.assertEqual(h["golden_dominant"], ["真实经营动态"])

    def test_distortion_when_overrides_flip_dominant(self):
        """判决误差带偏研判:系统漏判 c1 的矛盾证据与 c3 的证实证据;
        金标覆写后 H1 归零、H2 反超 -> 失真。"""
        claims = [claim("e:c1", "c1", "待证实"),   # 金标:矛盾(系统漏判)
                  claim("e:c2", "c2", "已证实"),
                  claim("e:c3", "c3", "待证实")]   # 金标:已证实(系统漏判)
        golden = {"c1": "矛盾", "c2": "已证实", "c3": "已证实"}
        h = hypothesis_concordance(claims, self.HYP_ROWS, golden)
        self.assertFalse(h["dominant_agree"])
        self.assertEqual(h["system_dominant"], ["真实经营动态"])
        self.assertEqual(h["golden_dominant"], ["媒体转述偏差"])

    def test_unmatched_golden_not_overridden(self):
        """unmatched 金标条无对应系统 claim,不参与覆写(局限如实)。"""
        claims = [claim("e:c1", "c1", "已证实")]
        golden = {"c_ghost": "矛盾"}              # 系统里不存在
        h = hypothesis_concordance(claims, self.HYP_ROWS, golden)
        self.assertTrue(h["dominant_agree"])      # 覆写未发生,格局不变

    def test_no_dominant_returns_none(self):
        """全 0 分无主导假设:一致性与否不判(返回 None)。"""
        claims = [claim("e:c1", "c1", "待证实"),
                  claim("e:c3", "c3", "待证实")]
        golden = {"c1": "待证实", "c3": "待证实"}
        h = hypothesis_concordance(claims, self.HYP_ROWS, golden)
        self.assertIsNone(h["dominant_agree"])
        self.assertEqual(h["system_dominant"], [])


class TestBootstrapCI(unittest.TestCase):

    def test_perfect_is_degenerate(self):
        """零误差:CI 退化为单点 [1,1]。"""
        self.assertEqual(bootstrap_ci_agreement([0] * 20, 2), (1.0, 1.0))

    def test_deterministic_same_seed(self):
        """同 seed 确定性:回放可复现。"""
        errs = [0, 0, 1, 0, 2, 0, 0, 1]
        a = bootstrap_ci_agreement(errs, 2, n_boot=500, seed=7)
        b = bootstrap_ci_agreement(errs, 2, n_boot=500, seed=7)
        self.assertEqual(a, b)

    def test_ci_brackets_point_estimate_and_widens_with_noise(self):
        """CI 包含点估计;噪声越大区间越宽。"""
        clean = bootstrap_ci_agreement([0] * 18 + [1] * 2, 2)
        noisy = bootstrap_ci_agreement([0] * 10 + [1] * 5 + [2] * 5, 2)
        point_clean = 1 - (2 * 1) / (20 * 2)
        self.assertLessEqual(clean[0], point_clean)
        self.assertGreaterEqual(clean[1], point_clean)
        self.assertLess(clean[1] - clean[0], noisy[1] - noisy[0])

    def test_empty_returns_none(self):
        self.assertIsNone(bootstrap_ci_agreement([], 2))


if __name__ == "__main__":
    unittest.main()
