"""ccgp 适配器:静态列表解析/地域与采购人/详情补抓(预算/截止)/8态 + 注册路由。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_domain  # noqa: E402
from sense2act.collect.rules import ToolRouter  # noqa: E402
from sense2act.crawler import (  # noqa: E402
    CcgpAdapter,
    EgressHttpClient,
    LocalCollectService,
    build_default_registry,
)
from sense2act.models.observation import CollectRequest, EvidenceItemLite  # noqa: E402

LOCAL_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index.htm"
CENTRAL_URL = "https://www.ccgp.gov.cn/cggg/zygg/gkzb/index.htm"
DETAIL_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261004_27439617.htm"

# 结构取自 2026-10-04 实抓列表页:ul.c_list_bid > li,锚点带完整 title,
# li 文本含 发布时间/地域/采购人 三组 <em>
LIST_LOCAL = """
<html><body><ul class="c_list_bid">
<li>
 <a href="./202610/t20261004_27439618.htm" target="_blank"
    title="吉林市政府采购中心关于吉林市城市照明建设管理中心购置2026年主城区市管有路无灯街路新建路灯灯杆及配套设施项目的公开招标公告">吉林市政府采购中心关于吉林市城市照明…</a>
 发布时间：<em>2026-10-04 14:23</em> 地域：<em>吉林</em> 采购人：<em>吉林市城市照明建设管理中心</em>
</li>
<li>
 <a href="./202610/t20261004_27439617.htm" target="_blank"
    title="某市第一医院医疗设备采购项目公开招标公告">某市第一医院医疗设备采购项目…</a>
 发布时间：<em>2026-10-04 10:00</em> 地域：<em>浙江</em> 采购人：<em>某市第一医院</em>
</li>
</ul></body></html>"""

# 中央栏目含一条与地方重复的公告(同 id)——验证跨栏目去重
LIST_CENTRAL = """
<html><body><ul class="c_list_bid">
<li>
 <a href="./202610/t20261004_27439617.htm" target="_blank"
    title="某市第一医院医疗设备采购项目公开招标公告">某市第一医院医疗设备采购项目…</a>
 发布时间：<em>2026-10-04 10:00</em> 地域：<em>浙江</em> 采购人：<em>某市第一医院</em>
