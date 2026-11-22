"""A 线报告附件(2026-10-23):egress 二进制 / 图片抽取 / 资产步骤 / 报告渲染。

认识论红线:图片=附件不进判决链(evidence.assets 只有资产步骤写、只有
S7 渲染读);默认关=离线/回放产物零变化(跨版本逐字节不变由 e2e 确定性
用例与本套负向断言共同把守)。
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import install_simple, make_domain, make_ledger  # noqa: E402
from sense2act.collect.crawler_gate import NotRecordedError  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.crawler.adapters.official_site import (  # noqa: E402
    extract_image_urls)
from sense2act.crawler.egress import (  # noqa: E402
    EgressBinaryResponse,
    EgressHttpClient,
    egress_fingerprint,
)
from sense2act.stages.d_hooks import TemplateSectionWriter  # noqa: E402
from sense2act.stages.s7_assets import AssetCollector, AssetConfig  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 3000
PNG2 = b"\x89PNG\r\n\x1a\n" + b"1" * 3000
PNG_SHA = hashlib.sha256(PNG).hexdigest()
DETAIL = "https://www.xmfh.com.cn/info/1724/17031.htm"
IMG1 = "https://www.xmfh.com.cn/u/cms/www/202610/01_product.jpg"
IMG2 = "/u/cms/www/202610/02_product.png"
IMG2_ABS = "https://www.xmfh.com.cn/u/cms/www/202610/02_product.png"
# 正文区取图 fixture(trafilatura 可解析):正文图 2 张(绝对+相对),
# 正文内的 data:/logo 类过滤,正文外的页头页脚站级图天然排除
PAGE = f"""<html><head><title>产品发布</title></head><body>
<div class="header"><img src="/static/site_banner.png"></div>
<div class="article"><h1>新产品发布说明</h1>
<p>2026年10月,我院举行新产品发布会,现场展示了新一代设备与配套方案,
临床团队介绍了应用进展和培训计划,相关内容构成有效正文供抽取器识别。</p>
<p><img src="{IMG1}" alt="设备"></p>
<p>第二段正文继续介绍设备参数与临床合作细节,补充足够篇幅以确保正文
抽取稳定,避免短文本被视为噪音而被丢弃,保障图片处于正文上下文中。</p>
<p><img src="{IMG2}"><img src="data:image/png;base64,AAAA">
<img src="/static/logo.png"></p></div>
<div class="footer"><img src="/static/qr.png"></div></body></html>"""


class TestEgressBinary(unittest.TestCase):
    """get_binary:录制 base64 / 回放字节级 / 未录制如实抛 / 独立记忆。"""

    def test_record_then_replay_byte_identical(self):
        tmp = tempfile.mkdtemp()
        rec = Path(tmp) / "egress.json"
        live = EgressHttpClient(
            record_path=rec,
            transport_binary=lambda url, t: (200, PNG, "image/png"))
        r1 = live.get_binary(IMG1, headers={"Referer": DETAIL})
        self.assertEqual(r1.data, PNG)
        self.assertEqual(r1.content_type, "image/png")
        data = json.loads(rec.read_text(encoding="utf-8-sig"))
        entry = data["records"][egress_fingerprint(IMG1)]
        self.assertEqual(entry["encoding"], "base64")
        replay = EgressHttpClient(replay_path=rec)
        r2 = replay.get_binary(IMG1)
        self.assertEqual(r2.data, PNG)          # 字节级回放(I4 同构)
        self.assertEqual(r2.content_type, "image/png")

    def test_replay_miss_raises_not_recorded(self):
        tmp = tempfile.mkdtemp()
        rec = Path(tmp) / "egress.json"
        rec.write_text(json.dumps({"kind": "egress_v1", "records": {}}),
                       encoding="utf-8")
        with self.assertRaises(NotRecordedError):
            EgressHttpClient(replay_path=rec).get_binary(IMG1)

    def test_old_text_record_is_not_binary(self):
        """旧格式文本条目(无 encoding 字段)对二进制路径=未录制,如实抛。"""
        tmp = tempfile.mkdtemp()
        rec = Path(tmp) / "egress.json"
        fp = egress_fingerprint(DETAIL)
        rec.write_text(json.dumps({"kind": "egress_v1", "records": {
            fp: {"url": DETAIL, "status_code": 200, "text": "<html/>"}}}),
            encoding="utf-8")
        egress = EgressHttpClient(replay_path=rec)
        self.assertEqual(egress.get(DETAIL).text, "<html/>")
        with self.assertRaises(NotRecordedError):
            egress.get_binary(DETAIL)

    def test_text_get_on_binary_record_returns_empty(self):
        """文本路径踩到二进制条目不炸(text 缺失 → 空串兜底)。"""
        tmp = tempfile.mkdtemp()
        rec = Path(tmp) / "egress.json"
        import base64
        rec.write_text(json.dumps({"kind": "egress_v1", "records": {
            egress_fingerprint(IMG1): {
                "url": IMG1, "status_code": 200, "encoding": "base64",
                "data_b64": base64.b64encode(PNG).decode("ascii"),
                "content_type": "image/png"}}}), encoding="utf-8")
        resp = EgressHttpClient(replay_path=rec).get(IMG1)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "")

    def test_memo_same_url_one_call(self):
        egress = EgressHttpClient(
            transport_binary=lambda url, t: (200, PNG, "image/png"))
        egress.get_binary(IMG1)
        egress.get_binary(IMG1)
        self.assertEqual(egress.calls, 1)

    def test_replay_rejects_binary_transport(self):
        rec = Path(tempfile.mkdtemp()) / "e.json"
        rec.write_text(json.dumps({"kind": "egress_v1", "records": {}}),
                       encoding="utf-8")
        with self.assertRaises(ValueError):
            EgressHttpClient(replay_path=rec,
                             transport_binary=lambda u, t: (200, b"", ""))


class TestExtractImageUrls(unittest.TestCase):
    def test_filters_and_resolves(self):
        urls = extract_image_urls(PAGE, DETAIL)
        self.assertEqual(urls, [IMG1, IMG2_ABS])   # 正文外站级图/data:/logo 全滤

    def test_limit(self):
        paras = "".join(
            f'<p>第{n}段正文内容,介绍设备参数与临床应用进展的细节。</p>'
            f'<p><img src="/p/i{n}.jpg"></p>' for n in range(8))
        many = (f'<html><head><title>多图</title></head><body>'
                f'<div class="article"><h1>多图正文</h1>{paras}</div>'
                f'</body></html>')
        self.assertEqual(len(extract_image_urls(many, DETAIL, limit=3)), 3)

    def test_bad_html_empty(self):
        self.assertEqual(extract_image_urls("<html><body>", DETAIL), [])

    def test_content_less_page_yields_nothing(self):
        """正文抽取失败=无图(不回退全页扫查,防站级图混入)。"""
        nav_only = ('<html><body><div class="nav"><a href="/a">关于</a>'
                    '<img src="/static/banner.jpg"></div></body></html>')
        self.assertEqual(extract_image_urls(nav_only, DETAIL), [])


def _article_page(img_urls: list[str]) -> str:
    """正文结构页(trafilatura 可解析):引言段 + 每图配一段正文。"""
    paras = ("<p>引言:我院今日举行新产品发布会,介绍新一代设备的参数、"
             "临床应用与培训计划,以下为现场图文实录与相关说明。</p>")
    for i, u in enumerate(img_urls, 1):
        paras += (f"<p>第{i}段正文,介绍设备参数、临床应用与培训计划的细节,"
                  f"构成足够篇幅的有效正文内容供抽取器识别主要内容区域。</p>"
                  f'<p><img src="{u}"></p>')
    return (f'<html><head><title>产品发布</title></head><body>'
            f'<div class="header"><img src="/static/banner.png"></div>'
            f'<div class="article"><h1>新产品发布说明</h1>{paras}</div>'
            f'<div class="footer"><img src="/static/qr.png"></div></body></html>')


class FakeEgress:
    """资产步骤测试替身:get=详情页文本路由,get_binary=图片字节路由。"""

    def __init__(self, pages: dict[str, str], binaries: dict[str, tuple],
                 fail_pages: set[str] | None = None):
        self._pages, self._binaries = pages, binaries
        self._fail_pages = fail_pages or set()
        self.binary_calls: list[str] = []

    def get(self, url):
        if url in self._fail_pages:
            raise TimeoutError("simulated")
        text = self._pages.get(url, "")

        class _R:
            pass
        r = _R()
        r.status_code, r.text = (200, text) if text else (404, "")
        return r

    def get_binary(self, url, headers=None):
        self.binary_calls.append(url)
        code, data, ctype = self._binaries.get(url, (404, b"", ""))
        return EgressBinaryResponse(code, data, ctype)


def _add_official_evidence(led, event_id, url, tag):
    eid = led.insert_evidence(event_id, {
        "platform": "official_website", "source": "www.xmfh.com.cn",
        "url": url, "title": f"产品发布 {tag}", "content": "正文",
        "published_at": "", "author_type": "当事方",
        "dedup_key": f"official:xmfh:{tag}"}, 1)
    assert eid
    return eid


class TestAssetCollector(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)
        self.runs = tempfile.mkdtemp()

    def _collector(self, egress, **cfg):
        return AssetCollector(self.led, egress, runs_dir=self.runs,
                              config=AssetConfig(**cfg) if cfg else None)

    def test_collect_writes_files_and_ledger(self):
        eid = _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        egress = FakeEgress(
            {DETAIL: PAGE}, {IMG1: (200, PNG, "image/png"),
                             IMG2_ABS: (200, PNG2, "image/png")})
        n = self._collector(egress).collect("evt_t")
        self.assertEqual(n, 2)
        out = Path(self.runs) / "evt_t" / "assets"
        self.assertTrue((out / "E1_img1.png").exists())
        self.assertTrue((out / "E1_img2.png").exists())
        assets = self.led.evidence_assets("evt_t")[eid]
        self.assertEqual(assets[0]["sha256"], PNG_SHA)
        self.assertEqual(assets[0]["source_url"], IMG1)
        self.assertEqual(assets[0]["path"], "assets/E1_img1.png")
        self.assertEqual(egress.binary_calls, [IMG1, IMG2_ABS])

    def test_per_evidence_cap(self):
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        urls = [f"https://www.xmfh.com.cn/p/i{n}.jpg" for n in range(6)]
        binaries = {u: (200, PNG[:2000] + str(n).encode() * 2000, "image/jpeg")
                    for n, u in enumerate(urls)}
        egress = FakeEgress({DETAIL: _article_page(urls)}, binaries)
        n = self._collector(egress, per_evidence=3).collect("evt_t")
        self.assertEqual(n, 3)

    def test_per_event_cap(self):
        d1, d2 = DETAIL, DETAIL.replace("17031", "17032")
        _add_official_evidence(self.led, "evt_t", d1, "1")
        _add_official_evidence(self.led, "evt_t", d2, "2")
        p1_urls = [IMG1, IMG2_ABS]
        p2_urls = [f"https://www.xmfh.com.cn/q/i{n}.jpg" for n in range(3)]
        binaries = {IMG1: (200, PNG, "image/png"), IMG2_ABS: (200, PNG2, "image/png"),
                    **{u: (200, PNG[:1500] + f"q{n}".encode() * 1500, "image/jpeg")
                       for n, u in enumerate(p2_urls)}}
        egress = FakeEgress({d1: _article_page(p1_urls),
                             d2: _article_page(p2_urls)}, binaries)
        n = self._collector(egress, per_evidence=3, per_event=4).collect("evt_t")
        self.assertEqual(n, 4)

    def test_non_image_content_type_rejected(self):
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        egress = FakeEgress({DETAIL: PAGE},
                            {IMG1: (200, b"<html>404</html>", "text/html")})
        self.assertEqual(self._collector(egress).collect("evt_t"), 0)
        self.assertEqual(self.led.evidence_assets("evt_t"), {})

    def test_tiny_bytes_rejected(self):
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        egress = FakeEgress({DETAIL: PAGE},
                            {IMG1: (200, b"\x89PNG" + b"0" * 10, "image/png")})
        self.assertEqual(self._collector(egress).collect("evt_t"), 0)

    def test_other_platforms_skipped(self):
        self.led.insert_evidence("evt_t", {
            "platform": "ccgp", "source": "www.ccgp.gov.cn",
            "url": DETAIL, "title": "公告", "content": "正文",
            "dedup_key": "ccgp:1"}, 1)
        egress = FakeEgress({DETAIL: PAGE}, {IMG1: (200, PNG, "image/png")})
        self.assertEqual(self._collector(egress).collect("evt_t"), 0)
        self.assertEqual(egress.binary_calls, [])

    def test_detail_unavailable_no_crash(self):
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        egress = FakeEgress({}, {}, fail_pages={DETAIL})
        self.assertEqual(self._collector(egress).collect("evt_t"), 0)

    def test_binary_unrecorded_replay_like_failure_skips(self):
        """get_binary 抛错(如回放未录制)=该图如实跳过,不炸。"""
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")

        class Boom(FakeEgress):
            def get_binary(self, url, headers=None):
                raise NotRecordedError("未录制")

        self.assertEqual(
            self._collector(Boom({DETAIL: PAGE}, {})).collect("evt_t"), 0)

    def test_cross_evidence_shared_image_dropped(self):
        """跨证据去重:同一张图挂在 ≥2 条证据 = 站级公共图,整组撤下删文件。"""
        d1, d2 = DETAIL, DETAIL.replace("17031", "17032")
        e1 = _add_official_evidence(self.led, "evt_t", d1, "1")
        e2 = _add_official_evidence(self.led, "evt_t", d2, "2")
        shared = "https://www.xmfh.com.cn/u/cms/www/202610/shared_banner.jpg"
        # 页1:站级图 + 独有图;页2:只有同一张站级图
        binaries = {
            IMG1: (200, PNG, "image/png"),              # 两页同图(站级)
            IMG2_ABS: (200, PNG2, "image/png"),         # 仅第一页独有
            shared: (200, PNG, "image/png"),            # 与 IMG1 同字节
        }
        egress = FakeEgress({d1: PAGE, d2: _article_page([shared])}, binaries)
        n = self._collector(egress).collect("evt_t")
        self.assertEqual(n, 1)                          # 只有第一页独有图存活
        assets_map = self.led.evidence_assets("evt_t")
        self.assertEqual(assets_map[e1][0]["sha256"],
                         hashlib.sha256(PNG2).hexdigest())
        self.assertNotIn(e2, assets_map)                # 站级图证据零附件
        kept = Path(self.runs) / "evt_t" / "assets"
        self.assertTrue((kept / "E1_img2.png").exists())
        self.assertFalse((kept / "E2_img1.png").exists())   # 撤下即删文件

    def test_within_evidence_same_bytes_kept_once(self):
        """同证据内同字节不同 URL:只留一张(站内缩略图与正文图同源)。"""
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        egress = FakeEgress(
            {DETAIL: PAGE}, {IMG1: (200, PNG, "image/png"),
                             IMG2_ABS: (200, PNG, "image/png")})
        n = self._collector(egress).collect("evt_t")
        self.assertEqual(n, 1)
        kept = Path(self.runs) / "evt_t" / "assets"
        self.assertTrue((kept / "E1_img1.png").exists())
        self.assertFalse((kept / "E1_img2.png").exists())


class TestRenderAssets(unittest.TestCase):
    """S7 渲染消费:有附件→md/html/audit 呈现;无附件→产物零附件痕迹。"""

    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _finalize(self):
        ReportBuilder(
            self.led, self.domain, InvestigationConfig(),
            TemplateSectionWriter(), provider=None, runs_dir=self.tmp
        ).finalize("evt_t", 2, "轮数上限", costs={"llm_calls": 1},
                   envelope_label="测试事件", signal_type="风险")
        return Path(self.tmp) / "evt_t"

    def test_report_carries_assets(self):
        eid = _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        AssetCollector(self.led, FakeEgress(
            {DETAIL: PAGE}, {IMG1: (200, PNG, "image/png")}),
            runs_dir=self.tmp).collect("evt_t")
        out = self._finalize()
        md = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("![附件图](assets/E1_img1.png)", md)
        self.assertIn("仅呈现,非证据", md)
        html = (out / "report.html").read_text(encoding="utf-8")
        self.assertIn('<img src="assets/E1_img1.png"', html)
        self.assertIn("附件(仅呈现,非证据)</th>", html)
        # 排版回归(2026-10-23):长 URL 不可断行曾把证据表撑出容器
        self.assertIn("overflow-wrap:anywhere", html)
        self.assertIn("table-layout:fixed", html)
        audit = json.loads((out / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["assets"][0]["evidence_id"], eid)
        self.assertEqual(audit["assets"][0]["sha256"], PNG_SHA)

    def test_report_without_assets_unchanged_shape(self):
        _add_official_evidence(self.led, "evt_t", DETAIL, "1")
        out = self._finalize()
        md = (out / "report.md").read_text(encoding="utf-8")
        html = (out / "report.html").read_text(encoding="utf-8")
        audit_text = (out / "audit.json").read_text(encoding="utf-8")
        self.assertNotIn("![附件图]", md)
        self.assertNotIn("<th>附件", html)
        self.assertNotIn('"assets"', audit_text)   # 无附件不加键=旧产物不变


if __name__ == "__main__":
    unittest.main()
