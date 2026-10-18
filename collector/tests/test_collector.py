"""采集 worker 契约测试:假后端 + 按 URL 路由的假出网,校验与后端 §9.1 一致的不变量。"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "agent"))

from sense2act.crawler import AdapterResult  # noqa: E402

from collector import sources  # noqa: E402
from collector.backend import BackendError  # noqa: E402
from collector.documents import buyer_from_text, publish_date, to_document  # noqa: E402
from collector.sources import AdapterSpec, SourceConfig  # noqa: E402
from collector.state import CollectorState  # noqa: E402
from collector.worker import Collector, PoliteHttp  # noqa: E402

LOCAL_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/index.htm"
CENTRAL_URL = "https://www.ccgp.gov.cn/cggg/zygg/gkzb/index.htm"
HOSPITAL_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261004_27439617.htm"
LIGHT_URL = "https://www.ccgp.gov.cn/cggg/dfgg/gkzb/202610/t20261004_27439618.htm"

# 结构同 2026-10-04 实抓列表页(agent/tests/test_ccgp.py 同款夹具)
LIST_LOCAL = """
<html><body><ul class="c_list_bid">
<li>
 <a href="./202610/t20261004_27439618.htm" title="吉林市城市照明建设管理中心路灯灯杆及配套设施项目公开招标公告">吉林市…</a>
 发布时间：<em>2026-10-04 14:23</em> 地域：<em>吉林</em> 采购人：<em>吉林市城市照明建设管理中心</em>
</li>
<li>
 <a href="./202610/t20261004_27439617.htm" title="某市第一医院医疗设备采购项目公开招标公告">某市第一医院…</a>
 发布时间：<em>2026-10-04 10:00</em> 地域：<em>浙江</em> 采购人：<em>某市第一医院</em>
</li>
</ul></body></html>"""
LIST_CENTRAL = """
<html><body><ul class="c_list_bid">
<li>
 <a href="./202610/t20261004_27439617.htm" title="某市第一医院医疗设备采购项目公开招标公告">某市第一医院…</a>
 发布时间：<em>2026-10-04 10:00</em> 地域：<em>浙江</em> 采购人：<em>某市第一医院</em>
