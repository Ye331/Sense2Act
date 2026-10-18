"""捕获信封 -> 证据条目(证据粒度升级通道)。

头条契约(与 F 盘 toutiao_browser_capture.py 产出一致,亦与 F 盘
ToutiaoAdapter.parse_payload 的信封契约相同):
  {"captureType": "toutiao_article_detail", "articleId", "url",
   "title", "content", "publishTime"(ISO, 可选), "author"(可选)}
  正文 <40 字视为未获全文,拒绝入账(空捕获不如不捕获)。

小红书信封:F 盘 xhs_browser_capture.py 的产出字段以脚本为准——此处为
容错解析(标题/URL/正文/时间,字段名宽松匹配),接入真脚本时核对一次。
所有条目带 content_from_browser_auto 警告与通道标记(审计可辨)。
"""
from __future__ import annotations

from ..models.observation import EvidenceItemLite
from urllib.parse import urlparse

MIN_BODY_CHARS = 40


class CaptureEnvelopeError(ValueError):
    pass


def toutiao_detail_to_lite(envelope: dict) -> EvidenceItemLite:
    if not isinstance(envelope, dict):
        raise CaptureEnvelopeError("信封不是对象")
    if envelope.get("captureType") != "toutiao_article_detail":
        raise CaptureEnvelopeError(
            f"未知的头条捕获类型: {envelope.get('captureType')!r}")
    title = str(envelope.get("title") or "").strip()
    content = str(envelope.get("content") or "").strip()
    url = str(envelope.get("url") or "").strip()
    article_id = str(envelope.get("articleId") or "").strip()
    if not (title and content and (article_id or url)):
        raise CaptureEnvelopeError("信封缺 title/content/articleId")
    if len("".join(content.split())) < MIN_BODY_CHARS:
        raise CaptureEnvelopeError(
            f"正文过短({len(content)} 字 <{MIN_BODY_CHARS}),未获全文,拒绝入账")
    from ..crawler.adapters.toutiao import _source_id
    return EvidenceItemLite(
        platform="toutiao",
        source=_domain(url) or "www.toutiao.com",
        url=url or f"https://www.toutiao.com/article/{article_id}/",
        title=title,
        content=content,                              # 全文级(升级)
        published_at=str(envelope.get("publishTime") or ""),
        author_type=str(envelope.get("author") or ""),
        dedup_key=f"toutiao:{article_id or _source_id(url) or _hash(title)}",
    )


def xhs_items_to_lite(envelope: dict) -> list[EvidenceItemLite]:
    """小红书搜索捕获(容错解析;字段契约以 F 盘脚本为准,接入时核对)。"""
    if not isinstance(envelope, dict):
        raise CaptureEnvelopeError("信封不是对象")
    raw_items = envelope.get("items") or envelope.get("results") or []
    if not isinstance(raw_items, list):
        raise CaptureEnvelopeError("信封 items 不是列表")
    out: list[EvidenceItemLite] = []
    for pos, raw in enumerate(raw_items[:20]):        # 硬限:单次≤20 条
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or raw.get("noteTitle") or "").strip()
        url = str(raw.get("url") or raw.get("noteUrl") or "").strip()
        content = str(raw.get("content") or raw.get("desc") or "").strip()
        if not title:
            continue
        note_id = str(raw.get("noteId") or raw.get("id") or "")
        out.append(EvidenceItemLite(
            platform="xiaohongshu",
            source="xiaohongshu.com",
            url=url,
            title=title,
            content=content or title,
            published_at=str(raw.get("publishTime") or raw.get("time") or ""),
            author_type=str(raw.get("author") or raw.get("nickName") or ""),
            dedup_key=f"xhs:{note_id or _hash(title)}",
        ))
    return out


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
