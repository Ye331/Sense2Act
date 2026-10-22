"""检测记录 → 后端信号命中(POST /internal/signals 的 hits)。

detail 的 score / reliable / explanation 三个键是与 Agent 的约定:Agent 把它们还原成
TenderSignal 的检测器,检测解释即成为调查的第一批证据(sense2act/bridge/envelope.py)。
只上报依据可靠的指标;不可靠的不补零、不伪造命中。
"""
from __future__ import annotations

import math

# 检测指标 → 后端 rule_type(novelty 后端暂无规则类型,rule_id 留空,只记 detector)
RULE_TYPES = {"amount": "amount_anomaly", "frequency": "frequency_burst", "novelty": "novelty", "relevance": "semantic_match"}


def _num(value, digits=4):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _money(value) -> str:
    number = _num(value, 2)
    return "未披露" if number is None else f"{number:,.2f} 元"


def record_hits(record: dict, weights: dict[str, float], profile: str, rules: dict[str, dict]) -> list[dict]:
    hits = []

    def add(kind: str, weight: float, detail: dict):
        rule = rules.get(RULE_TYPES[kind], {})
        # 命中记录实际参与总分的权重，与缺失指标后的归一计算保持一致。
        weight /= record['scoring_weight_sum']
        hits.append({"rule_type": RULE_TYPES[kind], "rule_id": rule.get("id"), "rule_version": rule.get("version"),
                     "weight": _num(weight, 3) or 0.0, "detail": {k: v for k, v in detail.items() if v is not None}})

    if record.get("amount_reliable"):
        add("amount", weights["amount"], {
            "score": _num(record["amount_score"]), "reliable": True, "amount": _num(record["amount_yuan"], 2),
            "historical_median": _num(record["historical_median_amount"], 2), "ratio": _num(record["amount_ratio"], 3),
            "samples": int(record["amount_history_count"]),
            "explanation": f"金额 {_money(record['amount_yuan'])},为同单位 {int(record['amount_history_count'])} 条历史"
                           f"中位数 {_money(record['historical_median_amount'])} 的 {record['amount_ratio']:.2f} 倍"})
    if record.get("novelty_reliable"):
        similar = record.get("most_similar_history_project") or ""
        add("novelty", weights["novelty"], {
            "score": _num(record["novelty_score"]), "reliable": True, "max_similarity": _num(record["max_history_similarity"]),
            "samples": int(record["novelty_history_count"]), "most_similar_title": similar or None,
            "most_similar_url": record.get("most_similar_history_url") or None,
            "explanation": f"与同单位 {int(record['novelty_history_count'])} 个历史项目相比,最相似为“{similar}”"
                           f"(相似度 {record['max_history_similarity']:.3f}),属较新的采购方向"})
    if record.get("frequency_reliable"):
        add("frequency", weights["frequency"], {
            "score": _num(record["frequency_score"]), "reliable": True, "recent_count": int(record["recent_count"]),
            "historical_average": _num(record["historical_frequency"], 3), "ratio": _num(record["frequency_ratio"], 3),
            "window_days": int(record["frequency_window"]),
            "explanation": f"近 {int(record['frequency_window'])} 天该单位项目 {int(record['recent_count'])} 个,"
                           f"历史窗口均值 {record['historical_frequency']:.2f} 个(比值 {record['frequency_ratio']:.2f})"})
    add("relevance", weights.get("relevance", 0.0), {
        "score": _num(record["relevance_score"]), "reliable": True, "threshold": _num(record["relevance_threshold"]),
        "profile": profile,
        "deadline": record.get("deadline"), "evaluation_date": record["evaluation_date"],
        "deadline_status": record["deadline_status"], "deadline_reason": record["deadline_reason"],
        "explanation": f"与关注画像的语义相关性 {record['relevance_score']:.3f}(门槛 {record['relevance_threshold']:.2f});"
                       f"北京时间评估日期 {record['evaluation_date']},截止日期 {record.get('deadline') or '未知'}:"
                       f"{record['deadline_reason']}"})
    return hits
