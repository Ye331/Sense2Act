"""本地爬虫框架:JSONP 解析/关键词过滤/出网录制-回放/扇出聚合。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.crawler import (  # noqa: E402
    CctvNewsAdapter,
    EgressResponse,
    EgressHttpClient,
    LocalCollectService,
    build_default_registry,
)
from sense2act.models.observation import CollectRequest  # noqa: E402

JSONP_SAMPLE = (
    'callback({"data":{"list":['
    '{"id":"ARTI001","title":"某医院启动AI辅助诊断采购","url":"https://news.cctv.com/a1",'
    '"brief":"预算480万元","focus_date":"2026-09-20 10:00:00","keywords":"AI 采购"},'
    '{"id":"ARTI002","title":"国际油价波动","url":"https://news.cctv.com/a2",'
    '"brief":"油价分析","focus_date":"2026-09-21 09:00:00","keywords":"油价"},'
    '{"id":"ARTI003","title":"缺URL的残缺记录","url":"","brief":"x","focus_date":""}'
    ']}})')


class FakeTransport:
    """可编程传输替身:按 URL 返回 (status_code, text);记录调用次数。"""

    def __init__(self, routes: dict[str, tuple[int, str]]):
        self._routes = routes
        self.calls: list[str] = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if url not in self._routes:
            return 404, ""
        return self._routes[url]


CCTV_URL = ("https://news.cctv.com/2019/07/gaiban/cmsdatainterface/page/"
            "china_1.jsonp")


class TestCctvAdapter(unittest.TestCase):
    def test_parse_filter_and_honest_empty(self):
        """解析/残缺记录跳过/关键词过滤;无命中如实 empty 并给候选数。"""
        adapter = CctvNewsAdapter(http_get=lambda url, timeout=None: EgressResponse(200, JSONP_SAMPLE))
        result = adapter.search("医院 AI 采购", limit=10)
        self.assertEqual(result.status, "success")
        self.assertEqual(len(result.items), 1)          # 残缺记录被跳过+关键词过滤
        item = result.items[0]
        self.assertEqual(item["dedup_key"], "cctv_news:china:ARTI001")  # 栏目前缀(全栏目扫查)
        self.assertEqual(item["source"], "news.cctv.com")
        self.assertEqual(item["author_type"], "官方媒体")
        self.assertIn("480", item["content"])

        miss = adapter.search("完全不相关的词", limit=10)
        self.assertEqual(miss.status, "empty")
        self.assertIn("没有匹配", miss.message)

    def test_empty_query_not_full_dump(self):
        """缺陷回归:空查询不得伪装成功并全量返回(与 tianjin_zfcg 同纪律)。"""
        adapter = CctvNewsAdapter(http_get=lambda url, timeout=None: EgressResponse(200, JSONP_SAMPLE))
        for q in ("", "   "):
            r = adapter.search(q, limit=10)
            self.assertEqual(r.status, "empty")
            self.assertIn("拒绝全量", r.message)

    def test_status_mapping(self):
        cases = [(429, "rate_limited"), (403, "authentication_required"),
                 (502, "network_error"), (404, "network_error")]
        for code, expect in cases:
            adapter = CctvNewsAdapter(http_get=lambda u, t=None, c=code: EgressResponse(c, ""))
            self.assertEqual(adapter.search("q", 10).status, expect)
        bad = CctvNewsAdapter(http_get=lambda u, t=None: EgressResponse(200, "不是jsonp"))
        self.assertEqual(bad.search("q", 10).status, "parser_changed")


class TestEgressGate(unittest.TestCase):
    def test_record_then_replay_zero_network(self):
        tmp = tempfile.mkdtemp()
        rec = Path(tmp) / "egress.json"
        transport = FakeTransport({CCTV_URL: (200, JSONP_SAMPLE)})
        eg = EgressHttpClient(record_path=rec, transport=transport)
        r1 = eg.get(CCTV_URL)
        self.assertEqual(r1.status_code, 200)
        eg.save()
        self.assertEqual(len(transport.calls), 1)

        replay = EgressHttpClient(replay_path=rec)   # 无 transport:零网络
        r2 = replay.get(CCTV_URL)
        self.assertEqual(r2.text, JSONP_SAMPLE)      # 字节级同响应(I4)
        from sense2act.collect.crawler_gate import NotRecordedError
        with self.assertRaises(NotRecordedError):
            replay.get("https://news.cctv.com/other.jsonp")


class TestEgressBodyDecoding(unittest.TestCase):
    def test_gzip_magic_decompressed(self):
        """乙方宝缺陷回归:服务器无视 identity 仍 gzip -> 魔法数兜底解压。"""
        import gzip
        raw = gzip.compress("<html>公告正文</html>".encode("utf-8"))
        self.assertEqual(EgressHttpClient._decode_body(raw), "<html>公告正文</html>")
        self.assertEqual(EgressHttpClient._decode_body(b"<html>plain</html>"),
                         "<html>plain</html>")


class TestLocalCollectService(unittest.TestCase):
    def test_fanout_dedup_and_aggregation(self):
        class FlakyAdapter:
            platform = "flaky"
            adapter_version = "t"

            def search(self, query, limit):
                return __import__("sense2act.crawler", fromlist=["AdapterResult"]) \
                    .AdapterResult(status="rate_limited", retryable=True,
                                   message="限流")

        egress = EgressHttpClient(transport=FakeTransport({CCTV_URL: (200, JSONP_SAMPLE)}))
        registry = build_default_registry(egress)
        from sense2act.crawler import AdapterResult
        registry._factories["flaky"] = lambda: FlakyAdapter()

        service = LocalCollectService(registry, egress=egress, max_calls=10)
        receipt = service.send(CollectRequest(
            query="医院 AI 采购", platforms=["cctv_news", "flaky"], limit=10))
        self.assertEqual(receipt.status, "partial")      # 一好一坏 -> partial
        self.assertEqual(len(receipt.items), 1)
        self.assertEqual(receipt.source_reports[0].status, "success")
        self.assertEqual(receipt.source_reports[1].status, "rate_limited")
        self.assertTrue(receipt.source_reports[1].retryable)

    def test_failed_all_is_failed(self):
        egress = EgressHttpClient(transport=FakeTransport({}))
        service = LocalCollectService(build_default_registry(egress),
                                      egress=egress, max_calls=10)
        receipt = service.send(CollectRequest(query="x", platforms=["cctv_news"]))
        self.assertEqual(receipt.status, "failed")       # 404->network_error 且零条目

    def test_query_passes_through_with_routing_keys(self):
        """路由键注入后 query 透传:桩适配器收到含 | 编号/采购人 后缀的 query,
        且独立字段(buyer/project_no)一并透传(§八 契约 + 定向检索锚点)。"""
        seen = {}

        class SpyAdapter:
            platform = "spy"
            adapter_version = "t"

            def search(self, query, limit, buyer="", project_no="", published_date=""):
                seen["query"] = query
                seen["buyer"] = buyer
                seen["project_no"] = project_no
                from sense2act.crawler import AdapterResult
                return AdapterResult(status="empty", items=[])

        egress = EgressHttpClient(transport=FakeTransport({}))
        registry = build_default_registry(egress)
        registry._factories["spy"] = lambda: SpyAdapter()
        service = LocalCollectService(registry, egress=egress, max_calls=10)
        service.send(CollectRequest(
            query="中标公告 | 编号:SHGP-2026-A536 | 采购人:某医院",
            platforms=["spy"], limit=10, buyer="某医院", project_no="SHGP-2026-A536"))
        self.assertEqual(seen["query"],
                         "中标公告 | 编号:SHGP-2026-A536 | 采购人:某医院")
        self.assertEqual(seen["project_no"], "SHGP-2026-A536")
        self.assertEqual(seen["buyer"], "某医院")

    def test_source_url_passthrough_to_declaring_adapter(self):
        """信封自带 URL(发现三)按需下传:声明 source_url 参数的适配器
        收到,其余源零感知(与 buyer/project_no 同一内省模式)。"""
        seen = {}

        class SpyAdapter:
            platform = "spy"
            adapter_version = "t"

            def search(self, query, limit, source_url=""):
                seen["source_url"] = source_url
                from sense2act.crawler import AdapterResult
                return AdapterResult(status="empty", items=[])

        class PlainAdapter:
            platform = "plain"
            adapter_version = "t"

            def search(self, query, limit):
                seen["plain_called"] = True
                from sense2act.crawler import AdapterResult
                return AdapterResult(status="empty", items=[])

        egress = EgressHttpClient(transport=FakeTransport({}))
        registry = build_default_registry(egress)
        registry._factories["spy"] = lambda: SpyAdapter()
        registry._factories["plain"] = lambda: PlainAdapter()
        service = LocalCollectService(registry, egress=egress, max_calls=10)
        service.send(CollectRequest(
            query="q", platforms=["spy", "plain"],
            source_url="https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/x.htm"))
        self.assertEqual(seen["source_url"],
                         "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/x.htm")
        self.assertTrue(seen.get("plain_called"))

    def test_history_tool_with_platforms_goes_out(self):
        """修复(2026-10-16 live 验收):history_baseline 带平台时正常扇出,
        不再被 tool 早退拦截——否则 live 下历史类缺口零 HTTP 空转
        (CRAG 回边换来的 fallback 平台同样会被挡)。"""

        class SpyAdapter:
            platform = "spy"
            adapter_version = "t"
            called = False

            def search(self, query, limit):
                self.called = True
                from sense2act.crawler import AdapterResult
                return AdapterResult(status="empty", items=[])

        spy = SpyAdapter()
        egress = EgressHttpClient(transport=FakeTransport({}))
        registry = build_default_registry(egress)
        registry._factories["spy"] = lambda: spy
        service = LocalCollectService(registry, egress=egress, max_calls=10)
        receipt = service.send(CollectRequest(
            query="历史采购 | 编号:X | 采购人:Y", tool="history_baseline",
            platforms=["spy"]))
        self.assertTrue(spy.called)
        self.assertEqual(receipt.tool, "history_baseline")

    def test_history_tool_without_platforms_still_honest(self):
        """无平台可去时保留诚实回执:私域历史库待接如实 source_unavailable。"""
        egress = EgressHttpClient(transport=FakeTransport({}))
        service = LocalCollectService(build_default_registry(egress),
                                      egress=egress, max_calls=10)
        receipt = service.send(CollectRequest(query="x", tool="history_baseline"))
        self.assertEqual(receipt.status, "empty")
        self.assertEqual(receipt.source_reports[0].platform, "internal_history")
        self.assertIn("私域", receipt.source_reports[0].message)


if __name__ == "__main__":
    unittest.main()