</li>
</ul></body></html>"""

# 字段取自 2026-10-04 实抓详情页(项目编号/预算/开标/代理/联系人/获取文件时间)
DETAIL_OK = """
<html><body><div>
项目编号： 采购计划-[2026]-05669号-J2026ZCY283 项目名称： 某市第一医院医疗设备采购项目
预算金额 ￥203.000000万元（人民币） 联系人及联系方式： 项目联系人 李国军 项目联系电话 （0432）62048505
采购单位 某市第一医院 行政区域 浙江省 公告时间 2026年10月04日 10:00
代理机构名称 某市政府采购中心 代理机构地址 某市公共资源交易中心1009室
获取招标文件时间 2026年10月04日至2026年10月13日 每日上午08:30至12:00
开标时间 2026年10月28日 08:35 开标地点 某市公共资源交易中心8楼开标区
</div></body></html>"""

DETAIL_WITH_BODY = """
<html><body><nav>网站导航</nav><div class="vF_detail_main">
项目编号： 采购计划-[2026]-05669号-J2026ZCY283
预算金额 ￥203.000000万元（人民币）
开标时间 2026年10月28日 08:35
<div class="vF_detail_content"><div id="noticeArea">
<style>.hidden { display: none }</style>
<h3>项目概况</h3>
<p>医疗设备采购需求：采购超声诊断设备一套。</p>
<p>资格要求：供应商须具备医疗器械经营许可。</p>
<script>伪造正文</script>
</div><div class="related">相关附件：无关下载</div></div>
</div></body></html>"""

LIST_CHANGED = "<html><div>改版了</div></html>"
LIST_BLOCKED = "<html><title>频繁访问!</title>您的访问过于频繁,请稍后再试。</html>"


def routed_transport(routes: dict[str, tuple[int, str]]):
    """按 URL 路由的传输替身;未注册 URL 返回 404。"""
    def http_get(url, timeout=None):
        class R:
            pass
        status, text = routes.get(url, (404, ""))
        r = R()
        r.status_code = status
        r.text = text
        return r
    return http_get


OK_ROUTES = {LOCAL_URL: (200, LIST_LOCAL), CENTRAL_URL: (200, LIST_CENTRAL),
             DETAIL_URL: (200, DETAIL_OK)}


class TestCcgpAdapter(unittest.TestCase):
    def test_sweep_fields_and_cross_column_dedup(self):
        ad = CcgpAdapter(http_get=routed_transport(OK_ROUTES))
        items = ad.sweep()
        self.assertEqual(len(items), 2)               # 跨栏目同 id 去重
        hospital = next(i for i in items if "医院" in i["title"])
        self.assertEqual(hospital["province"], "浙江")       # 列表自带地域
        self.assertEqual(hospital["buyer"], "某市第一医院")   # 列表自带采购人
        self.assertEqual(hospital["stage"], "招标采购")
        self.assertEqual(hospital["opportunity"], "商机进行中")
        self.assertEqual(hospital["dedup_key"], "ccgp:27439617")
        self.assertIn("2026-10-04T02:00:00", hospital["published_at"])  # 10:00 京时 -> UTC

    def test_search_detail_enrichment_and_contract(self):
        ad = CcgpAdapter(http_get=routed_transport(OK_ROUTES))
        r = ad.search("医院", 10)
        self.assertEqual(r.status, "success")
        self.assertEqual(len(r.items), 1)
        item = r.items[0]
        # 详情补抓:7 字段全部折进 content(此前只折 3 字段,代理/联系人/电话/获取文件时间被丢)
        self.assertIn("阶段:招标采购", item["content"])
        self.assertIn("预算:203万元", item["content"])
        self.assertIn("投标截止:2026-10-28 08:35", item["content"])
        self.assertIn("项目编号:采购计划-[2026]-05669号-J2026ZCY283", item["content"])
        self.assertIn("代理机构:某市政府采购中心", item["content"])
        self.assertIn("联系人:李国军", item["content"])
        self.assertIn("电话:（0432）62048505", item["content"])
        self.assertIn("获取文件截止:2026-10-13", item["content"])
        # 契约纪律:rich 字段(省份/采购人等)不得流入 EvidenceItemLite(extra=forbid)
        EvidenceItemLite.model_validate(item)
        self.assertNotIn("province", item)

    def test_detail_body_preserves_paragraphs_and_fields(self):
        routes = {**OK_ROUTES, DETAIL_URL: (200, DETAIL_WITH_BODY)}
        result = CcgpAdapter(http_get=routed_transport(routes)).search("医院", 10)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.items), 1)
        item = EvidenceItemLite.model_validate(result.items[0])
        self.assertEqual(item.source, "www.ccgp.gov.cn")
        self.assertEqual(item.dedup_key, "ccgp:27439617")
        self.assertIn("某市第一医院医疗设备采购项目公开招标公告", item.content)
        self.assertIn("阶段:招标采购", item.content)
        self.assertIn("预算:203万元", item.content)
        self.assertIn("投标截止:2026-10-28 08:35", item.content)
        self.assertIn("项目编号:采购计划-[2026]-05669号-J2026ZCY283", item.content)
        self.assertIn("项目概况\n医疗设备采购需求：采购超声诊断设备一套。\n资格要求：供应商须具备医疗器械经营许可。", item.content)
        for unwanted in ("网站导航", "伪造正文", "无关下载", "display: none"):
            self.assertNotIn(unwanted, item.content)

    def test_detail_body_falls_back_to_verified_container(self):
        detail = """<html><body><nav>站点导航</nav>
        <div class="vF_detail_content"><p>一、采购需求</p>
        <p>采购医用设备一套。</p><aside>相关推荐</aside></div>
        </body></html>"""
        routes = {**OK_ROUTES, DETAIL_URL: (200, detail)}
        item = CcgpAdapter(http_get=routed_transport(routes)).search("医院", 10).items[0]
        self.assertIn("一、采购需求\n采购医用设备一套。", item["content"])
        self.assertNotIn("站点导航", item["content"])
        self.assertNotIn("相关推荐", item["content"])

    def test_empty_or_changed_body_does_not_invent_text(self):
        cases = (
            '<div id="noticeArea"><style>无正文</style></div>',
            '<html><body><div class="new-layout">无关页面</div></body></html>',
        )
        for detail in cases:
            with self.subTest(detail=detail):
                routes = {**OK_ROUTES, DETAIL_URL: (200, detail)}
                item = CcgpAdapter(http_get=routed_transport(routes)).search("医院", 10).items[0]
                self.assertEqual(item["content"],
                                 "某市第一医院医疗设备采购项目公开招标公告 | 阶段:招标采购")

    def test_detail_access_failure_degrades_without_body(self):
        for code, detail in ((403, ""), (429, ""),
                             (200, '<div id="noticeArea">验证码后显示正文</div>')):
            with self.subTest(code=code, detail=detail):
                routes = {**OK_ROUTES, DETAIL_URL: (code, detail)}
                item = CcgpAdapter(http_get=routed_transport(routes)).search("医院", 10).items[0]
                self.assertEqual(item["content"],
                                 "某市第一医院医疗设备采购项目公开招标公告 | 阶段:招标采购")
        def http_get(url, timeout=None):
            if url == DETAIL_URL:
                raise TimeoutError("detail timeout")
            return routed_transport(OK_ROUTES)(url, timeout)
        item = CcgpAdapter(http_get=http_get).search("医院", 10).items[0]
        self.assertEqual(item["content"],
                         "某市第一医院医疗设备采购项目公开招标公告 | 阶段:招标采购")

    def test_detail_cap_limits_body_fetches(self):
        other_url = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261004_27439618.htm"
        calls = []
        routes = {**OK_ROUTES, other_url: (200, DETAIL_WITH_BODY)}
        def http_get(url, timeout=None):
            calls.append(url)
            return routed_transport(routes)(url, timeout)
        result = CcgpAdapter(http_get=http_get, detail_cap=1, max_pages=1).search("市", 10)
        self.assertEqual(len(result.items), 2)
        self.assertEqual([url for url in calls if url not in (LOCAL_URL, CENTRAL_URL)],
                         [other_url])
        self.assertIn("医疗设备采购需求", result.items[0]["content"])
        self.assertNotIn("医疗设备采购需求", result.items[1]["content"])
        self.assertEqual(len({item["dedup_key"] for item in result.items}), 2)

    def test_zero_detail_cap_keeps_evidence_contract(self):
        calls = []
        def http_get(url, timeout=None):
            calls.append(url)
            return routed_transport(OK_ROUTES)(url, timeout)
        result = CcgpAdapter(http_get=http_get, detail_cap=0, max_pages=1).search("医院", 10)
        self.assertEqual(result.status, "success")
        self.assertEqual(calls, [LOCAL_URL, CENTRAL_URL])
        self.assertEqual(EvidenceItemLite.model_validate(result.items[0]).dedup_key,
                         "ccgp:27439617")

    def test_body_reaches_local_collect_service_model(self):
        routes = {**OK_ROUTES, DETAIL_URL: (200, DETAIL_WITH_BODY)}
        egress = EgressHttpClient(transport=lambda url, timeout: routes.get(url, (404, "")))
        service = LocalCollectService(build_default_registry(egress), egress=egress)
        receipt = service.send(CollectRequest(query="医院", platforms=["ccgp"], limit=10))
        self.assertEqual(receipt.status, "success")
        self.assertEqual(len(receipt.items), 1)
        self.assertIsInstance(receipt.items[0], EvidenceItemLite)
        self.assertIn("资格要求：供应商须具备医疗器械经营许可。",
                      receipt.items[0].content)
        self.assertEqual(receipt.items[0].dedup_key, "ccgp:27439617")

    def test_precise_match_buyer_excludes_unrelated(self):
        """定向检索:有采购人时精确命中,排除宽词命中的无关公告(瞄准失灵修复)。"""
        ad = CcgpAdapter(http_get=routed_transport(OK_ROUTES))
        # 宽词"医院"会命中医院条目;但带 buyer 时只精确命中该采购人
        r = ad.search("医院 采购 设备", 10, buyer="某市第一医院")
        self.assertEqual(r.status, "success")
        self.assertEqual(len(r.items), 1)
        self.assertEqual(r.items[0]["title"], "某市第一医院医疗设备采购项目公开招标公告")
        # 无关采购人(吉林照明)即使标题含"采购"也不进结果
        r2 = ad.search("医院 采购", 10, buyer="某市第一医院")
        self.assertNotIn("吉林", " ".join(i["title"] for i in r2.items))

    def test_precise_match_project_no_miss_honest_empty(self):
        """定向检索:项目编号未命中时如实 empty,不退回宽词伪装成功。"""
        ad = CcgpAdapter(http_get=routed_transport(OK_ROUTES))
        r = ad.search("医院", 10, project_no="SHGP-2026-A536")
        self.assertEqual(r.status, "empty")
        self.assertIn("未命中", r.message)

    def test_pagination_backtrack(self):
        """翻页回溯:max_pages>1 时翻 index_2 拿更早一页的公告。"""
        p2 = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index_2.htm"
        p2_html = """
