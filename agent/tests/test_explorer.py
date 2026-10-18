"""路径 B 探索器 v1:换词重试/深度与预算/轨迹留痕/与 Executor 的降级链集成。

回归背景:2026-09-27 live 实测中,S2 提示与证据批次词表不重叠导致 9 连扑空。
本文件锁定修复行为:一跳不中 -> 从缺口文本重建候选词 -> 命中。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_domain  # noqa: E402
from sense2act.collect import FakeCollector, OfflineGate, ToolRouter  # noqa: E402
from sense2act.collect.expansion import candidate_queries  # noqa: E402
from sense2act.models.base import ActionType  # noqa: E402
from sense2act.models.decision import DecisionSheet, GapStep  # noqa: E402
from sense2act.stages.s3_executor import (  # noqa: E402
    Executor,
    StubExplorer,
    WordFamilyExplorer,
)

# 复刻 live 失败现场:S2 提示词与批次 keys 字面不重叠
LIVE_SEED = {
    "batches": [
        {"keys": ["医院 智慧医院 三期 规划"], "items": [
            {"platform": "official_website", "source": "hosp_web",
             "title": "智慧医院三期规划", "content": "三期规划原文",
             "authorType": "官方"}]},
    ],
    "history": {},
}


class TestCandidateQueries(unittest.TestCase):
    def test_hints_first_at_depth0(self):
        cands = candidate_queries(["提示词"], "缺口文本", max_depth=0)
        self.assertEqual(cands[0], (0, "提示词"))

    def test_cross_source_boost(self):
        """同时出现在缺口与主张里的词(跨源)排在本层非跨源词之前。"""
        cands = candidate_queries([], "该院既定建设规划文件(智慧医院三期)",
                                  claim="本次采购属于三期建设")
        d1 = [q for d, q in cands if d == 1]
        self.assertIn("三期", d1[:2])      # 跨源词置顶(两个跨源词占据前两位)
        self.assertIn("建设", d1[:2])
        self.assertNotIn("院既", d1[:2])   # 非跨源噪声词被压后

    def test_depth_tiers(self):
        cands = candidate_queries([], "智慧医院三期规划文件")
        by_depth = {d for d, _ in cands}
        self.assertEqual(by_depth, {1, 2, 3})   # 2/3/4 克三层

    def test_cap_respected(self):
        cands = candidate_queries([], "该院既定建设规划文件智慧医院三期批复方案",
                                  cap=5)
        self.assertLessEqual(len(cands), 5)


class TestWordFamilyExplorer(unittest.TestCase):
    def setUp(self):
        self.gate = OfflineGate(FakeCollector(LIVE_SEED), max_calls=40)
        self.explorer = WordFamilyExplorer(
            self.gate, ToolRouter(make_domain().platform_rules),
            depth_max=3, calls_max=15)

    def test_bridges_live_failure(self):
        """复刻 live 失败现场:提示扑空 -> 缺口重建词命中。"""
        gap = GapStep(gap="该院既定建设规划文件(智慧医院三期)",
                      acceptance="规划或批复文件原文")
        outcome = self.explorer.explore(
            gap, hints=["XX市第一人民医院 招标公告"],
            claim_text="本次采购属于三期建设")
        self.assertTrue(outcome["reachable"])
        self.assertEqual(len(outcome["items"]), 1)
        self.assertTrue(outcome["trajectory"])          # 每次尝试有轨迹
        self.assertTrue(outcome["accepted_query"])       # 命中词留痕(B->A 原料)

    def test_unreachable_when_nothing_matches(self):
        gap = GapStep(gap="外星基地坐标", acceptance="天文台原始记录")
        outcome = self.explorer.explore(gap, hints=["完全不相关"])
        self.assertFalse(outcome["reachable"])
        self.assertLessEqual(self.gate.calls, 15)        # 调用上限被尊重

    def test_budget_cap(self):
        gap = GapStep(gap="外星基地坐标", acceptance="x")
        explorer = WordFamilyExplorer(self.gate, ToolRouter({}),
                                      depth_max=3, calls_max=3)
        outcome = explorer.explore(gap, hints=["a"])
        self.assertFalse(outcome["reachable"])
        self.assertTrue(any("预算耗尽" in s.desc for s in outcome["trajectory"]))


class TestExecutorIntegration(unittest.TestCase):
    def test_tool_action_recovers_via_expansion(self):
        """查工具一跳扑空 -> 换词重试命中(不再空转到 CRAG)。"""
        ex = Executor(OfflineGate(FakeCollector(LIVE_SEED), max_calls=40),
                      ToolRouter(make_domain().platform_rules),
                      WordFamilyExplorer(OfflineGate(FakeCollector(LIVE_SEED)),
                                         ToolRouter({}), depth_max=3, calls_max=15))
        sheet = DecisionSheet(
            round=1, main_target="C2", action=ActionType.TOOL,
            gap_chain=[GapStep(gap="该院既定建设规划文件(智慧医院三期)",
                               acceptance="规划原文")],
            search_hints=["XX市第一人民医院 招标公告"])
        obs, quality = ex.execute(sheet, 1, "本次采购属于三期建设")
        self.assertEqual(quality, "ok")
        self.assertIn("换词命中", obs.action_brief)

    def test_stub_still_unreachable(self):
        """StubExplorer 对照行为不变(用于对照实验)。"""
        ex = Executor(OfflineGate(FakeCollector(LIVE_SEED)),
                      ToolRouter({}), StubExplorer())
        sheet = DecisionSheet(
            round=1, main_target="C1", action=ActionType.EXPLORE,
            gap_chain=[GapStep(gap="任意", acceptance="任意")])
        calls = []
        obs, quality = ex.execute(
            sheet, 1, "", unreachable_callback=lambda d: calls.append(d))
        self.assertEqual(quality, "unreachable")
        self.assertEqual(calls, ["C1"])


if __name__ == "__main__":
    unittest.main()
