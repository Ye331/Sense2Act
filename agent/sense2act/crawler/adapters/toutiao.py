"""头条搜索适配器(自 F 盘 crawler_tool 移植精简版)。

保留精华:跳转链循环剥皮(jump?url= 双层嵌套)、多形态时间信号解析
(ISO/纯日期/中文日期/相对表述,置信分级)、验证码/登录页诚实识别。
精简:分页(search_page)与浏览器捕获通道(parse_payload)不移植。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse, urlunparse, urlencode

from lxml import html

from ..base import AdapterResult

_CST = timezone(timedelta(hours=8))
_SEARCH_URL = "https://so.toutiao.com/search"


def _build_url(query: str, page_number: int) -> str:
    qs = urlencode({"dvpf": "pc", "source": "input", "keyword": query,
                    "pd": "synthesis", "page_num": page_number})
    return f"{_SEARCH_URL}?{qs}"


class ToutiaoAdapter:
    platform = "toutiao"
    adapter_version = "0.1.0-ported"

    def __init__(self, http_get, *, clock: datetime | None = None):
        self._http_get = http_get
        self.clock = clock or datetime.now(timezone.utc)

    def search(self, query: str, limit: int) -> AdapterResult:
        try:
            resp = self._http_get(_build_url(query, 0))
        except TimeoutError:
            return AdapterResult(status="network_error", retryable=True,
                                 message="头条请求超时")
        except Exception as exc:
            return AdapterResult(status="network_error", retryable=True,
                                 message=f"头条请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return AdapterResult(status="rate_limited", retryable=True,
                                 message="头条搜索被限流")
        if code in (401, 403):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="头条拒绝匿名搜索")
        if code is not None and code >= 400:
            return AdapterResult(status="network_error", retryable=code >= 500,
                                 message=f"头条搜索返回 HTTP {code}")
        return self._parse_html(resp.text, limit)

    # ------------------------------------------------------------------

    def _parse_html(self, page: str, limit: int) -> AdapterResult:
        if any(m in page for m in ("验证码", "安全验证", "登录后", "登录以继续")):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="头条返回验证/登录页,不对抗")
        tree = html.fromstring(page)
        cards = tree.xpath(
            '//div[contains(@class, "s-result-list")]'
            '//div[contains(@class, "cs-card")]')
        if not cards:
            if "s-result-list" not in page:
                return AdapterResult(status="parser_changed", retryable=False,
                                     message="头条搜索容器缺失(疑似改版)")
            return AdapterResult(status="empty")
        items = []
        for card in cards[:limit]:
            anchors = card.xpath(
                './/a[contains(@class, "text-underline-hover")][1]')
            if not anchors:
                continue
            url = _unwrap_jump(anchors[0].get("href") or "")
            title = _text(anchors[0])
            if not url or not title:
                continue
            spans = [_text(n) for n in card.xpath(
                './/span[contains(@class, "text-underline-hover")]')]
            summary = next((s for s in spans if s), "")
            source_values = [_text(n) for n in card.xpath(
                './/div[contains(@class, "cs-source-content")]'
                '//span[contains(@class, "text-ellipsis")]')]
            published = source_values[-1] if len(source_values) > 1 else \
                (source_values[0] if source_values else None)
            published_at, _conf = _parse_time(published, self.clock)
            published_at = str(published_at) if published_at else None  # 防御:任何分支不得漏出 datetime
            items.append({
                "platform": self.platform,
                "source": _domain(url),
                "url": url,
                "title": title,
                "content": summary or title,      # 摘要级
                "published_at": published_at or "",
                "author_type": "",
                "dedup_key": f"toutiao:{_source_id(url) or _hash(title)}",
            })
        return AdapterResult(status="success" if items else "empty", items=items)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _text(node) -> str:
    value = " ".join(node.itertext()).strip()
    return unescape(value) or ""


def _unwrap_jump(href: str, max_hops: int = 3) -> str:
    url = href
    for _ in range(max_hops):
        values = parse_qs(urlparse(url).query).get("url")
        if not values:
            break
        url = unquote(values[0])
    return url if url.startswith("http") else ""


def _source_id(url: str) -> str | None:
    m = re.search(r"/(?:a|article/)(\d{6,})(?:[/?#]|$)", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6,})(?:[/?#]|$)", url)
    return m.group(1) if m else None


def _parse_time(raw, clock: datetime) -> tuple[str | None, float]:
    """多形态时间信号(移植版精简):ISO/纯日期/中文日期/点分/MM-DD/相对表述。"""
    if not raw:
        return None, 0.0
    value = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        return _midnight(int(m[1]), int(m[2]), int(m[3])), 0.85
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_CST)
        return moment.astimezone(timezone.utc).isoformat(), 1.0
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", value)
    if m:
        return _midnight(int(m[1]), int(m[2]), int(m[3])), 0.85
    m = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", value)
    if m:
        return _midnight(int(m[1]), int(m[2]), int(m[3])), 0.85
    m = re.fullmatch(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", value)
    if m:
        return _year_inferred(clock, int(m[1]), int(m[2])).astimezone(
            timezone.utc).isoformat(), 0.65
    for pattern, unit, delta in ((r"(\d+)\s*分钟前", "m", None),
                                 (r"(\d+)\s*小时前", "h", None),
                                 (r"(\d+)\s*天前", "d", None)):
        m = re.fullmatch(pattern, value)
        if m:
            n = int(m[1])
            moment = clock - (timedelta(minutes=n) if unit == "m"
                              else timedelta(hours=n) if unit == "h"
                              else timedelta(days=n))
            return moment.isoformat(), 0.6
    if value in ("今天", "今日"):
        return clock.isoformat(), 0.6
    if value in ("昨天", "昨日"):
        return (clock - timedelta(days=1)).isoformat(), 0.6
    return None, 0.0


def _midnight(year, month, day) -> str:
    return datetime(year, month, day, tzinfo=_CST).astimezone(
        timezone.utc).isoformat()


def _year_inferred(clock: datetime, month: int, day: int) -> datetime:
    """MM-DD 缺年:按北京时区时钟推断,未来 2 天以上视为去年。"""
    today = clock.astimezone(_CST).date()
    candidate = date(today.year, month, day)
    if candidate > today + timedelta(days=2):
        candidate = date(today.year - 1, month, day)
    return datetime.combine(candidate, datetime.min.time(), tzinfo=_CST)
