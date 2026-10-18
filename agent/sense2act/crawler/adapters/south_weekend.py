"""南方周末适配器(自 F 盘 crawler_tool 移植精简版,SSR 搜索路径)。

保留:SSR 结果面板解析 / 登录挑战页识别 / status 脚本空结果识别 /
/contents/ 链接规范化。精简:JSON 翻页变体与详情补抓不移植。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin, urlencode

from lxml import html

from ..base import AdapterResult

_CST = timezone(timedelta(hours=8))
_BASE = "https://www.infzm.com"


class SouthWeekendAdapter:
    platform = "south_weekend"
    adapter_version = "0.1.0-ported"

    def __init__(self, http_get, *, clock: datetime | None = None):
        self._http_get = http_get
        self.clock = clock or datetime.now(timezone.utc)

    def search(self, query: str, limit: int) -> AdapterResult:
        url = f"{_BASE}/search?{urlencode({'k': query})}"
        try:
            resp = self._http_get(url)
        except TimeoutError:
            return AdapterResult(status="network_error", retryable=True,
                                 message="南周请求超时")
        except Exception as exc:
            return AdapterResult(status="network_error", retryable=True,
                                 message=f"南周请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return AdapterResult(status="rate_limited", retryable=True,
                                 message="南周搜索被限流")
        if code in (401, 403):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="南周拒绝匿名搜索")
        if code is not None and code >= 400:
            return AdapterResult(status="network_error", retryable=code >= 500,
                                 message=f"南周搜索返回 HTTP {code}")
        return self._parse_html(resp.text, limit)

    def _parse_html(self, page: str, limit: int) -> AdapterResult:
        if "登录" in page and "密码" in page or "请登录" in page:
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="南周返回登录/挑战页,不对抗")
        tree = html.fromstring(page)
        panel = tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" nfzm-panel--list ")]')
        if not panel:
            if "暂无搜索结果" in page:
                return AdapterResult(status="empty")
            return AdapterResult(status="parser_changed", retryable=False,
                                 message="南周 SSR 结果面板缺失(疑似改版)")
        rows = panel[0].xpath(
            './/*[contains(concat(" ", normalize-space(@class), " "), '
            '" nfzm-list ") and contains(concat(" ", normalize-space(@class),'
            ' " "), " ui-line ")]/li')
        items = []
        for row in rows[:limit]:
            links = row.xpath('.//a[contains(@href, "/contents/")]')
            if not links:
                continue
            href = links[0].get("href") or ""
            url = urljoin(_BASE + "/", href)
            m = re.search(r"/contents/(\d+)", url)
            if not m:
                continue
            titles = row.xpath(
                '//*[contains(concat(" ", normalize-space(@class), " "), '
                '" nfzm-content-item__title ")]//h5')
            descs = row.xpath(
                '//*[contains(concat(" ", normalize-space(@class), " "), '
                '" nfzm-content-item__description ")]')
            metas = [" ".join(n.itertext()).strip() for n in row.xpath(
                '//*[contains(concat(" ", normalize-space(@class), " "), '
                '" nfzm-content-item__meta ")]/span')]
            title = _text(titles[0]) if titles else ""
            description = _text(descs[0]) if descs else ""
            if not title:
                continue
            date_text = metas[1] if len(metas) > 1 else None
            items.append({
                "platform": self.platform,
                "source": "www.infzm.com",
                "url": url,
                "title": title,
                "content": description or title,   # 摘要级
                "published_at": _parse_date(date_text, self.clock) or "",
                "author_type": "媒体",
                "dedup_key": f"south_weekend:{m.group(1)}",
            })
        return AdapterResult(status="success" if items else "empty", items=items)


def _text(node) -> str:
    value = " ".join(node.itertext()).strip()
    return unescape(value) or ""


def _parse_date(raw, clock: datetime) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        return datetime(int(m[1]), int(m[2]), int(m[3]),
                        tzinfo=_CST).astimezone(timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(value).astimezone(
            timezone.utc).isoformat()
    except ValueError:
        return None
