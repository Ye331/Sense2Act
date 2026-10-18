"""天津政采适配器:解析/跨栏目去重/Java日期/8态映射 + 注册/路由/谱系回归。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_domain  # noqa: E402
from sense2act.collect.rules import ToolRouter  # noqa: E402
from sense2act.crawler import (  # noqa: E402
    EgressHttpClient,
    TianjinZfcgAdapter,
    build_default_registry,
)
from sense2act.crawler.adapters.tianjin_zfcg import parse_tender_meta  # noqa: E402
from sense2act.models.observation import EvidenceItemLite  # noqa: E402

# 结构取自 2026-10-03 实抓首页(栏目 tab 内容同页静态渲染,同一公告跨栏目重复)
TJ_OK = """
<html><body>
<ul class="txtList">
 <li>
  <a href="/viewer.do?id=1094165916&ver=2" target="_blank">
   天津市西青区中医医院2026年信息化软件采购项目 (项目编号:XCSD-2026-C-293)公开招标公告
  </a><span class="times">Fri Oct 02 18:03:22 CST 2026</span>
 </li>
 <li>
  <a href="/viewer.do?id=1094164529&ver=2" target="_blank">
   天津轻工职业技术学院数智时尚与创意中心建设项目 (项目编号:SHGP-2026-A516)公开招标公告
  </a><span class="times">Wed Sep 30 21:28:09 CST 2026</span>
 </li>
</ul>
<ul class="txtList">
 <li>
  <a href="/viewer.do?id=1094165916&ver=2">天津市西青区中医医院(重复栏目)</a>
  <span class="times">Fri Oct 02 18:03:22 CST 2026</span>
 </li>
 <li>
  <a href="/viewer.do?id=1094158781&ver=2">天津市宝坻区政府采购中心考核结果公告</a>
  <span class="times">Wed Sep 30 14:24:20 CST 2026</span>
 </li>
 <li>
  <a href="/viewer.do?id=&ver=2">无ID残缺条目应被跳过</a>
  <span class="times">Wed Sep 30 14:24:20 CST 2026</span>
 </li>
