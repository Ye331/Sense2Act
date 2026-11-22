"""台账终态 → 后端 ReportDraft(契约 §9.3)。

claim 三态 → 报告 nature(事实与推测分开,不伪装确定):
    已证实                      → fact
    矛盾(查清了,结论为假)       → fact,文本标“查证不成立”
    待证实且有支持证据          → inference
    待证实且无支持证据          → speculation
每条 claim 必须引用已登记证据;一条也引用不到的 claim 不进报告(它已作为 unresolved 问题留在调查里)。
"""
from __future__ import annotations

import re
from .reporter import StepReporter
from .report_projection import attachments, conclusions, project_claims

NATURE = {"已证实": ("fact", 0.9), "矛盾": ("fact", 0.85)}


def _plain(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown or "", flags=re.M)
    text = re.sub(r"\*\*|`|〔[^〕]*〕", "", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _pack_summary(parts: list[str], footer: str, limit: int = 2000) -> str:
    """按完整行装入 summary，绝不在半句或引用定位符中间硬切。"""
    footer = footer.strip()
    if len(footer) > limit:
        footer = "结论状态与停止原因详见调查记录。"
    budget = limit - len(footer) - (1 if footer else 0)
    kept: list[str] = []
    used = 0
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        extra = len(part) + (1 if kept else 0)
        if used + extra > budget:
            continue
        kept.append(part)
        used += extra
    body = "\n".join(kept)
    return "\n".join(x for x in (body, footer) if x)


def _day(value: str | None) -> dict:
    day = (value or "")[:10]
    return {"start_date": day} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else {}


def _framing(envelope) -> tuple[str, str, dict]:
    """按信封类型取:报告标题主语、没有扩展结论时的兜底事实、事件图谱(事件 + 主体)。"""
    if envelope.kind == "rumor" and envelope.rumor:
        rumor = envelope.rumor
        clue = rumor.clues[0] if rumor.clues else None
        headline = f"{rumor.subject_name}·{clue.title}" if clue and clue.title else rumor.summary[:80]
        fallback = f"{rumor.source} 报道《{(clue.title if clue else rumor.summary)[:200]}》,本轮调查未形成可证实的扩展结论"
        extraction = {
            "event": {"title": headline[:500], "type": "news", **_day(clue.published_at if clue else rumor.signal_time)},
            "entities": [{"name": rumor.subject_name, "type": "org", "role": "participant"}],
            "relations": [{"source": "entity:0", "target": "event", "relation": "参与"}]}
        return headline, fallback, extraction
    tender = envelope.tender
    fallback = f"{tender.purchaser} 发布招标《{tender.project_name}》,本轮调查未形成可证实的扩展结论"
    extraction = {
        "event": {"title": tender.project_name[:500], "type": "procurement", **_day(tender.published_date)},
        "entities": [{"name": tender.purchaser or "采购单位", "type": "org", "role": "participant"},
                     {"name": tender.project_name, "type": "project", "role": "object"}],
        "relations": [{"source": "entity:0", "target": "event", "relation": "参与"},
                      {"source": "entity:1", "target": "event", "relation": "同属"}]}
    return tender.project_name, fallback, extraction


def build_report(reporter: StepReporter, envelope, summary: dict | None, stop_reason: str) -> dict:
    ledger, event_id = reporter.ledger, reporter.event_id
    headline, _fallback, extraction = _framing(envelope)
    claims, pending_findings, process_notes = project_claims(reporter, envelope)
    conclusion_views = conclusions(
        reporter, {c["source_claim_id"] for c in claims})

    standings = (summary or {}).get("standings") or ledger.hypothesis_standings(event_id)
    leading = next((s for s in standings if s.get("dominant")), None) or \
        max(standings, key=lambda s: (s.get("score", 0), s.get("verified", 0)), default=None)
    title = f"{headline}:{leading['label']}" if leading else headline

    conclusion = next((s["draft_text"] for s in ledger.get_sections(event_id) if s["title"] == "结论" and s["draft_text"]), "")
    counts = ledger.status_counts(event_id)
    tally = "、".join(f"{k} {v} 条" for k, v in counts.items()) or "无"
    selected = next((v for v in conclusion_views if v.get("selected")), None)
    conclusion_parts = []
    if selected:
        conclusion_parts = [f"当前判断：{selected['current_judgment']}"]
        for key, label in (("basis", "依据"), ("gaps", "缺口"),
                           ("change_conditions", "改变条件"), ("next_steps", "下一步")):
            values = selected.get(key) or []
            if values:
                conclusion_parts.append(f"{label}：" + "".join(values))
    if not conclusion_parts and conclusion:
        conclusion_parts = [line for line in _plain(conclusion).splitlines()
                            if line.strip()]
    summary_text = _pack_summary(
        conclusion_parts, f"结论状态:{tally}。停止原因:{stop_reason}")

    suggestions = [{"text": r["action"][:1000], "priority": "high" if i == 0 else "medium"}
                   for i, r in enumerate((summary or {}).get("recommendations") or []) if r.get("action")]

    return {
        "title": title[:500],
        "summary": summary_text,
        "conclusions": conclusion_views,
        "claims": claims,
        "pending_findings": pending_findings,
        "process_notes": process_notes,
        "attachments": attachments(reporter),
        "action_suggestions": suggestions,
        "event_extraction": extraction,
    }
