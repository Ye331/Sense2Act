"""真实采集客户端:对接 crawler_tool 服务(外部服务,不 import 不修改)。

契约(与 crawler_tool 的 POST /api/v1/tool/search-content 对齐):
- 请求 {query, limit, freshness:"prefer_fresh", platforms?};
- 响应 {items: [ContentItem...], status(success|partial|failed), sourceReports: [...]}
- ContentItem(content.v1, camelCase) -> EvidenceItemLite 的字段映射在本模块完成
  (source 取 URL 域名 -> S4 据此打谱系根与可信度;dedupKey 优先用采集侧稳定键)。

唯一出网纪律:本客户端只应被 CrawlerGate 包裹使用(S3 不直连)。
失败回执与成功同构(network_error + retryable),不伪装成 empty。
"""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit

from ..models.observation import (
    CollectReceipt,
    CollectRequest,
    EvidenceItemLite,
    SourceReport,
)


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def contentitem_to_lite(item: dict) -> EvidenceItemLite:
    """ContentItem(content.v1) -> 观察包条目(S4 加工前的形态)。"""
    url = item.get("url") or ""
    dedup = (item.get("dedup") or {}).get("dedupKey") or item.get("contentId") or ""
    author = item.get("author") or {}
    content = item.get("content") or item.get("summary") or item.get("title") or ""
    return EvidenceItemLite(
        platform=item.get("platform") or "other",
        source=_domain(url) or item.get("platform") or "",
        url=url,
        title=item.get("title") or "",
        content=content,
        published_at=item.get("publishedAt") or "",
        author_type=("认证账号" if author.get("verified") else ""),
        dedup_key=dedup,
    )


class CrawlerHttpClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8301", timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def search(self, request: CollectRequest) -> CollectReceipt:
        payload: dict = {
            "query": request.query,
            "limit": request.limit,
            "freshness": "prefer_fresh",
        }
        if request.platforms:
            payload["platforms"] = request.platforms
        req = urllib.request.Request(
            f"{self._base}/api/v1/tool/search-content",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:  # 失败回执与成功同构,不伪装成 empty
            return CollectReceipt(
                request=request, items=[], tool=request.tool,
                source_reports=[SourceReport(
                    platform=request.platforms[0] if request.platforms else "http",
                    status="network_error", count=0, retryable=True,
                )],
                status="failed",
            )
        items = [contentitem_to_lite(i) for i in data.get("items", [])]
        reports = [SourceReport.model_validate(r)
                   for r in data.get("sourceReports", [])]
        return CollectReceipt(
            request=request, items=items, source_reports=reports,
            status=data.get("status", "success"), tool=request.tool,
        )
