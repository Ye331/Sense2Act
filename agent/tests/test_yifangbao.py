"""乙方宝适配器:首页区块/zbzt 频道解析/WAF 挑战识别/聚合源认识论配置。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_domain  # noqa: E402
from sense2act.crawler import (  # noqa: E402
    EgressHttpClient,
    YifangbaoAdapter,
    build_default_registry,
)
from sense2act.models.observation import EvidenceItemLite  # noqa: E402

BASE = "https://www.yfbzb.com"
CHANNEL = "/zbzt/41/3/"
CHANNEL_URL = BASE + CHANNEL

# 结构取自 2026-10-04 实抓首页:四区块 bids-list(id 即公告大类)
HOME_OK = """
<html><body>
<ul class="bids-list" id="invitedBidList">
 <li><a href="/inviteBid/detail/20261004_636187717.html" class="bid-title"
        data-content-id="636187717" title="成峯天萃东府项目前期物业服务招标公告">
     成峯天萃东府项目前期物业服务招标公告</a>
     <time class="bid-date" datetime="2026-10-04">2026-10-04</time></li>
 <li><a href="/inviteBid/detail/20261004_636187716.html" class="bid-title"
        title="某市第一医院医疗设备采购项目公开招标公告">
     某市第一医院医疗设备采购项目公开招标公告</a>
     <time class="bid-date" datetime="2026-10-04">2026-10-04</time></li>
</ul>
<ul class="bids-list" id="winBidList">
 <li><a href="/winbid/detail/20261004_636187727.html" class="bid-title"
        title="员工补充医疗保险集中采购项目中选候选人公示">
     员工补充医疗保险集中采购项目中选候选人公示</a>
     <time class="bid-date" datetime="2026-10-04">2026-10-04</time></li>
</ul>
<!-- 登录表单含"验证码"placeholder:合法存在,不得误判为 WAF 挑战(实测踩坑) -->
<div class="login"><input name="checkcode" placeholder="请输入验证码" maxlength="6"/></div>
</body></html>"""

# 结构取自 2026-10-04 实抓频道页:search-bottom-item(标题含高亮 font 标签)
CHANNEL_OK = """
<html><body><ul>
<li class="search-bottom-item ">
 <a class="border-wrapper" href="/inviteBid/detail/20261004_636198004.html" target="_blank">
  <h2>连江县贵安新天地幼儿园政府<font color='#ff0000'>采购</font>意向</h2> </a>
 <p>...政府采购意向公告 连江县贵安新天地幼儿园政府采购意向 ...</p>
 <div class="time-type">
  <img src="/x.png"/><span>招标预告</span>
  <img src="/y.png"/><span>福建</span>
  <img src="/z.png"/><span>2026-10-04</span>
 </div>
</li>
</ul></body></html>"""

WAF_CHALLENGE = '<html><script>SafeLineChallenge("x")</script></html>'
CHANGED = "<html><div>改版了</div></html>"


def ok_transport(routes: dict[str, str]):
    def http_get(url, timeout=None):
        class R:
            pass
        r = R()
        r.status_code = 200
        r.text = routes.get(url, "<html></html>")
        return r
    return http_get


class TestYifangbaoAdapter(unittest.TestCase):
    def test_home_sections_stage_and_search(self):
        ad = YifangbaoAdapter(http_get=ok_transport({BASE: HOME_OK}))
        items = ad.sweep()
        self.assertEqual(len(items), 3)
        stages = {i["dedup_key"]: i["stage"] for i in items}
        self.assertEqual(stages["yifangbao:636187717"], "招标采购")   # 区块名优先
        self.assertEqual(stages["yifangbao:636187727"], "中标成交")
        hospital = ad.search("医院", 10)
        self.assertEqual(hospital.status, "success")
        self.assertEqual(len(hospital.items), 1)
        item = hospital.items[0]
        self.assertEqual(item["source"], "www.yfbzb.com")
        self.assertEqual(item["author_type"], "聚合平台")
        self.assertIn("2026-10-03T16:00:00", item["published_at"])  # 京时零点->UTC
        EvidenceItemLite.model_validate(item)              # 契约(rich 字段已剥离)
        self.assertNotIn("province", item)

    def test_channel_region_date_and_highlight_strip(self):
        ad = YifangbaoAdapter(http_get=ok_transport({CHANNEL_URL: CHANNEL_OK}),
                              channels=(CHANNEL,))
        items = ad.sweep()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["title"], "连江县贵安新天地幼儿园政府采购意向")  # 剥离高亮
        self.assertEqual(it["province"], "福建")
        self.assertEqual(it["stage"], "采购意向")
        self.assertIn("地区:福建", it["content"])
        self.assertIn("2026-10-03T16:00:00", it["published_at"])

    def test_waf_challenge_and_parser_changed(self):
        routes = {BASE: WAF_CHALLENGE}
        self.assertEqual(
            YifangbaoAdapter(http_get=ok_transport(routes)).search("q", 10).status,
            "authentication_required")
        self.assertEqual(
            YifangbaoAdapter(http_get=ok_transport({BASE: CHANGED}))
            .search("q", 10).status, "parser_changed")

    def test_blank_query_rejected(self):
        ad = YifangbaoAdapter(http_get=ok_transport({BASE: HOME_OK}))
        r = ad.search("", 10)
        self.assertEqual(r.status, "empty")
        self.assertIn("拒绝全量", r.message)


class TestYifangbaoEpistemics(unittest.TestCase):
    def test_registered_but_not_in_r6(self):
        egress = EgressHttpClient(transport=lambda u, t: (200, HOME_OK))
        registry = build_default_registry(egress)
        self.assertIn("yifangbao", registry.platforms())
        self.assertNotIn("yifangbao", registry.defaults())
        import json
        rules = json.loads((Path(__file__).resolve().parent.parent /
                            "data/config/platform_rules.json").read_text(
                                encoding="utf-8-sig"))
        r6 = next(r for r in rules["platform_rules"] if r["id"] == "R6")
        self.assertNotIn("yifangbao", r6["platforms"])   # 防与 ccgp 独立性双计

    def test_tier_mid_not_authoritative(self):
        domain = make_domain()
        self.assertEqual(domain.credibility_of("www.yfbzb.com"), "中")
        self.assertNotIn("www.yfbzb.com", domain.authoritative_roots)


if __name__ == "__main__":
    unittest.main()
