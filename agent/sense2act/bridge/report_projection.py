"""Agent 台账到 ReportDraft v2 的只读投影。

这里集中执行对外报告的证据治理：claim 只能引用与其同向、已登记的专属证据；
信封 E1 仅能支撑可由白名单字段确定性核对的基础事实。图片附件只投影元数据，
不参与 claim 状态与证据判定。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import PurePosixPath
from types import SimpleNamespace

from ..config import load_domain_config
from ..conclusion_present import build_conclusion_views
from ..stages.s7_report import ReportBuilder, _claim_tier, _load_report_phrases


def _contains_value(text: str, value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    variants = {raw, raw.replace(",", "")}
    try:
        amount = float(raw)
        variants.update({str(amount), f"{amount:g}", f"{amount:,.2f}", f"{amount:.2f}"})
    except ValueError:
        pass
    compact = text.replace(",", "").replace("，", "")
    return any(v and (v in text or v.replace(",", "") in compact) for v in variants)


def envelope_fact(text: str, envelope) -> bool:
    """仅认可“字段锚 + 信封原值”同时命中的确定性基础事实。"""
    if envelope.kind == "tender" and envelope.tender:
        t = envelope.tender
        fields = (
            (("项目名称", "项目名", "招标项目"), t.project_name),
            (("采购人", "采购方", "采购单位"), t.purchaser),
            (("预算", "金额"), t.amount_yuan),
            (("发布日期", "公告日期", "发布于"), t.published_date),
            (("项目编号",), t.project_id),
        )
    elif envelope.kind == "rumor" and envelope.rumor and envelope.rumor.clues:
        clue = envelope.rumor.clues[0]
        fields = (
            (("线索标题", "报道标题", "标题"), clue.title),
            (("监测对象", "主体"), envelope.rumor.subject_name),
            (("发布日期", "发布时间", "发布于"), clue.published_at[:10]),
        )
    else:
        return False
    return any(any(anchor in text for anchor in anchors) and _contains_value(text, value)
               for anchors, value in fields)


def fallback_fact(envelope) -> str:
    """完全没有可输出 claim 时生成一条白名单基础事实。"""
    if envelope.kind == "tender" and envelope.tender:
        return f"项目名称为《{envelope.tender.project_name}》"
    if envelope.kind == "rumor" and envelope.rumor and envelope.rumor.clues:
        return f"线索标题为《{envelope.rumor.clues[0].title}》"
    return ""


_DISTINCT_SOURCE_CLAIM_RE = re.compile(
    r"近[一二三四五六七八九十\d]+年|历史(?:采购|中标|记录|项目)|既往(?:采购|记录|项目)|同等金额.*记录|"
    r"立项|批复|规划|前期采购文件|未被(?:更正|撤销)|(?:未|无).{0,12}(?:更正|撤销)公告")
_CONDITIONAL_CLAIM_RE = re.compile(
    r"(?:若|如果|假如).{0,40}(?:中标|成交|参与|投标|收入|收益|贡献|可行)|"
    r"(?:预计|预期|可能).*(?:收入|贡献|收益)|具备.*(?:报名|投标|递交).*可行条件")


def _specific_evidence_ids(reporter, envelope, text: str,
                           evidence_ids: list[str]) -> list[str]:
    """投影层最后一道蕴含性门：挡住当前公告冒充历史记录及条件预测冒充事实。"""
    if _CONDITIONAL_CLAIM_RE.search(text or ""):
        return []
    if not _DISTINCT_SOURCE_CLAIM_RE.search(text or ""):
        return evidence_ids
    current_url = ""
    if envelope.kind == "tender" and envelope.tender:
        current_url = (envelope.tender.source_url or "").rstrip("/")
    inverse = {backend: local for local, backend in reporter.evidence.items()}
    kept: list[str] = []
    for backend_id in evidence_ids:
        local_id = inverse.get(backend_id)
        row = reporter.ledger.evidence_row(local_id) if local_id else None
        if row is None:
            continue
        url = (row["url"] or "").rstrip("/")
        # 历史事实至少要落在另一份材料上；同一目标公告重复抓取仍是一件当前材料。
        if url and (not current_url or url != current_url):
            kept.append(backend_id)
    return kept


def _conclusion_context(reporter):
    ledger, event_id = reporter.ledger, reporter.event_id
    domain = load_domain_config(ledger.config_dir)
    builder = ReportBuilder.__new__(ReportBuilder)
    builder._ledger = ledger
    builder._domain = domain
    builder._config = SimpleNamespace(
        kb_enabled=False, kb_path="", t1_independence_enabled=False)
    graph = builder._chain_graph(event_id)  # noqa: SLF001
    argument = (graph or {}).get("argument")
    overview = ((graph or {}).get("overview") or {}).get("hypotheses") or []
    if not graph or not argument or not overview:
        return None

    claims = {c["claim_id"]: dict(c) for c in ledger.all_claims(event_id)}
    standings = {s["hypothesis_id"]: s for s in ledger.hypothesis_standings(event_id)
                 if s.get("emerged")}
    members: dict[str, list[str]] = {}
    for row in ledger.conn.execute(
            "SELECT hc.hypothesis_id, hc.claim_id FROM hypothesis_claims hc"
            " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
            " WHERE h.event_id=? ORDER BY hc.hypothesis_id, hc.claim_id", (event_id,)):
        members.setdefault(row["hypothesis_id"], []).append(row["claim_id"])
    inputs = []
    for hyp in overview:
        hid = hyp["hypothesis_id"]
        standing = standings.get(hid) or {}
        inputs.append({
            "hypothesis_id": hid, "label": hyp.get("label", hid),
            "description": standing.get("description", ""),
            "state": hyp.get("state", ""), "score": hyp.get("score", 0),
            "chosen": hid == argument.get("hypothesis_id"),
            "member_ids": members.get(hid, []),
        })
    rules = _load_report_phrases(ledger.config_dir)
    excluded = {cid for cid, claim in claims.items()
                if _claim_tier(claim.get("text") or "", rules)}
    meta = graph.get("meta") or {}
    as_of_round = meta.get("as_of_round", "latest")
    snapshot = f"{meta.get('as_of') or 'unknown'}|round:{as_of_round}"
    views = build_conclusion_views(
        graph, list(claims.values()), inputs, excluded_claim_ids=excluded,
        source_section_id=f"{event_id}:S3", snapshot_version=snapshot)
    return builder, claims, rules, views


def conclusions(reporter, allowed_claim_ids: set[str] | None = None) -> list[dict]:
    """输出与 S7/chain-web 同源的强类型结论视图。"""
    try:
        context = _conclusion_context(reporter)
        if not context:
            return []
        builder, claims, rules, views = context
        gists = reporter.ledger.claim_gists(reporter.event_id)

        def phrases(ids: list[str]) -> list[str]:
            out: list[str] = []
            for cid in ids:
                phrase = builder._card_phrase(cid, claims, gists, rules)  # noqa: SLF001
                if phrase and phrase not in out:
                    out.append(phrase.rstrip("。") + "。")
            return out[:3]

        result = []
        for view in views:
            original_support = list(view["supporting_claim_ids"])
            supporting = (original_support if allowed_claim_ids is None else
                          [cid for cid in original_support if cid in allowed_claim_ids])
            pending_ids = list(view["pending_claim_ids"])
            pending_ids.extend(cid for cid in original_support if cid not in supporting)
            pending_ids = list(dict.fromkeys(pending_ids))
            result.append({
                "hypothesis_id": view["hypothesis_id"],
                "label": view["label"], "selected": bool(view.get("chosen")),
                "current_judgment": view["current_judgment"],
                "basis": phrases(supporting),
                "gaps": phrases(pending_ids),
                "change_conditions": list(view["change_conditions"][:3]),
                "next_steps": list(view["next_steps"][:3]),
                "supporting_claim_ids": supporting,
                "pending_claim_ids": pending_ids,
            })
        return result
    except (AttributeError, KeyError, OSError, RuntimeError, sqlite3.Error,
            TypeError, ValueError):
        return []


def project_claims(reporter, envelope) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (有据事实, 待补证, 过程说明)。"""
    ledger, event_id = reporter.ledger, reporter.event_id
    rules = _load_report_phrases(ledger.config_dir)
    base_local = f"{event_id}:E1"
    base_backend = reporter.evidence.get(base_local)
    verified: list[dict] = []
    pending: list[dict] = []
    process: list[dict] = []

    for raw in ledger.all_claims(event_id):
        row = dict(raw)
        cid, status, text = row["claim_id"], row["status"], row["text"]
        tier = _claim_tier(text or "", rules)
        note = {"source_claim_id": cid, "text": text}
        if tier == "block":
            process.append({**note, "reason": "process_or_meta"})
            continue
        if tier == "rewrite":
            pending.append({**note, "gap": "信封字段不足，尚不能形成业务事实",
                            "next_step": "补充公开材料并核验该项"})
            continue
        if status == "待证实":
            pending.append({**note, "gap": "尚缺足以完成裁定的专属证据",
                            "next_step": "补充独立来源并核验该项"})
            continue

        ruling = "矛盾" if status == "矛盾" else "支持"
        evidence_ids = reporter.claim_evidence(cid, ruling, exact=True)
        if base_backend in evidence_ids and not envelope_fact(text, envelope):
            evidence_ids = [eid for eid in evidence_ids if eid != base_backend]
        evidence_ids = _specific_evidence_ids(reporter, envelope, text, evidence_ids)
        if not evidence_ids:
            diagnostic = ("claim_specific_evidence_missing"
                          if _DISTINCT_SOURCE_CLAIM_RE.search(text or "") or _CONDITIONAL_CLAIM_RE.search(text or "")
                          else "terminal_claim_without_matching_evidence")
            pending.append({**note, "gap": "终态条目缺少方向匹配且实际蕴含该结论的专属证据",
                            "next_step": "修复证据关联后重新核验",
                            "diagnostic": diagnostic})
            continue
        verified.append({
            "source_claim_id": cid,
            "text": (("查证不成立:" if status == "矛盾" else "") + text)[:2000],
            "nature": "fact", "status": "contested" if status == "矛盾" else "verified",
            "confidence": 0.85 if status == "矛盾" else 0.9,
            "evidence_ids": evidence_ids,
        })

    if not verified and base_backend:
        text = fallback_fact(envelope)
        if text:
            verified.append({"source_claim_id": "envelope:base", "text": text,
                             "nature": "fact", "status": "verified", "confidence": 0.95,
                             "evidence_ids": [base_backend]})
    return verified, pending, process


def attachments(reporter) -> list[dict]:
    """把台账图片投影成共享卷内的相对 storage_key。"""
    out: list[dict] = []
    for local_eid, items in reporter.ledger.evidence_assets(reporter.event_id).items():
        backend_eid = reporter.evidence.get(local_eid)
        if not backend_eid:
            continue
        for item in items:
            path = PurePosixPath(str(item.get("path") or "").replace("\\", "/"))
            if not path.parts or path.is_absolute() or ".." in path.parts:
                continue
            kind = str(item.get("kind") or "image")
            ctype = str(item.get("content_type") or "").lower()
            if kind not in {"image", "screenshot"} or ctype not in {
                    "image/png", "image/jpeg", "image/webp"}:
                continue
            storage = PurePosixPath(reporter.inv, "runs", reporter.event_id, *path.parts)
            out.append({
                "evidence_id": backend_eid, "kind": "image",
                "storage_key": storage.as_posix(), "content_type": ctype,
                "sha256": str(item.get("sha256") or ""),
                "size": int(item.get("bytes") or 0),
                "caption": str(item.get("caption") or ""),
                "presentation_only": True,
            })
    return out