<html><body><ul class="c_list_bid">
<li><a href="./202610/t20261003_27439599.htm" target="_blank"
    title="某市第一医院旧批次设备采购项目公开招标公告">某市第一医院旧批次…</a>
 发布时间：<em>2026-10-03 09:00</em> 地域：<em>浙江</em> 采购人：<em>某市第一医院</em>
</li></ul></body></html>"""
        routes = dict(OK_ROUTES)
        routes[p2] = (200, p2_html)
        ad = CcgpAdapter(http_get=routed_transport(routes), max_pages=2)
        r = ad.search("医院", 10, buyer="某市第一医院")
        self.assertEqual(r.status, "success")
        # index_2 的旧批次公告也进来了(翻页回溯命中)
        titles = {i["title"] for i in r.items}
        self.assertIn("某市第一医院旧批次设备采购项目公开招标公告", titles)

    def test_status_mapping(self):
        cases = [(429, "rate_limited"), (403, "authentication_required"),
                 (502, "network_error")]
        for code, expect in cases:
            routes = {u: (code, "") for u in (LOCAL_URL, CENTRAL_URL)}
            ad = CcgpAdapter(http_get=routed_transport(routes))
            self.assertEqual(ad.search("q", 10).status, expect, code)

    def test_block_page_and_parser_changed(self):
        routes = {u: (200, LIST_BLOCKED) for u in (LOCAL_URL, CENTRAL_URL)}
        self.assertEqual(
            CcgpAdapter(http_get=routed_transport(routes)).search("q", 10).status,
            "authentication_required")
        routes2 = {u: (200, LIST_CHANGED) for u in (LOCAL_URL, CENTRAL_URL)}
        self.assertEqual(
            CcgpAdapter(http_get=routed_transport(routes2)).search("q", 10).status,
            "parser_changed")

    def test_blank_and_generic_query_rejected(self):
        ad = CcgpAdapter(http_get=routed_transport(OK_ROUTES))
        for q in ("", "   "):
            r = ad.search(q, 10)
            self.assertEqual(r.status, "empty")
            self.assertIn("拒绝全量", r.message)
        rg = ad.search("采购 公告", 10)
        self.assertEqual(rg.status, "empty")
        self.assertIn("类别词", rg.message)


class TestCcgpRegistrationAndRouting(unittest.TestCase):
    def test_registered_and_routed(self):
        egress = EgressHttpClient(transport=lambda u, t: (200, LIST_LOCAL))
        registry = build_default_registry(egress)
        self.assertIn("ccgp", registry.platforms())
        self.assertNotIn("ccgp", registry.defaults())     # 按需启用(R6)
        router = ToolRouter(make_domain().platform_rules)
        req = router.route("查询该项目的中标结果公告", ["中标公告"], "claim")
        self.assertEqual(req.platforms, ["ccgp", "tianjin_zfcg"])
        domain = make_domain()
        self.assertEqual(domain.upstream_root("www.ccgp.gov.cn"), "ccgp")
        self.assertEqual(domain.credibility_of("ccgp"), "高")   # 权威根


class TestCcgpSourceUrlFetch(unittest.TestCase):
    """信封自带目标公告 URL 直抓(发现三,2026-10-16):列表盲扫够不着的
    大龄公告由它负责;直抓失败/非本站回退列表扫查(不伪装)。"""

    TARGET_URL = ("https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/"
                  "t20261011_27467046.htm")
    TARGET_PAGE = (
        "<html><head><title>中国历史研究院《（新编）中国通史》纂修工程"
        "出版服务项目公开招标公告_中国政府采购网</title></head><body><div>"
        "项目编号： HCZB-2026-ZB0931 "
        "预算金额 ￥1684.000000万元（人民币） "
        "代理机构名称 华采招标集团有限公司 代理机构地址 北京市某区某路1号 "
        "项目联系人 王某 项目联系电话 010-12345678 "
        "投标截止时间：2026年11月02日 13:30 "
        "发布时间： 2026-10-11 09:30"
        "</div></body></html>")

    def test_direct_fetch_returns_single_enriched_item(self):
        result = CcgpAdapter(http_get=routed_transport(
            {self.TARGET_URL: (200, self.TARGET_PAGE)})).search(
            "q", 10, buyer="中国历史研究院", source_url=self.TARGET_URL)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(
            item["title"],
            "中国历史研究院《（新编）中国通史》纂修工程出版服务项目公开招标公告")
        self.assertEqual(item["dedup_key"], "ccgp:27467046")
        self.assertEqual(item["published_at"][:10], "2026-10-11")
        self.assertIn("预算:1684万元", item["content"])
        self.assertIn("投标截止:2026-11-02 13:30", item["content"])
        self.assertIn("代理机构:华采招标集团有限公司", item["content"])
        # 契约投影:rich 字段(budget_yuan 等)不得流入 S3 回执(extra=forbid)
        EvidenceItemLite.model_validate(item)

    def test_direct_fetch_skips_list_sweep(self):
        """命中直抓后跳过列表盲扫:全程恰 1 次出网。"""
        calls: list[str] = []

        def counting_get(url, timeout=None):
            calls.append(url)
            return routed_transport(
                {self.TARGET_URL: (200, self.TARGET_PAGE)})(url)

        CcgpAdapter(http_get=counting_get).search(
            "q", 10, source_url=self.TARGET_URL)
        self.assertEqual(calls, [self.TARGET_URL])

    def test_non_ccgp_url_not_consumed_falls_back(self):
        """非本站 URL 不消费,回退列表扫查(适配器只认自家域名)。"""
        result = CcgpAdapter(http_get=routed_transport(OK_ROUTES)).search(
            "医院", 10, source_url="https://example.com/notice.htm")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.items[0]["dedup_key"], "ccgp:27439617")

    def test_direct_fetch_404_falls_back_to_sweep(self):
        """直抓 404 -> 回退列表扫查(egress 已录失败请求,诚实降级)。"""
        result = CcgpAdapter(http_get=routed_transport(OK_ROUTES)).search(
            "医院", 10, source_url=self.TARGET_URL)   # 未注册 -> 404
        self.assertEqual(result.status, "success")
        self.assertEqual(result.items[0]["dedup_key"], "ccgp:27439617")

    def test_direct_fetch_challenge_page_honest_block(self):
        """直抓遇拦截页 -> authentication_required,不对抗。"""
        result = CcgpAdapter(http_get=routed_transport(
            {self.TARGET_URL: (200, LIST_BLOCKED)})).search(
            "q", 10, source_url=self.TARGET_URL)
        self.assertEqual(result.status, "authentication_required")


if __name__ == "__main__":
    unittest.main()
