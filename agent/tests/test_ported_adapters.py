"""移植适配器:toutiao / south_weekend / sogou_wechat 的解析与挑战识别。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.crawler import (  # noqa: E402
    SogouWechatAdapter,
    SouthWeekendAdapter,
    ToutiaoAdapter,
)

TOUTIAO_OK = """
<html><body><div class="s-result-list">
 <div class="cs-card">
  <a class="text-underline-hover" href="/jump?url=https%3A%2F%2Fwww.toutiao.com%2Fa1730000000001%2F">医院采购AI系统</a>
  <span class="text-underline-hover">某院启动AI辅助诊断采购,预算480万元</span>
  <div class="cs-source-content"><span class="text-ellipsis">科技新知</span><span class="text-ellipsis">2026-09-20</span></div>
 </div>
 <div class="cs-card">
  <a class="text-underline-hover" href="https://www.example.com/news/2">第二条</a>
  <span class="text-underline-hover">新能源电池产能扩张</span>
  <div class="cs-source-content"><span class="text-ellipsis">3天前</span></div>
 </div>
</div></body></html>"""
TOUTIAO_CHALLENGE = "<html>安全验证 请完成验证码</html>"
TOUTIAO_CHANGED = "<html><div>别的页面</div></html>"

SW_OK = """
<html><body><div class="nfzm-panel--list "><ul class="nfzm-list ui-line">
 <li><a href="/contents/112345">x</a>
  <div class="nfzm-content-item__title"><h5>南方周末调查报道</h5></div>
  <div class="nfzm-content-item__description">深度内容摘要</div>
  <div class="nfzm-content-item__meta"><span>经济</span><span>2026-09-19</span></div></li>
</ul></div></body></html>"""
SW_CHANGED = "<html><div>改版了</div></html>"
SW_EMPTY = "<html><div>暂无搜索结果</div></html>"

SG_OK = """
<html><body><div id="wrapper"><div class="txt-box">
 <h3><a uigs="article_title_1" href="/link?url=x">微信公众号文章标题</a></h3>
 <script>timeConvert('1727000000')</script>
 <div class="s-p"><span class="all-time-y2">某账号：</span></div>
 <div class="txt-info">这是摘要内容</div>
</div></div></body></html>"""
SG_ANTISPIDER = "<html><div>antispider 请输入验证码</div></html>"
SG_EMPTY = '<html><div id="wrapper">没有找到相关结果</div></html>'


def ok_transport(pages_by_marker):
    """按标记返回 200 页面的替身。"""
    def http_get(url, timeout=None):
        class R:
            status_code = 200
            text = pages_by_marker
        return R()
    return http_get


class TestToutiao(unittest.TestCase):
    def test_parse_jump_unwrap_and_time(self):
        ad = ToutiaoAdapter(http_get=ok_transport(TOUTIAO_OK))
        r = ad.search("医院 AI", 10)
        self.assertEqual(r.status, "success")
        self.assertEqual(len(r.items), 2)
        first = r.items[0]
        self.assertEqual(first["url"], "https://www.toutiao.com/a1730000000001/")
        self.assertEqual(first["dedup_key"], "toutiao:1730000000001")
        # 北京 09-20 零点 -> UTC 09-19T16:00(时区归一是正确行为)
        self.assertIn("2026-09-19T16:00:00", first["published_at"])
        second = r.items[1]
        self.assertTrue(second["published_at"])          # 相对时间"3天前"可解析

    def test_challenge_and_changed(self):
        self.assertEqual(
            ToutiaoAdapter(http_get=ok_transport(TOUTIAO_CHALLENGE))
            .search("q", 10).status, "authentication_required")
        self.assertEqual(
            ToutiaoAdapter(http_get=ok_transport(TOUTIAO_CHANGED))
            .search("q", 10).status, "parser_changed")


class TestSouthWeekend(unittest.TestCase):
    def test_parse_panel(self):
        ad = SouthWeekendAdapter(http_get=ok_transport(SW_OK))
        r = ad.search("调查", 10)
        self.assertEqual(r.status, "success")
        self.assertEqual(r.items[0]["dedup_key"], "south_weekend:112345")
        self.assertEqual(r.items[0]["author_type"], "媒体")
        self.assertTrue(r.items[0]["url"].startswith("https://www.infzm.com/"))

    def test_changed_and_empty(self):
        self.assertEqual(
            SouthWeekendAdapter(http_get=ok_transport(SW_CHANGED))
            .search("q", 10).status, "parser_changed")
        self.assertEqual(
            SouthWeekendAdapter(http_get=ok_transport(SW_EMPTY))
            .search("q", 10).status, "empty")


class TestSogouWechat(unittest.TestCase):
    def test_parse_block(self):
        ad = SogouWechatAdapter(http_get=ok_transport(SG_OK))
        r = ad.search("养生", 10)
        self.assertEqual(r.status, "success")
        item = r.items[0]
        self.assertEqual(item["title"], "微信公众号文章标题")
        self.assertEqual(item["author_type"], "某账号")
        self.assertTrue(item["url"].startswith("https://weixin.sogou.com/"))
        self.assertTrue(item["dedup_key"].startswith("sogou_wechat:sg_"))

    def test_antispider_and_empty(self):
        self.assertEqual(
            SogouWechatAdapter(http_get=ok_transport(SG_ANTISPIDER))
            .search("q", 10).status, "rate_limited")
        self.assertEqual(
            SogouWechatAdapter(http_get=ok_transport(SG_EMPTY))
            .search("q", 10).status, "empty")


if __name__ == "__main__":
    unittest.main()
