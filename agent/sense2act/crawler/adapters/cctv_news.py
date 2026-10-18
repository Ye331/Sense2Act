"""央视新闻适配器(自 F 盘 crawler_tool 移植;v2:全栏目扫查)。

原版要点保留:匿名读栏目静态 JSONP 列表(无鉴权/无签名/未见挑战),
本地按标题/摘要过滤使 search 语义成立;匹配不到如实 empty(附候选数)。
v2 变更(2026-09-27 实测驱动):单栏目(最新国内列表 80 条)对具体查询的
命中率过低 -> 扫查全部栏目取并集;单栏目失败不拖垮整体;限流/拒绝即停
(礼貌纪律);跨栏目重复按 标题+URL 去重。
精简:暂不移植详情页补抓(P2)与 media 字段。
"""
from __future__ import annotations

import json

from ..base import AdapterResult

BASE_URL = "https://news.cctv.com"
DATA_FILE_TEMPLATE = BASE_URL + "/2019/07/gaiban/cmsdatainterface/page/{column}_{page}.jsonp"
ALLOWED_COLUMNS = ("china", "world", "society", "economy", "sports", "military", "tech")


class CctvNewsAdapter:
    platform = "cctv_news"
    adapter_version = "0.2.0-ported-sweep"

    def __init__(self, http_get, *, columns: tuple[str, ...] = ALLOWED_COLUMNS):
        self.columns = tuple(c for c in columns if c in ALLOWED_COLUMNS) \
            or ALLOWED_COLUMNS
        self._http_get = http_get

    def search(self, query: str, limit: int) -> AdapterResult:
        items: list[dict] = []
        http_fail = parse_fail = ok_cols = 0
        for col in self.columns:
            try:
                resp = self._http_get(
                    DATA_FILE_TEMPLATE.format(column=col, page=1))
            except TimeoutError:
                return AdapterResult(status="network_error", retryable=True,
                                     message="cctv 请求超时")
            except Exception as exc:
                return AdapterResult(status="network_error", retryable=True,
                                     message=f"cctv 请求失败: {type(exc).__name__}")
            code = resp.status_code
            if code == 429:
                return AdapterResult(status="rate_limited", retryable=True,
                                     message="cctv 列表被限流,礼貌即停")
            if code in (401, 403):
                return AdapterResult(status="authentication_required",
                                     retryable=False,
                                     message="cctv 拒绝匿名列表请求,即停")
            if code is not None and code >= 400:
                http_fail += 1          # 单栏目失败不拖垮整体
                continue
            payload = self._parse_jsonp(resp.text)
            if payload is None:
                parse_fail += 1         # 单栏目改版不拖垮整体
                continue
            ok_cols += 1
            items.extend(self._parse_records(payload, col))

        if not items:
            if parse_fail and parse_fail >= http_fail:
                return AdapterResult(status="parser_changed", retryable=False,
                                     message="cctv 栏目信封均无法解析(疑似改版)")
            if http_fail:
                return AdapterResult(status="network_error", retryable=True,
                                     message=f"cctv 全部 {http_fail} 个栏目请求失败")
            return AdapterResult(status="empty", message="cctv 各栏目列表均为空")

        # 跨栏目去重(同一篇文章可能出现在多个栏目)
        seen: set[str] = set()
        uniq = []
        for item in items:
            key = f"{item['title']}|{item['url']}"
            if key not in seen:
                seen.add(key)
                uniq.append(item)
        return self._filter_by_query(uniq, query, limit, ok_cols)

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_jsonp(text: str) -> dict | None:
        start, end = text.find("("), text.rfind(")")
        if start == -1 or end <= start:
            return None
        try:
            payload = json.loads(text[start + 1: end])
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _parse_records(self, payload: dict, column: str) -> list[dict]:
        data = payload.get("data")
        records = data.get("list") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return []
        items = []
        for record in records:
            if not isinstance(record, dict):
                continue
            title = " ".join(str(record.get("title") or "").split())
            url = record.get("url")
            source_id = record.get("id")
            if not title or not url or not source_id:
                continue
            brief = " ".join(str(record.get("brief") or "").split())
            items.append({
                "platform": self.platform,
                "source": "news.cctv.com",
                "url": url,
                "title": title,
                "content": brief or title,       # 摘要级证据
                "published_at": str(record.get("focus_date") or ""),
                "author_type": "官方媒体",
                "dedup_key": f"cctv_news:{column}:{source_id}",
            })
        return items

    def _filter_by_query(self, items: list[dict], query: str, limit: int,
                         scanned: int) -> AdapterResult:
        raw = str(query or "").strip()
        if not raw:
            # 空查询不得伪装成功并全量返回(与 tianjin_zfcg 同纪律)
            return AdapterResult(
                status="empty",
                message=f"查询词为空,拒绝全量返回"
                        f"(扫查 {scanned} 个栏目共 {len(items)} 条)")
        tokens = raw.split()
        matched = [
            item for item in items
            if any(t.casefold() in f"{item['title']} {item['content']}".casefold()
                   for t in tokens)
        ]
        if not matched:
            return AdapterResult(
                status="empty",
                message=f"扫查 {scanned} 个栏目共 {len(items)} 条,没有匹配关键词的条目")
        return AdapterResult(status="success", items=matched[:limit])
