"""搜狗微信适配器(自 F 盘 crawler_tool 移植精简版,匿名尽力而为)。

保留:诊断式分类(限流挑战/登录/空结果/结构改版,诚实不对抗)/
timeConvert 时间戳/合成稳定 ID/包裹链接不解析(解析一次一请求,不入预算)。
范围=发现 only:标题/账号/时间/摘要。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlencode

from bs4 import BeautifulSoup

from ..base import AdapterResult

_BASE = "https://weixin.sogou.com"


class SogouWechatAdapter:
    platform = "sogou_wechat"
    adapter_version = "0.1.0-ported"

    def __init__(self, http_get):
        self._http_get = http_get

    def search(self, query: str, limit: int) -> AdapterResult:
        url = f"{_BASE}/weixin?{urlencode({'type': 2, 'query': query})}"
        try:
            resp = self._http_get(url)
        except TimeoutError:
            return AdapterResult(status="network_error", retryable=True,
                                 message="搜狗请求超时")
        except Exception as exc:
            return AdapterResult(status="network_error", retryable=True,
                                 message=f"搜狗请求失败: {type(exc).__name__}")
        code = resp.status_code
        if code == 429:
            return AdapterResult(status="rate_limited", retryable=False,
                                 message="搜狗微信搜索被限流")
        if code in (401, 403):
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="搜狗拒绝匿名搜索")
        if code is not None and code >= 400:
            return AdapterResult(status="network_error", retryable=code >= 500,
                                 message=f"搜狗搜索返回 HTTP {code}")
        return self._parse_html(resp.text, limit)

    def _parse_html(self, page: str, limit: int) -> AdapterResult:
        soup = BeautifulSoup(page, "html.parser")
        blocks = soup.select("div.txt-box")
        anchors = soup.select('div.txt-box h3 a[uigs^="article_title_"]')
        if any(m in page for m in ("antispider", "请输入验证码",
                                   "访问过于频繁", "您的访问出错了")):
            return AdapterResult(status="rate_limited", retryable=False,
                                 message="搜狗返回挑战页,即停不对抗")
        if "请登录" in page:
            return AdapterResult(status="authentication_required", retryable=False,
                                 message="搜狗要求登录")
        if not (blocks and anchors):
            if "没有找到" in page and 'id="wrapper"' in page:
                return AdapterResult(status="empty")
            return AdapterResult(status="parser_changed", retryable=False,
                                 message="搜狗微信结果结构缺失或改版")
        items = []
        for block in blocks[:limit]:
            anchor = block.select_one('h3 a[uigs^="article_title_"]')
            if anchor is None:
                continue
            title = " ".join(anchor.get_text(" ", strip=True).split())
            href = anchor.get("href", "")
            if not title or not href:
                continue
            url = urljoin(f"{_BASE}/", href)
            time_match = re.search(r"timeConvert\('(\d+)'\)", str(block))
            published_at = _timestamp(
                time_match.group(1) if time_match else None)
            account_node = block.select_one("div.s-p span.all-time-y2")
            account = _account(account_node.get_text(" ", strip=True)
                               if account_node else None)
            summary_node = block.select_one(".txt-info")
            summary = " ".join(
                summary_node.get_text(" ", strip=True).split()) \
                if summary_node else ""
            source_id = "sg_" + hashlib.sha256(
                f"{account or ''}|{title}|{published_at or ''}".encode()
            ).hexdigest()[:16]
            items.append({
                "platform": self.platform,
                "source": "weixin.sogou.com",
                "url": url,                       # 搜狗包裹链接,不解析
                "title": title,
                "content": summary or title,      # 摘要级
                "published_at": published_at or "",
                "author_type": account or "",
                "dedup_key": f"sogou_wechat:{source_id}",
            })
        return AdapterResult(status="success" if items else "empty", items=items)


def _timestamp(value: str | None) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat() \
            if value else None
    except (TypeError, ValueError, OSError):
        return None


def _account(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.rstrip("：:").strip() or None
