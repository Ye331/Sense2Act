"""采集层:工具/平台路由 / FakeCollector 匹配与失败回执 / 闸门护栏 / 观察包组装。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_domain  # noqa: E402
from sense2act.collect import FakeCollector, OfflineGate, ToolRouter  # noqa: E402
from sense2act.models.decision import DecisionSheet, GapStep  # noqa: E402
from sense2act.models.base import ActionType  # noqa: E402
from sense2act.models.observation import CollectRequest, EvidenceItemLite  # noqa: E402
from sense2act.stages.s3_executor import Executor, StubExplorer  # noqa: E402

SEED = {
    "batches": [
        {"keys": ["B公司 官方记录"], "items": [
            {"platform": "official_website", "source": "zxgk.court.gov.cn",
             "title": "执行记录", "content": "B公司无失信记录", "authorType": "官方"}]},
        {"keys": ["无人命中"], "items": [], "reports": [
            {"platform": "toutiao", "status": "rate_limited", "count": 0,
             "retryable": True}]},
    ],
    "history": {"B公司": {"items": [
        {"platform": "internal_history", "source": "internal_history",
         "title": "内部历史", "content": "B公司历史采购分布", "authorType": "内部数据"}]}},
}


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.router = ToolRouter(make_domain().platform_rules)

    def test_history_tool_routing(self):
        req = self.router.route("历史中标方与金额分布", ["历史中标"], "claim 文本")
        self.assertEqual(req.tool, "history_baseline")

    def test_history_hit_still_selects_platforms(self):
        """修复(2026-10-16 live 验收):历史基线缺口命中工具路由后不得短路
        平台选择——live 私域库不存在,历史记录的权威源是公开政采源(R6),
        深搜 prompt 的『历史采购基线』类缺口必须能出网而非空转。"""
        req = self.router.route(
            "B公司历史采购基线是否异常", ["B公司 采购公告"], "claim")
        self.assertEqual(req.tool, "history_baseline")
        self.assertIn("ccgp", req.platforms)
        # 无任何平台关键词时也不得为空(fallback 兜底)
        req2 = self.router.route("历史采购基线核查", ["公司名称"], "claim")
        self.assertEqual(req2.tool, "history_baseline")
        self.assertTrue(req2.platforms)

    def test_history_keyword_not_overgreedy(self):
        """缺口含"历史行为记录"但非采购基线语境 -> 不路由到历史库。"""
        req = self.router.route("账号的历史行为记录", ["账号 行为"], "")
        self.assertEqual(req.tool, "search")

    def test_platform_rules_and_exclusion(self):
        req = self.router.route("官方公告与批复", ["公告"], "")
        self.assertIn("official_website", req.platforms)
        # cctv_news 2026-10-16 起移出默认路由(通用新闻栏目对行业信号不对题)
        self.assertNotIn("cctv_news", req.platforms)
        for p in req.platforms:
            self.assertNotIn(p, ["weibo", "xiaohongshu", "douyin", "wechat"])

    def test_search_context_downstream(self):
        """定向检索锚点:信封精确定位字段下传到 CollectRequest。"""
        req = self.router.route(
            "查询中标结果", ["中标公告"], "claim",
            search_context={"buyer": "某医院", "project_no": "SHGP-2026-A536",
                            "published_date": "2026-10-03"})
        self.assertEqual(req.buyer, "某医院")
        self.assertEqual(req.project_no, "SHGP-2026-A536")
        self.assertEqual(req.published_date, "2026-10-03")
        # 无 search_context 时三字段为空(不破坏现有调用)
        req2 = self.router.route("查询中标结果", ["中标公告"], "claim")
        self.assertEqual((req2.buyer, req2.project_no, req2.published_date),
                         ("", "", ""))

    def test_source_url_downstream(self):
        """信封自带目标公告 URL(发现三)透传到 CollectRequest;缺省为空。"""
        req = self.router.route(
            "查询中标结果", ["中标公告"], "claim",
            search_context={"source_url":
                            "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/x.htm"})
        self.assertEqual(
            req.source_url, "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/x.htm")
        req2 = self.router.route("查询中标结果", ["中标公告"], "claim")
        self.assertEqual(req2.source_url, "")

    def test_query_injects_routing_keys(self):
        """§八 契约:路由键确定性注入 query(自由词 | 编号:XXX | 采购人:YYY)。"""
        req = self.router.route(
            "查询中标结果", ["中标公告"], "claim",
            search_context={"buyer": "某医院", "project_no": "SHGP-2026-A536"})
        self.assertIn("| 编号:SHGP-2026-A536 | 采购人:某医院", req.query)
        # 无 search_context(rumor 场景) → 不注入
        req2 = self.router.route("查询中标结果", ["中标公告"], "claim")
        self.assertNotIn("| 编号:", req2.query)
        self.assertNotIn("| 采购人:", req2.query)
        # 有 buyer 无 project_no → 只注入采购人(提取不到编号就不硬造)
        req3 = self.router.route(
            "查询中标结果", ["中标公告"], "claim",
            search_context={"buyer": "某医院", "project_no": ""})
        self.assertIn("| 采购人:某医院", req3.query)
        self.assertNotIn("| 编号:", req3.query)

    def test_fallback_platforms(self):
        req = self.router.route("泛泛而谈的缺口", ["随便查查"], "")
        self.assertTrue(req.platforms)


class TestFakeCollector(unittest.TestCase):
    def setUp(self):
        self.collector = FakeCollector(SEED)

    def test_match_and_normal_receipt(self):
        receipt = self.collector.search(CollectRequest(
            query="B公司 官方记录", platforms=["toutiao"]))
        self.assertEqual(receipt.status, "success")
        self.assertEqual(len(receipt.items), 1)
        self.assertEqual(receipt.source_reports[0].platform, "official_website")

    def test_history_lookup(self):
        receipt = self.collector.search(CollectRequest(
            query="B公司 历史采购 基线", tool="history_baseline"))
        self.assertEqual(receipt.tool, "history_baseline")
        self.assertEqual(receipt.items[0].source, "internal_history")

    def test_empty_receipt_when_no_match(self):
        receipt = self.collector.search(CollectRequest(query="完全不相关查询"))
        self.assertEqual(receipt.status, "empty")
        self.assertEqual(receipt.source_reports[0].status, "empty")

    def test_failure_receipt_is_first_class(self):
        """失败回执与成功同构(rate_limited + retryable),不伪装成 empty。"""
        receipt = self.collector.search(CollectRequest(query="无人命中"))
        self.assertEqual(receipt.source_reports[0].status, "rate_limited")
        self.assertTrue(receipt.source_reports[0].retryable)


class TestGateAndExecutor(unittest.TestCase):
    def test_gate_budget(self):
        gate = OfflineGate(FakeCollector(SEED), max_calls=1)
        r1 = gate.send(CollectRequest(query="B公司 官方记录"))
        self.assertEqual(r1.status, "success")
        r2 = gate.send(CollectRequest(query="B公司 官方记录"))
        self.assertEqual(r2.status, "failed")  # 护栏:超限返回失败回执

    def test_explore_stub_marks_unreachable(self):
        calls = []
        ex = Executor(OfflineGate(FakeCollector(SEED)),
                      ToolRouter(make_domain().platform_rules), StubExplorer())
        sheet = DecisionSheet(
            round=1, main_target="C1", action=ActionType.EXPLORE,
            gap_chain=[GapStep(gap="微信正文深度", acceptance="页面级证据")])
        obs, quality = ex.execute(
            sheet, 1, "claim 文本", unreachable_callback=lambda d: calls.append(d))
        self.assertEqual(quality, "unreachable")
        self.assertEqual(calls, ["C1"])
        self.assertTrue(obs.trajectory)

    def test_assemble_dedup(self):
        seed = {"batches": [{"keys": ["k"], "items": [
            {"platform": "p1", "source": "s", "title": "t", "content": "c",
             "dedupKey": "dup"},
            {"platform": "p1", "source": "s", "title": "t", "content": "c",
             "dedupKey": "dup"},
            {"platform": "p2", "source": "s", "title": "t2", "content": "c2"},
        ]}]}
        ex = Executor(OfflineGate(FakeCollector(seed)),
                      ToolRouter({}), StubExplorer())
        sheet = DecisionSheet(round=1, main_target="C1", action=ActionType.TOOL,
                              gap_chain=[GapStep(gap="k", acceptance="a")],
                              search_hints=["k"])
        obs, q = ex.execute(sheet, 1, "")
        self.assertEqual(len(obs.items), 2)  # dedupKey 去重


if __name__ == "__main__":
    unittest.main()
