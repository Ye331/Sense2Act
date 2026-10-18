"""天津市政府采购网适配器(首页扫查式,与 cctv_news 同构)。

勘察结论(2026-10-03 实测驱动选源):ccgp 国家站对当前出口 IP 稳定软封
(200+"频繁访问"拦截页);ggzy 国家站检索接口对非浏览器客户端稳定 404
(疑似 TLS 指纹级 WAF);cebpubservice 检索挂 vaptcha 验证码(红线,不做)。
天津站(天津市财政局)首页为纯 SSR:单页 HTML 内含全栏目公告(采购公告/
意向公开/单一来源公示/投诉与监督检查处理/考核结果等,约 225 条,覆盖数日
窗口,tab 为纯 show/hide 无 AJAX);条目锚点 /viewer.do?id=N 稳定可去重;
span.times 为服务端渲染的 Java 日期串("Wed Sep 30 22:38:53 CST 2026")。

谱系:政采信息按《政府采购信息发布管理办法》同时发布于省级指定媒体与
ccgp 国家站,故 source_lineage 将本域名归并到 ccgp 根(同体系折算,
防将来接国家站时双源双计)。

v1 边界(如实):仅首页滚动窗口(约 225 条/数日,"更多"为 AJAX 不追);
摘要级=标题级(金额等在中标公告详情正文,详情页补抓属 P2);覆盖范围
为天津市域(区域局限,报告口径需知晓)。过滤纪律:空查询拒绝全量返回,
类别词(项目/公告/采购/建设等)不计入命中——单独命中类别词的无关公告
不得带权威根身份入台账。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from lxml import etree, html

from ..base import AdapterResult
from .tender_meta import filter_by_query, parse_tender_meta  # noqa: F401 (共享)

BASE_URL = "https://www.ccgp-tianjin.gov.cn"
_CST = timezone(timedelta(hours=8))

# Java Date.toString() 形态:"Wed Sep 30 22:38:53 CST 2026"(CST=中国标准时间)
_JAVA_DATE_RE = re.compile(
    r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+[A-Z]+\s+(\d{4})")
_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

_CHALLENGE_MARKERS = ("验证码", "安全验证", "频繁访问", "登录后", "访问异常")


def _parse_java_date(raw) -> str:
    """Java 日期串 -> UTC ISO;不可解析返回 ''(各分支必须返回 str)。"""
    if not raw:
        return ""
    m = _JAVA_DATE_RE.search(str(raw))
    if not m:
        return ""
    month = _MONTHS.get(m.group(1))
    if month is None:
        return ""
    try:
        moment = datetime(int(m.group(6)), month, int(m.group(2)),
                          int(m.group(3)), int(m.group(4)), int(m.group(5)),
                          tzinfo=_CST)
    except ValueError:
        return ""
    return moment.astimezone(timezone.utc).isoformat()


def _viewer_id(href: str) -> str | None:
    try:
        values = parse_qs(urlparse(href).query).get("id")
    except ValueError:
        return None
    return values[0] if values else None


class TianjinZfcgAdapter:
    platform = "tianjin_zfcg"
    adapter_version = "0.1.0"

    def __init__(self, http_get):
        self._http_get = http_get

    def search(self, query: str, limit: int) -> AdapterResult:
        try:
            resp = self._http_get(BASE_URL)
        except TimeoutError:
            return AdapterResult(status="network_error", retryable=True,
                                 message="天津政采请求超时")
        except Exception as exc:
            return AdapterResult(status="network_error", retryable=True,
                                 message=f"天津政采请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return AdapterResult(status="rate_limited", retryable=True,
                                 message="天津政采被限流")
        if code in (401, 403):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="天津政采拒绝匿名访问")
        if code is not None and code >= 400:
            return AdapterResult(status="network_error", retryable=code >= 500,
                                 message=f"天津政采返回 HTTP {code}")
        return self._parse(resp.text, query, limit)

    # ------------------------------------------------------------------

    def _parse(self, page: str, query: str, limit: int) -> AdapterResult:
        if any(marker in page for marker in _CHALLENGE_MARKERS):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="天津政采返回验证/拦截页,不对抗")
        try:
            tree = html.fromstring(page)
        except (ValueError, etree.LxmlError):
            # lxml 对空 body/畸形页抛 ParserError(LxmlError 子类,非 ValueError),
            # 语义是"页面改版/异常"而非"网络失败",必须归 parser_changed(不可重试)
            return AdapterResult(status="parser_changed", retryable=False,
                                 message="天津政采首页 HTML 无法解析")
        anchors = tree.xpath(
            '//ul[contains(@class, "txtList")]'
            '//li/a[contains(@href, "viewer.do")]')
        if not anchors:
            return AdapterResult(status="parser_changed", retryable=False,
                                 message="天津政采首页列表容器缺失(疑似改版)")
        items: list[dict] = []
        seen: set[str] = set()
        for node in anchors:
            href = node.get("href") or ""
            vid = _viewer_id(href)
            title = " ".join(" ".join(node.itertext()).split())
            if not vid or not title or vid in seen:
                continue
            seen.add(vid)                       # 同一公告在多个栏目 tab 重复出现
            times = node.xpath(
                'following-sibling::span[contains(@class, "times")]/text()')
            meta = parse_tender_meta(title)     # 阶段/编号(确定性,增强 content)
            content = title
            if meta["stage"] != "其他" or meta["project_no"]:
                parts = [f"阶段:{meta['stage']}"]
                if meta["project_no"]:
                    parts.append(f"项目编号:{meta['project_no']}")
                content = f"{title} | {' | '.join(parts)}"
            items.append({
                "platform": self.platform,
                "source": "www.ccgp-tianjin.gov.cn",
                "url": href if href.startswith("http") else BASE_URL + href,
                "title": title,
                "content": content,             # 标题+结构化后缀(摘要级)
                "published_at": _parse_java_date(times[0] if times else None),
                "author_type": "官方公告",
                "dedup_key": f"tianjin_zfcg:{vid}",
            })
        return self._filter_by_query(items, query, limit)

    def sweep(self) -> list[dict]:
        """全量清单(去重后不过滤)——供采集成果转储/盘点,不进 S3 回执。"""
        try:
            resp = self._http_get(BASE_URL)
        except Exception:
            return []
        if resp.status_code is None or resp.status_code >= 400:
            return []
        try:
            tree = html.fromstring(resp.text)
        except (ValueError, etree.LxmlError):
            return []
        items: list[dict] = []
        seen: set[str] = set()
        for node in tree.xpath(
                '//ul[contains(@class, "txtList")]'
                '//li/a[contains(@href, "viewer.do")]'):
            href = node.get("href") or ""
            vid = _viewer_id(href)
            title = " ".join(" ".join(node.itertext()).split())
            if not vid or not title or vid in seen:
                continue
            seen.add(vid)
            times = node.xpath(
                'following-sibling::span[contains(@class, "times")]/text()')
            meta = parse_tender_meta(title)
            items.append({
                "title": title,
                "url": href if href.startswith("http") else BASE_URL + href,
                "published_at": _parse_java_date(times[0] if times else None),
                "dedup_key": f"tianjin_zfcg:{vid}",
                **meta,
            })
        return items

    @staticmethod
    def _filter_by_query(items: list[dict], query: str, limit: int
                         ) -> AdapterResult:
        # 统一过滤排序(空查询/类别词纪律/命中词数+新鲜度排序)已抽至 tender_meta 共享
        return filter_by_query(
            items, query, limit, label="首页",
            window_note="窗口=最新数日,历史公告不在首页")
