"""适配器条目 + 详情页 → POST /internal/documents 的文档条目(字段见 api-design §9.1)。

纯函数、零网络:抓取在 worker,这里只做确定性映射。取不到的字段留空,不猜。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import trafilatura
from sense2act.crawler.adapters.ccgp import parse_detail_body, parse_detail_fields

CST = timezone(timedelta(hours=8))
# 详情页的拦截/挑战页特征:命中即视为未取到详情(与各适配器的识别口径一致)
CHALLENGE_MARKERS = ("验证码", "安全验证", "频繁访问", "访问异常", "SafeLineChallenge")
# 采购人:公告正文"采购人(名称/信息):XXX"的常见写法,只收以机构后缀结尾的名字
_BUYER_RE = re.compile(
    r"采购(?:人|单位)(?:名称|信息)?[：:\s]*(?:名\s*称[：:\s]*)?"
    r"([一-龥（）()]{2,38}?(?:医院|学校|学院|大学|中心|委员会|局|公司|集团|政府|办公室|研究院|研究所"
    r"|管理处|管理站|馆|厅|院|委|所|站|处|部|队|社|会))(?=[\s，,。;；:：]|地址|联系|$)")


def publish_date(published_at: str | None) -> str | None:
    """适配器的 UTC ISO 时刻 → 北京日历日(后端 publish_date);不可解析返回 None。"""
    if not published_at:
        return None
    try:
        moment = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CST)
    return moment.astimezone(CST).date().isoformat()


def is_challenge(page: str) -> bool:
    return any(marker in page[:5000] for marker in CHALLENGE_MARKERS)


def buyer_from_text(text: str) -> str | None:
    match = _BUYER_RE.search(text or "")
    return match.group(1) if match else None


def detail_fields(page: str) -> dict:
    """详情页 → 正文、预算(元)、投标截止(日)、采购人。正文优先 ccgp 公告容器,否则通用抽取。"""
    body = parse_detail_body(page)
    if not body:
        try:
            body = trafilatura.extract(page) or ""
        except Exception:  # noqa: BLE001 —— 抽取失败只是没有正文,不影响列表级字段
            body = ""
    fields = parse_detail_fields(page)
    deadline = (fields.get("deadline") or "")[:10] or None
    budget = fields.get("budget_yuan")
    return {"content_text": body.strip() or None,
            "amount": f"{budget:.2f}" if budget is not None else None,
            "deadline": deadline,
            "buyer": buyer_from_text(body),
            "project_no": fields.get("project_no") or None}


def to_document(item: dict, page: str | None, *, doc_type: str, category: str | None,
                region: str | None, adapter: str) -> dict:
    """一条采集结果 → 文档条目。page 为详情页原文(没取到为 None,文档即列表级、无快照)。"""
    detail = detail_fields(page) if page else {}
    title = " ".join(str(item.get("title") or "").split())[:500]
    meta = {k: item[k] for k in ("stage", "opportunity", "project_no", "author_type") if item.get(k)}
    if detail.get("project_no"):
        meta["project_no"] = detail["project_no"]
    return {
        "url": item["url"],
        "doc_type": doc_type,
        "title": title,
        "org_name": item.get("buyer") or detail.get("buyer"),
        "amount": detail.get("amount"),
        "publish_date": publish_date(item.get("published_at")),
        "deadline": detail.get("deadline"),
        "region": item.get("province") or region,
        "category": category,
        # 列表级条目只有标题 + 结构化后缀;详情抓到正文才用正文
        "content_text": detail.get("content_text") or item.get("content") or title,
        "raw_html": page,
        # 以下进后端 raw(原始字段)留痕:来源平台、站点、阶段等
        "platform": item.get("platform") or adapter,
        "site": item.get("source"),
        "detail_fetched": page is not None,
        **meta,
    }