</li>
</ul></body></html>"""
DETAIL_HOSPITAL = """
<html><body><div class="vF_detail_main">
项目编号： 采购计划-[2026]-05669号-J2026ZCY283
预算金额 ￥203.000000万元（人民币）
开标时间 2026年10月28日 08:35
<div class="vF_detail_content"><div id="noticeArea">
<p>医疗设备采购需求：采购超声诊断设备一套。</p>
<p>资格要求：供应商须具备医疗器械经营许可。</p>
</div></div></div></body></html>"""
BLOCKED = "<html><title>频繁访问!</title>您的访问过于频繁,请稍后再试。</html>"
CHANGED = "<html><div>改版了</div></html>"

logging.getLogger("collector").addHandler(logging.NullHandler())   # 失败路径的告警日志不刷测试输出
logging.getLogger("collector").propagate = False

CCGP_SOURCE = {"source_id": "src_ccgp", "name": "中国政府采购网", "adapter": "ccgp",
               "url": "https://www.ccgp.gov.cn/", "config": {"category": "医疗设备", "pages": 1}}


class Response:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


class Routes:
    """按 URL 返回固定页面;记录真实请求顺序。未注册的 URL 返回 404。"""

    def __init__(self, routes: dict[str, tuple[int, str]]):
        self.routes, self.calls = routes, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        status, text = self.routes.get(url, (404, ""))
        return Response(status, text)


class FakeBackend:
    def __init__(self, queue=(), push_error: BackendError | None = None):
        self.queue, self.push_error = list(queue), push_error
        self.pushed: list[dict] = []
        self.runs: list[dict] = []
        self.urls: set[str] = set()

    def ingest_queue(self):
        return self.queue

    def push_documents(self, source_id, items):
        if self.push_error:
            raise self.push_error
        accepted = [i for i in items if i["url"] not in self.urls]
        self.urls |= {i["url"] for i in items}
        self.pushed += accepted
        return {"accepted": len(accepted), "duplicates": len(items) - len(accepted), "failed": 0,
                "document_ids": [f"doc_{n}" for n in range(len(accepted))]}

    def report_run(self, source_id, ok, stats, errors):
        self.runs.append({"source_id": source_id, "ok": ok, "stats": stats, "errors": errors})
        return {"source_id": source_id}


class CollectorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def collector(self, backend, routes: Routes):
        return Collector(backend, CollectorState(self.state_path), http_factory=lambda: routes.get)


class TestTenderSource(CollectorCase):
    def ok_routes(self, **overrides):
        routes = {LOCAL_URL: (200, LIST_LOCAL), CENTRAL_URL: (200, LIST_CENTRAL),
                  HOSPITAL_URL: (200, DETAIL_HOSPITAL)}
        routes.update(overrides)
        return Routes(routes)

    def test_detail_enriched_document_matches_ingest_contract(self):
        backend = FakeBackend([CCGP_SOURCE])
        result = self.collector(backend, self.ok_routes()).run_once()
        self.assertEqual(result, {"due": 1, "ran": 1})
        doc = next(d for d in backend.pushed if d["url"] == HOSPITAL_URL)
        self.assertEqual(doc["doc_type"], "announcement")
        self.assertEqual(doc["title"], "某市第一医院医疗设备采购项目公开招标公告")
        self.assertEqual(doc["org_name"], "某市第一医院")            # 列表自带采购人
        self.assertEqual(doc["amount"], "2030000.00")                 # 203 万元 → 元,两位小数
        self.assertEqual(doc["publish_date"], "2026-10-04")           # 京时日历日
        self.assertEqual(doc["deadline"], "2026-10-28")
        self.assertEqual(doc["region"], "浙江")
        self.assertEqual(doc["category"], "医疗设备")                  # 源配置统一品目
        self.assertIn("采购超声诊断设备一套", doc["content_text"])      # 正文而非标题
        self.assertEqual(doc["raw_html"], DETAIL_HOSPITAL)            # 详情原文即快照
        self.assertEqual(doc["project_no"], "采购计划-[2026]-05669号-J2026ZCY283")

    def test_detail_failure_is_retried_then_ingested_title_level(self):
        backend, routes = FakeBackend([CCGP_SOURCE]), self.ok_routes()   # 路灯详情 404
        self.collector(backend, routes).run_once()
        self.assertEqual([d["url"] for d in backend.pushed], [HOSPITAL_URL])
        run = backend.runs[-1]
        self.assertTrue(run["ok"])                                     # 部分详情失败不算整源失败
        self.assertEqual(run["stats"]["new"], 2)
        self.assertEqual(run["errors"], [{"url": LIGHT_URL, "stage": "fetch", "error": "详情返回 HTTP 404"}])

        self.collector(backend, routes).run_once()                     # 第 2 次仍失败:继续等
        self.assertEqual(len(backend.pushed), 1)
        self.collector(backend, routes).run_once()                     # 第 3 次:按列表级入库
        light = next(d for d in backend.pushed if d["url"] == LIGHT_URL)
        self.assertIsNone(light["raw_html"])
        self.assertFalse(light["detail_fetched"])
        self.assertEqual(light["org_name"], "吉林市城市照明建设管理中心")
        self.assertIn("阶段:招标采购", light["content_text"])          # 标题 + 结构化后缀,不编正文

    def test_seen_urls_are_not_fetched_again(self):
        backend = FakeBackend([CCGP_SOURCE])
        self.collector(backend, self.ok_routes()).run_once()
        second = self.ok_routes()
        self.collector(backend, second).run_once()
        self.assertNotIn(HOSPITAL_URL, second.calls)                   # 已入库的详情不再抓
        self.assertEqual(len([d for d in backend.pushed if d["url"] == HOSPITAL_URL]), 1)

    def test_blocked_list_reports_failure_without_pushing(self):
        backend = FakeBackend([CCGP_SOURCE])
        self.collector(backend, Routes({LOCAL_URL: (200, BLOCKED)})).run_once()
        self.assertEqual(backend.pushed, [])
        run = backend.runs[-1]
        self.assertFalse(run["ok"])
        self.assertEqual(run["errors"][0]["stage"], "fetch")
        self.assertTrue(run["errors"][0]["error"].startswith("authentication_required"))

    def test_parser_changed_maps_to_normalize_stage(self):
        backend = FakeBackend([CCGP_SOURCE])
        routes = Routes({LOCAL_URL: (200, CHANGED), CENTRAL_URL: (200, CHANGED)})
        self.collector(backend, routes).run_once()
        self.assertEqual(backend.runs[-1]["errors"][0]["stage"], "normalize")

    def test_all_new_details_failing_marks_run_failed(self):
        backend = FakeBackend([CCGP_SOURCE])
        self.collector(backend, self.ok_routes(**{HOSPITAL_URL: (200, BLOCKED)})).run_once()
        self.assertEqual(backend.pushed, [])
        self.assertFalse(backend.runs[-1]["ok"])
        self.assertTrue(any("拦截" in e["error"] for e in backend.runs[-1]["errors"]))

    def test_config_filters_keywords_and_caps_items(self):
        source = {**CCGP_SOURCE, "config": {"keywords": ["医院"], "max_items": 5, "pages": 1}}
        backend, routes = FakeBackend([source]), self.ok_routes()
        self.collector(backend, routes).run_once()
        self.assertEqual([d["url"] for d in backend.pushed], [HOSPITAL_URL])
        self.assertNotIn(LIGHT_URL, routes.calls)                      # 筛掉的条目不抓详情
        self.assertEqual(backend.runs[-1]["stats"]["candidates"], 1)

    def test_push_error_reports_persist_and_keeps_items_unseen(self):
        backend = FakeBackend([CCGP_SOURCE], push_error=BackendError(40401, "信息源不存在"))
        self.collector(backend, self.ok_routes()).run_once()
        run = backend.runs[-1]
        self.assertFalse(run["ok"])
        self.assertEqual(run["errors"][-1]["stage"], "persist")
        self.assertFalse(CollectorState(self.state_path).seen("src_ccgp", HOSPITAL_URL))

    def test_unknown_adapter_left_for_other_crawlers(self):
        backend = FakeBackend([{"source_id": "src_demo", "adapter": "demo", "url": "https://x"}])
        self.assertEqual(self.collector(backend, Routes({})).run_once(), {"due": 1, "ran": 0})
        self.assertEqual(backend.runs, [])                              # 不回报,不影响该源健康度


class FakeNewsAdapter:
    """检索型新闻源替身:按检索词返回固定结果,'坏词' 模拟被拦。"""

    def __init__(self, http_get):
        self.http_get = http_get

    def search(self, query, limit):
        if query == "坏词":
            return AdapterResult(status="rate_limited", retryable=True, message="被限流")
        return AdapterResult(status="success", items=[{
            "platform": "toutiao", "source": "news.example.com", "url": "https://news.example.com/a1",
            "title": "科大讯飞发布医疗大模型新版本", "content": "摘要:讯飞医疗发布……",
            "published_at": "2026-09-03T01:13:00+00:00", "author_type": "媒体", "dedup_key": "toutiao:a1"}])


ARTICLE = """<html><head><meta property="article:published_time" content="2026-09-03T09:13:00+08:00">
</head><body><article><h1>科大讯飞发布医疗大模型新版本</h1>
<p>9月3日，科大讯飞发布医疗大模型新版本，覆盖辅助诊断与病历生成等场景，并在多家医院试点。</p>
<p>该模型面向基层医疗机构，已接入院内信息系统。</p></article></body></html>"""


class TestNewsSource(CollectorCase):
    def setUp(self):
        super().setUp()
        self.original = sources.ADAPTERS.get("toutiao")
        sources.ADAPTERS["toutiao"] = AdapterSpec(lambda get, c: FakeNewsAdapter(get), "news", detail=True, search=True)

    def tearDown(self):
        sources.ADAPTERS["toutiao"] = self.original
        super().tearDown()

    def source(self, config):
        return {"source_id": "src_news", "adapter": "toutiao", "url": "https://so.toutiao.com", "config": config}

    def test_queries_merged_and_article_kept_as_snapshot(self):
        backend = FakeBackend([self.source({"queries": ["科大讯飞 医疗", "讯飞 大模型", "坏词"]})])
        self.collector(backend, Routes({"https://news.example.com/a1": (200, ARTICLE)})).run_once()
        self.assertEqual(len(backend.pushed), 1)                        # 两个词命中同一篇,合并
        doc = backend.pushed[0]
        self.assertEqual(doc["doc_type"], "news")
        self.assertEqual(doc["publish_date"], "2026-09-03")
        self.assertEqual(doc["raw_html"], ARTICLE)                      # 新闻检测从快照读原文时间
        self.assertIsNone(doc["org_name"])
        self.assertEqual(doc["author_type"], "媒体")
        self.assertTrue(backend.runs[-1]["ok"])                          # 单个检索词失败只降级

    def test_search_source_without_queries_fails_honestly(self):
        backend = FakeBackend([self.source({})])
        self.collector(backend, Routes({})).run_once()
        run = backend.runs[-1]
        self.assertFalse(run["ok"])
        self.assertIn("queries", run["errors"][0]["error"])


class TestHelpers(unittest.TestCase):
    def test_publish_date_uses_beijing_calendar_day(self):
        self.assertEqual(publish_date("2026-10-03T16:30:00+00:00"), "2026-10-04")
        self.assertEqual(publish_date("2026-10-04"), "2026-10-04")
        self.assertIsNone(publish_date(""))
        self.assertIsNone(publish_date("昨天"))

    def test_buyer_from_text_requires_org_suffix(self):
        self.assertEqual(buyer_from_text("一、采购人信息 名 称：天津市西青区中医医院 地址：西青区"), "天津市西青区中医医院")
        self.assertEqual(buyer_from_text("采购人：天津轻工职业技术学院，联系人：王老师"), "天津轻工职业技术学院")
        self.assertIsNone(buyer_from_text("采购人：详见附件"))

    def test_list_level_document_has_no_snapshot(self):
        item = {"url": "https://x/1", "title": " 某项目 公告 ", "content": "某项目 公告 | 阶段:招标采购",
                "published_at": "", "province": "", "buyer": "", "platform": "yifangbao"}
        doc = to_document(item, None, doc_type="announcement", category=None, region="天津市", adapter="yifangbao")
        self.assertEqual(doc["title"], "某项目 公告")
        self.assertEqual(doc["region"], "天津市")                      # 列表无地域时用源配置兜底
        self.assertIsNone(doc["raw_html"])
        self.assertIsNone(doc["publish_date"])

    def test_source_config_defaults_and_bounds(self):
        config = SourceConfig.parse({"max_items": "999", "keywords": "医院", "detail": "yes", "doc_type": "bogus"})
        self.assertEqual(config.max_items, 200)
        self.assertEqual(config.keywords, ["医院"])
        self.assertIsNone(config.detail)                                 # 非布尔不当真
        self.assertIsNone(config.doc_type)
        self.assertTrue(config.accepts({"title": "某医院采购"}))
        self.assertFalse(config.accepts({"title": "某学校采购"}))

    def test_polite_http_spaces_out_real_requests_only(self):
        clock, sleeps = [0.0], []

        class Egress:
            def get(self, url, timeout=None):
                clock[0] += 0.2                                          # 每次真实请求耗时 0.2s
                return Response(200, url)

        http = PoliteHttp(Egress(), 1.0, sleep=lambda s: (sleeps.append(round(s, 3)), clock.__setitem__(0, clock[0] + s)),
                          clock=lambda: clock[0])
        http.get("https://a")
        http.get("https://b")
        http.get("https://a")                                            # 本轮已取过:不等待
        self.assertEqual(sleeps, [1.0])


class TestState(unittest.TestCase):
    def test_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            state = CollectorState(path)
            self.assertEqual(state.failed("s", "u1"), 1)
            state.mark_seen("s", ["u2"])
            state.save()
            again = CollectorState(path)
            self.assertTrue(again.seen("s", "u2"))
            self.assertEqual(again.attempts("s", "u1"), 1)
            self.assertFalse(again.seen("other", "u2"))


if __name__ == "__main__":
    unittest.main()