</ul>
</body></html>"""

TJ_CHANGED = "<html><div>改版了</div></html>"
TJ_BLOCKED = "<html><title>频繁访问!</title>您的访问过于频繁,请稍后再试。</html>"


def ok_transport(page):
    def http_get(url, timeout=None):
        class R:
            status_code = 200
            text = page
        return R()
    return http_get


class TestTianjinZfcgAdapter(unittest.TestCase):
    def test_parse_dedup_filter_and_contract(self):
        ad = TianjinZfcgAdapter(http_get=ok_transport(TJ_OK))
        r = ad.search("医院", 10)
        self.assertEqual(r.status, "success")
        self.assertEqual(len(r.items), 1)          # 跨栏目重复与残缺ID均被剔除
        item = r.items[0]
        self.assertEqual(item["dedup_key"], "tianjin_zfcg:1094165916")
        self.assertEqual(item["source"], "www.ccgp-tianjin.gov.cn")
        self.assertEqual(item["author_type"], "官方公告")
        self.assertTrue(item["url"].startswith("https://www.ccgp-tianjin.gov.cn/viewer.do"))
        # 北京 10-02 18:03 -> UTC 10:03(Java 日期串确定性解析,各分支返回 str)
        self.assertIn("2026-10-02T10:03:22", item["published_at"])
        EvidenceItemLite.model_validate(item)       # evidence-lite 契约(toutiao datetime 前车之鉴)
        # 类别词纪律:"采购"不计入命中 -> 只命中"采购"的政采中心条目不再入选
        r2 = ad.search("医院 采购", 10)
        self.assertEqual(len(r2.items), 1)
        self.assertEqual(r2.items[0]["dedup_key"], "tianjin_zfcg:1094165916")
        # 只命中类别词"建设"的条目(学院建设公告)同样被剔除,不污染台账
        r3 = ad.search("医院 建设", 10)
        self.assertEqual(len(r3.items), 1)
        self.assertEqual(r3.items[0]["dedup_key"], "tianjin_zfcg:1094165916")

    def test_honest_empty_with_scanned_count(self):
        ad = TianjinZfcgAdapter(http_get=ok_transport(TJ_OK))
        r = ad.search("完全无关的词", 10)
        self.assertEqual(r.status, "empty")
        self.assertIn("没有匹配", r.message)
        self.assertIn("扫查", r.message)

    def test_status_mapping(self):
        cases = [(429, "rate_limited"), (403, "authentication_required"),
                 (502, "network_error"), (404, "network_error")]
        for code, expect in cases:
            ad = TianjinZfcgAdapter(
                http_get=lambda u, t=None, c=code: type("R", (), {"status_code": c, "text": ""})())
            self.assertEqual(ad.search("q", 10).status, expect)

    def test_network_exception(self):
        def boom(url, timeout=None):
            raise TimeoutError("x")
        r = TianjinZfcgAdapter(http_get=boom).search("q", 10)
        self.assertEqual(r.status, "network_error")
        self.assertTrue(r.retryable)

    def test_block_page_and_parser_changed(self):
        self.assertEqual(
            TianjinZfcgAdapter(http_get=ok_transport(TJ_BLOCKED))
            .search("q", 10).status, "authentication_required")
        self.assertEqual(
            TianjinZfcgAdapter(http_get=ok_transport(TJ_CHANGED))
            .search("q", 10).status, "parser_changed")

    def test_empty_body_is_parser_changed_not_failed(self):
        """缺陷回归:空 body/畸形页触发 lxml ParserError(非 ValueError),
        必须归 parser_changed(不可重试),不得逃逸成 failed(retryable)。"""
        for page in ("", "   "):
            self.assertEqual(
                TianjinZfcgAdapter(http_get=ok_transport(page))
                .search("q", 10).status, "parser_changed")

    def test_blank_query_rejected_not_full_dump(self):
        """缺陷回归:空/纯空白查询不得伪装成功并全量返回(权威根污染)。"""
        for q in ("", "   ", None):
            r = TianjinZfcgAdapter(http_get=ok_transport(TJ_OK)).search(q, 10)
            self.assertEqual(r.status, "empty")
            self.assertIn("拒绝全量", r.message)

    def test_generic_only_query_rejected(self):
        """缺陷回归:全类别词查询零选择性,拒绝泛匹配并如实说明。"""
        r = TianjinZfcgAdapter(http_get=ok_transport(TJ_OK)).search("采购 公告", 10)
        self.assertEqual(r.status, "empty")
        self.assertIn("类别词", r.message)


class TestTenderMeta(unittest.TestCase):
    """标题 -> 阶段/项目编号/采购人 的确定性抽取(无把握给空串,不猜)。"""

    def test_stage_tail_keyword_wins(self):
        cases = [
            ("天津市西青区中医医院信息化软件采购项目(项目编号:XCSD-2026-C-293)公开招标公告", "招标采购"),
            # 陷阱:"合同"在前(合同能源管理是项目名),尾部的磋商才是公告类型
            ("天津市海河管理中心合同能源管理项目 (项目编号:JTGP202609001C)竞争性磋商公告", "招标采购"),
            ("某医院输液泵采购项目(项目编号:X1)合同公告", "合同签订"),
            ("某项目中标结果公告", "中标成交"),
            ("某项目中标结果终止公告", "终止废标"),      # 尾词优先:终止是本公告类型
            ("某项目招标文件更正公告", "更正变更"),
            ("关于某项目拟采用单一来源采购方式的公示", "单一来源"),
            ("天津医科大学总医院政府采购意向公告", "采购意向"),
            ("政府采购投诉处理决定公告(津财采投决[2026]50号)", "投诉处理"),
            ("面向中小企业预留项目执行情况公告", "执行情况"),
            ("某设备采购项目征求意见公告", "需求公示"),
            ("完全无关的一句话", "其他"),
        ]
        for title, expect in cases:
            self.assertEqual(parse_tender_meta(title)["stage"], expect, title)

    def test_opportunity_semantics(self):
        """商机语义(企业投标视角):进行中可投标/已关闭是竞争情报/其余非商机。"""
        cases = [
            ("某医院信息化采购项目公开招标公告", "商机进行中"),
            ("某医院政府采购意向公告", "商机进行中"),
            ("某项目招标文件更正公告", "商机进行中"),
            ("某项目中标公告", "商机已关闭"),
            ("某项目合同公告", "商机已关闭"),
            ("关于某项目拟采用单一来源采购方式的公示", "非商机"),  # 无竞争机会
            ("某项目终止公告", "非商机"),
            ("政府采购投诉处理决定公告", "非商机"),
            ("强化数据思维 提升监督质效", "非商机"),               # 新闻
        ]
        for title, expect in cases:
            self.assertEqual(parse_tender_meta(title)["opportunity"], expect, title)

    def test_project_no_and_buyer(self):
        m = parse_tender_meta(
            "天津市宝坻区人民医院天津市宝坻区人民医院输液泵采购项目"
            "(项目编号:宝坻区〔2026〕414278号)合同公告")
        self.assertEqual(m["project_no"], "宝坻区〔2026〕414278号")
        self.assertEqual(m["buyer"], "天津市宝坻区人民医院")   # 前缀重复形态
        m2 = parse_tender_meta("没有任何编号和重复前缀的公告")
        self.assertEqual((m2["project_no"], m2["buyer"]), ("", ""))

    def test_content_enriched_in_adapter_items(self):
        r = TianjinZfcgAdapter(http_get=ok_transport(TJ_OK)).search("医院", 10)
        self.assertIn("阶段:招标采购", r.items[0]["content"])
        self.assertIn("项目编号:XCSD-2026-C-293", r.items[0]["content"])
        EvidenceItemLite.model_validate(r.items[0])           # 契约仍成立


class TestTianjinRegistrationAndRouting(unittest.TestCase):
    def test_registered_in_default_registry(self):
        egress = EgressHttpClient(transport=lambda u, t: (200, TJ_OK))
        registry = build_default_registry(egress)
        self.assertIn("tianjin_zfcg", registry.platforms())
        self.assertNotIn("tianjin_zfcg", registry.defaults())  # 按需启用,不进默认扇出

    def test_router_r6_before_r1(self):
        router = ToolRouter(make_domain().platform_rules)
        req = router.route("查询该项目的中标结果公告", ["中标公告"], "claim")
        self.assertEqual(req.platforms, ["ccgp", "tianjin_zfcg"])
        # 回归:泛官方词仍走 R1,不被 R6 抢走(cctv 2026-10-16 起移出默认路由)
        req1 = router.route("官方公告与批复", ["公告"], "")
        self.assertIn("official_website", req1.platforms)
        # 回归:历史基线工具路由优先于平台规则
        reqh = router.route("历史中标方与金额分布", ["历史中标"], "")
        self.assertEqual(reqh.tool, "history_baseline")

    def test_lineage_folds_into_ccgp_root(self):
        domain = make_domain()
        self.assertEqual(domain.upstream_root("www.ccgp-tianjin.gov.cn"), "ccgp")
        self.assertEqual(domain.credibility_of("ccgp"), "高")       # 权威根:单源例外资格
        self.assertIn("ccgp", domain.authoritative_roots)


if __name__ == "__main__":
    unittest.main()
