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

NATURE = {"已证实": ("fact", 0.9), "矛盾": ("fact", 0.85)}


def _plain(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown or "", flags=re.M)
    text = re.sub(r"\*\*|`|〔[^〕]*〕", "", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def build_report(reporter: StepReporter, envelope, summary: dict | None, stop_reason: str) -> dict:
    ledger, event_id = reporter.ledger, reporter.event_id
    tender = envelope.tender
    # 信封基础字段证据(E1,走 doc_id 登记)是所有 claim 的兜底依据
    base = reporter.evidence.get(f"{event_id}:E1")

    claims = []
    for row in ledger.all_claims(event_id):
        status = row["status"]
        supporting = reporter.claim_evidence(row["claim_id"], "矛盾" if status == "矛盾" else "支持")
        evidence_ids = supporting or ([base] if base else [])
        if not evidence_ids:
            continue
        if status in NATURE:
            nature, confidence = NATURE[status]
        elif supporting:
            nature, confidence = "inference", 0.6
        else:
            nature, confidence = "speculation", 0.3
        text = ("查证不成立:" if status == "矛盾" else "") + row["text"]
        claims.append({"text": text[:2000], "nature": nature, "confidence": confidence, "evidence_ids": evidence_ids})
    if not claims and base:
        claims.append({"text": f"{tender.purchaser} 发布招标《{tender.project_name}》,本轮调查未形成可证实的扩展结论",
                       "nature": "fact", "confidence": 0.95, "evidence_ids": [base]})

    standings = (summary or {}).get("standings") or ledger.hypothesis_standings(event_id)
    leading = next((s for s in standings if s.get("dominant")), None) or \
        max(standings, key=lambda s: (s.get("score", 0), s.get("verified", 0)), default=None)
    title = f"{tender.project_name}:{leading['label']}" if leading else tender.project_name

    conclusion = next((s["draft_text"] for s in ledger.get_sections(event_id) if s["title"] == "结论" and s["draft_text"]), "")
    counts = ledger.status_counts(event_id)
    tally = "、".join(f"{k} {v} 条" for k, v in counts.items()) or "无"
    summary_text = (_plain(conclusion) + "\n" if conclusion else "") + f"结论状态:{tally}。停止原因:{stop_reason}"

    suggestions = [{"text": r["action"][:1000], "priority": "high" if i == 0 else "medium"}
                   for i, r in enumerate((summary or {}).get("recommendations") or []) if r.get("action")]

    entities = [{"name": tender.purchaser or "采购单位", "type": "org", "role": "participant"},
                {"name": tender.project_name, "type": "project", "role": "object"}]
    return {
        "title": title[:500],
        "summary": summary_text[:2000],
        "claims": claims,
        "action_suggestions": suggestions,
        "event_extraction": {
            "event": {"title": tender.project_name[:500], "type": "procurement",
                      **({"start_date": tender.published_date} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", tender.published_date or "") else {})},
            "entities": entities,
            "relations": [{"source": "entity:0", "target": "event", "relation": "参与"},
                          {"source": "entity:1", "target": "event", "relation": "同属"}],
        },
    }
