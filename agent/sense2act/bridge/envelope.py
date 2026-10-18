"""后端调查 context → 调查信封(纯代码映射,不走 LLM)。

信号命中(hits)由 signal-service 写入,detail 约定带 score / explanation / reliable:
- 招投标信号:还原成 TenderSignal 的四个检测器——检测解释因此成为调查的第一批证据(检测-调查置信贯通);
- 新闻信号(rule_type=news_event):detail.rumor 即 RumorSignal 载荷,线索卡成为第一批证据。
"""
from __future__ import annotations

from ..models.envelope import (AmountHistory, DetectorScore, FrequencyHistory, InvestigationEnvelope,
                               NoveltyHistory, RumorSignal, TenderSignal)

# 后端 rule_type → TenderSignal 字段
DETECTORS = {
    "amount_anomaly": "amount_anomaly",
    "frequency_burst": "frequency_anomaly",
    "novelty": "novelty_anomaly",
    "semantic_match": "relevance",
}


def _detector(hit: dict) -> DetectorScore:
    detail = hit.get("detail") or {}
    raw = detail.get("score", hit.get("weight", 0))
    score = min(max(float(raw if raw is not None else 0), 0.0), 1.0)
    return DetectorScore(
        score=round(score, 4),
        explanation=str(detail.get("explanation") or f"{hit.get('rule_type')} 命中(权重 {hit.get('weight')})"),
        basis_sufficient=bool(detail.get("reliable", True)))


def _float(value) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


NEWS_RULE = "news_event"


def signal_kind(context: dict) -> str:
    """信号类型:带新闻事件命中的是 rumor(传闻/动态核实),其余按招投标处理。"""
    hits = (context.get("signal") or {}).get("hits") or []
    return "rumor" if any(h.get("rule_type") == NEWS_RULE for h in hits) else "tender"


def build_envelope(context: dict, event_id: str) -> InvestigationEnvelope:
    signal = context.get("signal") or {}
    doc = signal.get("document") or {}
    if not doc:
        raise ValueError("调查 context 缺少信号文档,无法装配信封")
    hits = signal.get("hits") or []
    if signal_kind(context) == "rumor":
        rumor = next(h for h in hits if h.get("rule_type") == NEWS_RULE).get("detail", {}).get("rumor")
        if not rumor:
            raise ValueError("新闻信号缺少 rumor 载荷(signal-service/bridge/news.py 写入),无法装配信封")
        return InvestigationEnvelope(kind="rumor", event_id=event_id, rumor=RumorSignal.model_validate(rumor))
    detectors = {DETECTORS[h["rule_type"]]: _detector(h) for h in hits if h.get("rule_type") in DETECTORS}
    details = {h.get("rule_type"): h.get("detail") or {} for h in hits}

    amount = details.get("amount_anomaly", {})
    novelty = details.get("novelty", {})
    frequency = details.get("frequency_burst", {})
    relevance = details.get("semantic_match", {})
    summary = (doc.get("content_text") or "").strip()
    tender = TenderSignal(
        project_id=doc["id"],
        project_name=doc.get("title") or doc["id"],
        purchaser=doc.get("org_name") or "",
        amount_yuan=_float(doc.get("amount")),
        published_date=str(doc.get("publish_date") or ""),
        domain=doc.get("category") or "",
        project_summary=summary[:1500] if summary else "(公告正文未采集)",
        source_url=doc.get("url") or "",
        company_profile=str(relevance.get("profile") or ""),
        amount_history=AmountHistory(
            historical_median_amount_yuan=_float(amount.get("historical_median")),
            amount_ratio=_float(amount.get("ratio")),
            history_count=amount.get("samples")) if amount else None,
        novelty_history=NoveltyHistory(
            history_count=novelty.get("samples"),
            max_history_similarity=_float(novelty.get("max_similarity"))) if novelty else None,
        frequency_history=FrequencyHistory(
            window_days=frequency.get("window_days"), recent_count=frequency.get("recent_count"),
            historical_window_average_count=_float(frequency.get("historical_average")),
            frequency_ratio=_float(frequency.get("ratio"))) if frequency else None,
        **detectors,
    )
    return InvestigationEnvelope(kind="tender", event_id=event_id, tender=tender)
