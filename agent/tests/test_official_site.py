"""通用企业官网适配器:名称路由/启发式条目/trafilatura 详情补抓/认识论配置。"""
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
    OfficialSiteAdapter,
    build_default_registry,
)
from sense2act.models.observation import EvidenceItemLite  # noqa: E402

BASE = "https://www.xmfh.com.cn"
LIST_URL = BASE + "/yydt/xwgg.htm"
DETAIL_URL = BASE + "/info/1724/17031.htm"

# 结构取自 2026-10-04 实抓列表页:info/栏目/N.htm 锚点带完整 title
LIST_OK = """
<html><body><ul class="clearfix">
<li><a href="../info/1724/17031.htm" target="_blank"
   title="夯实分级诊疗根基 聚力医疗服务提质——我院召开2026年托管医院上半年工作汇报会">
   <div class="images-txt"><p class="clamp">夯实分级诊疗根基...</p></div></a></li>
<li><a href="../info/1724/16726.htm" target="_blank"
   title="喜报｜以核医之光点亮精准医疗新篇章,我院陈皓鋆荣获2026年度称号">
   <div class="images-txt"><p class="clamp">喜报｜以核医之光...</p></div></a></li>
</ul>
<a href="/yydt.htm">医院动态</a> <a href="/ksdh/nkzx.htm">内科</a>
</body></html>"""

# trafilatura 可解析的详情页(meta pubdate + 正文段)
DETAIL_OK = """
<html><head><title>夯实分级诊疗根基-厦门大学附属第一医院</title>
<meta name="pubdate" content="2026-09-10"/></head>
<body><div class="header">导航 首页 关于我们 就医服务</div>
<div class="article"><h1>夯实分级诊疗根基 聚力医疗服务提质</h1>
<p>2026年8月,为贯彻落实市政府卫生健康领域建设相关工作精神,深化探索城市医疗集团体制机制建设,厦门大学附属第一医院党委书记主持召开2026年托管医院上半年工作汇报会,各托管医院领导班子成员及相关职能部门负责人出席本次会议。</p>
<p>会议听取了各托管医院围绕医疗质量、运营管理、学科建设等方面的汇报,并对下半年重点工作作出部署。</p>
</div><div class="footer">版权所有 电话</div></body></html>"""

NO_ITEMS = '<html><body><a href="/about.htm">关于我们</a></body></html>'

PROFILES = {"厦门大学附属第一医院": {
    "domains": ["www.xmfh.com.cn"], "aliases": ["厦大附一"],
    "news_paths": ["/yydt/xwgg.htm"], "item_xpath": None}}


def ok_transport(routes: dict[str, str], default: str = "<html></html>"):
    def http_get(url, timeout=None):
        class R:
            pass
        r = R()
        r.status_code = 200
        r.text = routes.get(url, default)
        return r
    return http_get


ROUTES = {LIST_URL: LIST_OK, DETAIL_URL: DETAIL_OK}


class TestOfficialSiteAdapter(unittest.TestCase):
    def test_route_by_name_and_alias(self):
        ad = OfficialSiteAdapter(http_get=ok_transport(ROUTES), profiles=PROFILES)
        r = ad.search("厦大附一 分级诊疗", 10)         # 别名路由
        self.assertEqual(r.status, "success")
        miss = ad.search("未知公司 最新动态", 10)
        self.assertEqual(miss.status, "empty")
        self.assertIn("未命中已配置", miss.message)

    def test_topic_filter_and_latest_semantics(self):
        ad = OfficialSiteAdapter(http_get=ok_transport(ROUTES), profiles=PROFILES)
        r = ad.search("厦门大学附属第一医院 分级诊疗", 10)   # 主题词过滤
        self.assertEqual(len(r.items), 1)
        self.assertIn("分级诊疗", r.items[0]["title"])
        r2 = ad.search("厦门大学附属第一医院", 10)          # 仅企业名=最新公告
        self.assertEqual(r2.status, "success")
        self.assertEqual(len(r2.items), 2)

    def test_detail_enrichment_via_trafilatura(self):
        ad = OfficialSiteAdapter(http_get=ok_transport(ROUTES), profiles=PROFILES)
        r = ad.search("厦门大学附属第一医院 分级诊疗", 10)
        item = r.items[0]
        self.assertEqual(item["source"], "www.xmfh.com.cn")
        self.assertEqual(item["author_type"], "当事方")
        self.assertIn("托管医院上半年工作汇报会", item["content"])   # 正文级
        self.assertIn("2026-09-10", item["published_at"])            # meta 日期
        EvidenceItemLite.model_validate(item)                        # 契约
        self.assertNotIn("company", item)

    def test_no_items_honest_empty(self):
        ad = OfficialSiteAdapter(http_get=ok_transport({LIST_URL: NO_ITEMS}),
                                 profiles=PROFILES)
        r = ad.search("厦大附一", 10)
        self.assertEqual(r.status, "empty")
        self.assertIn("未识别出", r.message)

    def test_status_mapping(self):
        def boom_routes(url, timeout=None):
            class R:
                pass
            r = R()
            r.status_code = 403
            r.text = ""
            return r
        ad = OfficialSiteAdapter(http_get=boom_routes, profiles=PROFILES)
        self.assertEqual(ad.search("厦大附一", 10).status,
                         "authentication_required")


class TestOfficialSiteRegistration(unittest.TestCase):
    def test_registered_and_fills_r1_gap(self):
        egress = EgressHttpClient(transport=lambda u, t: (200, LIST_OK))
        registry = build_default_registry(egress)
        self.assertIn("official_website", registry.platforms())
        router = ToolRouter(make_domain().platform_rules)
        req = router.route("官方公告与批复", ["公告"], "")    # R1 缺口补上
        self.assertIn("official_website", req.platforms)
        # cctv_news 2026-10-16 起移出默认路由(不对题),见 platform_rules.json R1 note
        self.assertNotIn("cctv_news", req.platforms)

    def test_company_web_lineage_mid_tier(self):
        domain = make_domain()
        self.assertEqual(domain.upstream_root("www.xmfh.com.cn"), "company_web")
        self.assertEqual(domain.credibility_of("company_web"), "中")
        self.assertNotIn("company_web", domain.authoritative_roots)  # 无单源例外


if __name__ == "__main__":
    unittest.main()
