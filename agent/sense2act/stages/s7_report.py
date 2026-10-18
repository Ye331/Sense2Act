"""S7 报告生成 = 审查界面(两阶段之阶段二:出循环后本体)。

五环: ①补齐未起草节 -> ②Open Questions 生成(纯 SQL 零 LLM,禁止抹平)
     -> ③行动建议(溯源式,每条挂依据链) -> ④终稿核对(机械断言快照==台账;
        action_facing 下 STALE 禁交付) -> ⑤交付(report.md + report.html 演示包
        + brief.md 企业简报(报告双形态,交付层视图) + result.json + audit.json
        研判审计包)。

报告双形态纪律(2026-10-10 设计决策):六节固定结构与其架构决策不动——
report.md 是台账的六节三态投影(canonical 叙事),brief.md 是**同一台账的
交付层决策视图**(商机卡片/红旗/建议,全部模板确定性生成,零 LLM、零台账
写入);多视图不改变 claim 的节归属与任何调查行为。运营者事项(如替换
模拟样本)经确定性标记词分流进「内部待办」,不进企业简报。

S6 与 check 的分工边界:S6 问"claim 是真的吗"(claim vs 证据,代码终判);
check 问"文本忠实表达台账了吗"(文本 vs 台账)——报告是台账的投影。
"""
from __future__ import annotations

import html as html_mod
import json
from pathlib import Path
from typing import Protocol

from pydantic import Field

from ..config import DomainConfig, InvestigationConfig
from ..evidence_graph import (
    build_graph_payload,
    graph_diagrams as _graph_diagrams,
    render_section as _graph_section,
)
from ..llm import StructuredLLM, call_structured
from ..models.base import SchemaModel
from ..models.intel import Recommendation
from ..prompts import S7_RECOMMEND_PROMPT
from ..store.ledger import LedgerRepository


class RecommendationsResult(SchemaModel):
    items: list[Recommendation] = Field(default_factory=list)


class RecommendProvider(Protocol):
    def recommend(self, context: dict) -> list[Recommendation]: ...


class LLMRecommendProvider:
    def __init__(self, llm: StructuredLLM, retry: int = 2):
        self._llm = llm
        self._retry = retry

    def recommend(self, context: dict) -> list[Recommendation]:
        out = call_structured(
            self._llm, stage="s7_recommend", system=S7_RECOMMEND_PROMPT,
            user=json.dumps(context, ensure_ascii=False),
            model_cls=RecommendationsResult, retry=self._retry)
        return out.items


_STATUS_BADGE = {"已证实": ("已证实", "ok"), "矛盾": ("矛盾", "bad"),
                 "待证实": ("待证实", "warn")}

# 运营者事项分流标记(报告双形态 P1,2026-10-10):建议文案涉及系统内部数据
# 质量(如替换模拟历史样本)时归「内部待办」,不进企业简报。确定性规则先行,
# prompt 受众字段留待下次录制批(分权:能代码兜底的不先动 prompt)。
_INTERNAL_MARKERS = ("模拟样本", "模拟历史", "模拟数据")


class ReportBuilder:
    def __init__(
        self,
        ledger: LedgerRepository,
        domain: DomainConfig,
        config: InvestigationConfig,
        writer,                     # SectionWriter(draft/check/edit)
        provider: RecommendProvider | None = None,
        runs_dir: str | Path = "runs",
    ):
        self._ledger = ledger
        self._domain = domain
        self._config = config
        self._writer = writer
        self._provider = provider
        self._runs = Path(runs_dir)

    # ------------------------------------------------------------- 主入口

    def finalize(self, event_id: str, round_no: int, stop_reason: str,
                 costs: dict | None = None, envelope_label: str = "",
                 signal_type: str = "", envelope=None) -> dict:
        # 环① 补齐未起草节(结论节从假设记分板起草;空节用信封摘要)
        for sec in self._ledger.pending_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            kind = "conclusion" if sec["ord"] == 3 else sec["kind"]
            context = {
                "event_id": event_id, "title": sec["title"], "kind": kind,
                "claims": self._claim_payload(claims),
                "standings": self._ledger.hypothesis_standings(event_id),
                "envelope_label": envelope_label,
            }
            text = self._writer.draft(context)
            snapshot = {c["draft_id"]: c["status"] for c in claims}
            self._ledger.set_draft(sec["section_id"], text, snapshot, round_no)

        # 环② Open Questions(纯代码零 LLM,按优先级置顶,禁止抹平)
        oq_rows = self._ledger.oq_claims(event_id)
        oq_text = self._render_oq(oq_rows)
        oq_sec = f"{event_id}:S4"
        self._ledger.set_draft(oq_sec, oq_text, {}, round_no)

        # 环③ 行动建议(溯源式:依据链校验不过则丢弃该条)
        recommendations = self._recommend(event_id, envelope_label)
        rec_text = self._render_recommendations(recommendations)
        self._ledger.set_draft(f"{event_id}:S5", rec_text, {}, round_no)

        # 环④ 终稿核对:断言全部 standard 节快照 == 最终台账(不一致即流程 bug)
        self._final_check(event_id, round_no)

        # 环⑤ 交付
        summary = self._summary(event_id, stop_reason, recommendations, costs,
                                signal_type)
        out_dir = self._runs / event_id
        self._deliver(out_dir, event_id, summary, envelope_label,
                      envelope=envelope, oq_rows=oq_rows,
                      rec_items=recommendations)
        return summary

    # ------------------------------------------------------------- 环②③

    def _render_oq(self, rows) -> str:
        lines = ["### Open Questions", "",
                 "以下断言截至交付仍未获足够独立证据,**如实保留,不做推测性结论**:",
                 ""]
        if not rows:
            lines.append("- (无未决问题)")
        for r in rows:
            note = " [来源不可达,已登记缺口]" if r["unreachable"] else ""
            lines.append(
                f"- **{r['draft_id']}**(优先级 {r['priority']}){note}:"
                f"{r['text']}")
        return "\n".join(lines)

    def _recommend(self, event_id: str, envelope_label: str) -> list[Recommendation]:
        standings = self._ledger.hypothesis_standings(event_id)
        claims = [
            {"id": r["draft_id"], "text": r["text"], "status": r["status"]}
            for r in self._ledger.all_claims(event_id)
        ]
        valid_ids = {c["id"] for c in claims}
        context = {
            "event": envelope_label, "standings": standings, "claims": claims,
            "watch_focus": [w.get("focus", "") for w in self._domain.watchlist],
        }
        items: list[Recommendation] = []
        if self._provider is not None:
            try:
                items = self._provider.recommend(context)
            except Exception:
                items = []
        # 校验依据链(建议不能凭空出现);不足则用模板建议兜底
        items = [r for r in items if set(r.basis_claim_ids) & valid_ids]
        if not items and standings:
            # 模板兜底:主导假设优先;无主导(得分<=0)时取最高分假设,
            # 但依据链必须挂在已证实 claim 上——没有已证实项则不出建议
            top = next((s for s in standings if s.get("dominant")), None)
            if top is None:
                top = max(standings, key=lambda s: s["score"])
            verified = [r["draft_id"] for r in self._ledger.all_claims(event_id)
                        if r["status"] == "已证实"]
            if verified:
                lead = "当前证据结构更支持该假设" if top.get("dominant") \
                    else "当前证据倾向该假设(尚不构成主导)"
                items = [Recommendation(
                    action=f"围绕假设「{top['label']}」组织后续跟踪,"
                           f"并对未决项安排补充核查",
                    rationale=f"{lead}(已证实 {top['verified']} 条,"
                              f"矛盾 {top['contradicted']} 条);未决项依据"
                              f" Open Questions 节安排人工补查。",
                    basis_claim_ids=verified[:2],
                    hypothesis_label=top["label"],
                )]
        return items

    def _render_recommendations(self, items: list[Recommendation]) -> str:
        lines = ["### 行动建议", "",
                 "每条建议附依据链(可沿 [Ex] 追溯到证据原文);建议供决策参考,"
                 "不替代企业决策。", ""]
        if not items:
            lines.append("- (无足以支撑行动建议的已查实结论)")
        for i, r in enumerate(items, 1):
            hyp = f"(依赖假设: {r.hypothesis_label})" if r.hypothesis_label else ""
            lines.append(f"{i}. **{r.action}** {hyp}")
            lines.append(f"   - 依据: {', '.join(r.basis_claim_ids)};"
                         f"{r.rationale}")
        return "\n".join(lines)

    # ------------------------------------------------------------- 环④

    def _final_check(self, event_id: str, round_no: int) -> None:
        if self._config.deployment_mode == "action_facing":
            stale = self._ledger.stale_sections(event_id)
            if stale:
                raise RuntimeError(
                    f"action_facing 模式禁止交付 STALE 节: "
                    f"{[s['title'] for s in stale]}")
        # report_only:先修复残留 STALE 节(定向重写+快照刷新),再做断言
        for sec in self._ledger.stale_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            snapshot = json.loads(sec["snapshot_json"] or "{}")
            current = {c["draft_id"]: c["status"] for c in claims}
            drift = [
                {"claim_id": cid, "issue": f"快照 {old} != 台账 {current.get(cid)}"}
                for cid, old in snapshot.items() if current.get(cid) != old]
            text = self._writer.edit(
                sec["draft_text"] or "", drift,
                {"title": sec["title"], "kind": sec["kind"],
                 "claims": self._claim_payload(claims), "drift": drift})
            self._ledger.set_draft(
                sec["section_id"], text, current, round_no, redraft=True)
        for sec in self._ledger.drafted_sections(event_id):
            if sec["kind"] != "standard":
                continue  # OQ/建议节不做快照断言(自动聚合视图)
            claims = self._ledger.claims_for_section(sec["section_id"])
            snapshot = json.loads(sec["snapshot_json"] or "{}")
            current = {c["draft_id"]: c["status"] for c in claims}
            if snapshot != current:
                # 修复一次(edit 定向重写)再断言;再不一致即流程 bug
                drift = [
                    {"claim_id": cid, "issue": f"快照 {old} != 台账 {current.get(cid)}"}
                    for cid, old in snapshot.items() if current.get(cid) != old]
                text = self._writer.edit(
                    sec["draft_text"] or "", drift,
                    {"title": sec["title"], "kind": sec["kind"],
                     "claims": self._claim_payload(claims), "drift": drift})
                self._ledger.set_draft(
                    sec["section_id"], text, current, round_no, redraft=True)
                claims2 = self._ledger.claims_for_section(sec["section_id"])
                snapshot2 = {c["draft_id"]: c["status"] for c in claims2}
                sec2 = [s for s in self._ledger.drafted_sections(event_id)
                        if s["section_id"] == sec["section_id"]][0]
                if json.loads(sec2["snapshot_json"] or "{}") != snapshot2:
                    raise AssertionError(
                        f"终稿核对失败(快照!=台账): {sec['title']} —— 流程 bug")

    # ------------------------------------------------------------- 环⑤

    def _summary(self, event_id: str, stop_reason: str,
                 recommendations: list[Recommendation], costs: dict | None,
                 signal_type: str) -> dict:
        return {
            "event_id": event_id, "signal_type": signal_type,
            "stop_reason": stop_reason,
            "status_counts": self._ledger.status_counts(event_id),
            "standings": self._ledger.hypothesis_standings(event_id),
            "relations": [
                {k: r[k] for k in ("head_name", "relation_type", "tail_name")}
                for r in self._ledger.relations_view(event_id)],
            "transitions": [
                {"claim": t["claim_id"].rsplit(":", 1)[-1],
                 "old": t["old_status"], "new": t["new_status"],
                 "cause": t["cause"], "round": t["round"]}
                for t in self._ledger.transitions(event_id)],
            "recommendations": [r.model_dump(by_alias=True) for r in recommendations],
            "costs": costs or {},
        }

    def _deliver(self, out_dir: Path, event_id: str, summary: dict,
                 envelope_label: str, envelope=None, oq_rows=None,
                 rec_items=None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        sections = self._ledger.get_sections(event_id)
        evidence = self._ledger.conn.execute(
            "SELECT * FROM evidence WHERE event_id=? ORDER BY rowid",
            (event_id,)).fetchall()
        verdicts = self._verdict_notes(event_id)
        md = self._render_markdown(
            sections, evidence, verdicts, summary, envelope_label,
            rec_items=rec_items)
        (out_dir / "report.md").write_text(md, encoding="utf-8")
        (out_dir / "report.html").write_text(
            self._render_html(sections, evidence, verdicts, summary,
                              envelope_label),
            encoding="utf-8")
        # 企业简报(形态 A):同一台账的决策视图,模板确定性生成,零 LLM 零写库
        (out_dir / "brief.md").write_text(
            self._render_brief(summary, envelope, envelope_label,
                               oq_rows or [], rec_items or [], evidence),
            encoding="utf-8")
        (out_dir / "result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        # 研判审计包:全链路留痕导出(合规/复盘用)
        audit = {
            "event_id": event_id,
            "decision_log": [
                {"round": r["round"], "kind": r["kind"],
                 "payload": json.loads(r["payload_json"]),
                 "degraded": bool(r["degraded"])}
                for r in self._ledger.decision_log_rows(event_id)],
            "transitions": summary["transitions"],
            "sections": [
                {"title": s["title"], "state": s["state"],
                 "snapshot": json.loads(s["snapshot_json"] or "{}")}
                for s in sections],
        }
        (out_dir / "audit.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")

    # ------------------------------------------------------------- 渲染

    def _claim_payload(self, claims) -> list[dict]:
        out = []
        for c in claims:
            rows = self._ledger.evidence_for_claim(c["claim_id"])
            strong = [e for e in rows
                      if e["ruling"] in ("支持", "矛盾", "时效刷新")]
            weak = [e for e in rows
                    if e["ruling"] is None or e["ruling"] == "无关"]
            refs = [e["evidence_id"].rsplit(":", 1)[-1]
                    for e in (strong + weak)[: self._config.evidence_topk]]
            out.append({"id": c["draft_id"], "text": c["text"],
                        "status": c["status"], "category": c["category"],
                        "refs": refs})
        return out

    def _verdict_notes(self, event_id: str) -> dict[str, dict]:
        """从 decision_log 提取每条 claim 的最终判决(核对证书的规则命中栏)。"""
        notes: dict[str, dict] = {}
        for r in self._ledger.decision_log_rows(event_id):
            if r["kind"] != "判决":
                continue
            p = json.loads(r["payload_json"])
            notes[p["claim_id"].rsplit(":", 1)[-1]] = p
        return notes

    # ------------------------------------------------------- 企业简报(形态 A)
    # 全部模板确定性生成(零 LLM、零台账写入):卡片/红旗/研判倾向都是从台账
    # 与信封"读出来"的交付层视图;六节 canonical 叙事在 report.md 原样保留。

    def _dominant_line(self, standings: list[dict]) -> str:
        doms = [s for s in standings if s.get("dominant")]
        if not doms:
            return "(无主导假设)"
        labels = "、".join(s["label"] for s in doms)
        score = doms[0]["score"]
        return (f"{labels}(并列,得分 {score})" if len(doms) > 1
                else f"{labels}(得分 {score})")

    def _split_recommendations(self, rec_items):
        """确定性受众分流:文案命中内部标记词的归「内部待办」,不进企业简报。"""
        internal, external = [], []
        for r in rec_items or []:
            blob = f"{r.action} {r.rationale}"
            (internal if any(m in blob for m in _INTERNAL_MARKERS)
             else external).append(r)
        return external, internal

    def _refs_of(self, claim_id: str, k: int = 1) -> str:
        rows = self._ledger.evidence_for_claim(claim_id)
        return "、".join(
            f"[{r['evidence_id'].rsplit(':', 1)[-1]}]" for r in rows[:k])

    def _field_status(self, match: str, claims):
        """卡片字段状态(确定性):字段值出现在已证实 claim 文本 -> 已证实(带
        该 claim 首个引用);仅出现在待证实/矛盾 claim -> 同状态;完全未提及 ->
        信封载明(锚 [E1] 基础字段种子)。"""
        if not match:
            return "信封未提供", ""
        for want in ("已证实", "待证实", "矛盾"):
            for c in claims:
                if c["status"] == want and match in (c["text"] or ""):
                    refs = self._refs_of(c["claim_id"])
                    return (want if want != "矛盾" else "矛盾"), refs
        return "信封载明", "[E1]"

    @staticmethod
    def _clip(text: str, n: int = 50) -> str:
        text = text or ""
        return text if len(text) <= n else text[:n] + "…"

    def _tender_card_rows(self, t) -> list[tuple[str, str, str]]:
        """(字段, 显示值, 状态匹配值)三元组;匹配值=信封原值,供 _field_status
        在 claim 文本中定位。缺失字段(截止/获取文件/代理)如实标"信封未提供"。"""
        if t.amount_yuan is None:
            amount_disp, amount_match = "—", ""
        elif t.amount_yuan >= 10000:
            amount_disp, amount_match = f"{t.amount_yuan / 10000:g} 万元", str(t.amount_yuan)
        else:
            amount_disp, amount_match = f"{t.amount_yuan:g} 元", str(t.amount_yuan)
        return [
            ("采购人", t.purchaser or "—", t.purchaser),
            ("预算金额", amount_disp, amount_match),
            ("项目编号", t.project_id or "—", t.project_id),
            ("公告发布日", t.published_date or "—", t.published_date),
            ("采购内容", self._clip(t.project_summary) or "—", t.project_summary or ""),
            ("公告原文链接", t.source_url or "—", t.source_url or ""),
            ("投标截止时间", "—", ""),
            ("获取招标文件方式", "—", ""),
            ("代理机构 / 联系人", "—", ""),
        ]

    def _rumor_card_rows(self, r, evidence) -> list[tuple[str, str, str]]:
        """rumor 无商机卡片,给主体卡片;线索行附轮 0 种子的谱系根折算读数。"""
        seeds = [ev for ev in evidence if ev["round"] == 0 and ev["lineage_root"]]
        roots = sorted({ev["lineage_root"] for ev in seeds})
        clue = (f"{len(r.clues)} 条线索;谱系根 {len(roots)} 个"
                f"({'、'.join(roots)};同源折算后)" if roots else "无线索卡")
        return [
            ("监测主体", r.subject_name or "—", r.subject_name),
            ("统一信用代码", r.subject_credit_code or "—", r.subject_credit_code),
            ("事件摘要", self._clip(r.summary, 60) or "—", r.summary),
            ("信号识别时间", r.signal_time or "—", r.signal_time),
            ("监测重点", r.watch_focus or "—", r.watch_focus),
            ("线索", clue, ""),
        ]

    def _render_card(self, rows, claims) -> list[str]:
        lines = ["| 字段 | 值 | 状态 |", "|---|---|---|"]
        for label, disp, match in rows:
            status, refs = self._field_status(match, claims)
            cell = " ".join(x for x in (status, refs) if x)
            lines.append(f"| {label} | {disp or '—'} | {cell} |")
        return lines

    def _gist(self, summary: dict, envelope) -> str:
        """一句话研判(确定性规则,非 LLM):定性 + 画像相关分 + 研判倾向。
        倾向规则:有矛盾 -> 复核;多假设并列主导 -> 观察跟踪;单一主导 -> 可推进;
        无主导 -> 人工复核。待证实计数如实附注。"""
        counts = summary["status_counts"]
        doms = [s for s in summary["standings"] if s.get("dominant")]
        parts = [f"信号定性「{summary['signal_type'] or '未定性'}」"]
        rel = envelope.tender.relevance if (envelope and envelope.tender) else None
        if rel is not None and rel.score is not None:
            parts.append(f"与企业画像相关得分 {rel.score:g}")
        if counts.get("矛盾"):
            lean = "存在被推翻结论,建议复核后再采取行动"
        elif len(doms) > 1:
            lean = (f"{len(doms)} 个假设并列主导"
                    f"({'、'.join(s['label'] for s in doms)}),缺乏区分证据,"
                    "建议观察性跟踪,补齐区分证据后再实质投入")
        elif doms:
            lean = (f"证据结构支持「{doms[0]['label']}」(得分 {doms[0]['score']}),"
                    "可按建议推进")
        else:
            lean = "暂无主导假设,建议人工复核"
        if counts.get("待证实"):
            lean += f";{counts['待证实']} 项未决如实保留(见 Open Questions)"
        return "。".join(parts) + f"。**研判倾向:{lean}。**"

    def _red_flags(self, envelope, claims, oq_rows) -> list[str]:
        flags: list[str] = []
        for c in claims:                       # 被推翻的结论最优先
            if c["status"] == "矛盾":
                flags.append(f"**结论被推翻** {c['draft_id']}:{self._clip(c['text'], 40)}")
        if envelope and envelope.tender:
            t = envelope.tender
            for name, det in (("金额异常", t.amount_anomaly),
                              ("频率异常", t.frequency_anomaly),
                              ("新颖性", t.novelty_anomaly)):
                if det is None:
                    continue
                basis = ("历史依据充分" if det.basis_sufficient
                         else "历史依据不足,不强行评分")
                flags.append(f"**{name}信号**:得分 {det.score:g}({basis})"
                             "——是否成立以裁定为准,不预作风险结论")
            if t.content_source_note:
                flags.append(f"**正文质量**:上游说明「{t.content_source_note}」")
        for r in oq_rows or []:
            if r["priority"] >= 4:
                note = " [来源不可达]" if r["unreachable"] else ""
                flags.append(f"**未决高优断言** {r['draft_id']}"
                             f"(优先级 {r['priority']}){note}:"
                             f"{self._clip(r['text'], 44)}")
        return flags[:6]

    def _world_verified_lines(self, claims, verdicts) -> list[str]:
        """已核实事实(外部证据):已证实 ∧ 非复述 ∧ 存在非信封根的世界断言。
        纯 signal_layer 根的复述型已证实不进正文(已压缩进卡片)——篇幅重分配。"""
        lines = []
        for c in claims:
            if c["status"] != "已证实" or c["corpus_bound"]:
                continue
            roots = [r for r in verdicts.get(c["draft_id"], {}).get(
                "support_roots", []) if r != "signal_layer"]
            if not roots:
                continue
            lines.append(f"- **{c['draft_id']}** {self._clip(c['text'], 60)}"
                         f"(独立根:{'、'.join(roots)})")
        return lines

    def _brief_evidence(self, claims, rec_items, oq_rows, evidence, cap: int = 15):
        """简报引用证据:信封种子(卡片 [E1] 锚)+ 世界断言/OQ/建议依据链所引
        证据,去重保序,上限 cap。非种子证据只保留**实际进入裁定的**(裁定方向
        非 空/无关)——实体交集宽进挂账会带进未参与判决的噪声,不进企业简报。"""
        by_draft = {c["draft_id"]: c["claim_id"] for c in claims}
        want: set[str] = set()
        for c in claims:
            if c["status"] == "已证实" and not c["corpus_bound"]:
                want.add(c["claim_id"])
        for r in oq_rows or []:
            want.add(r["claim_id"])
        for r in rec_items or []:
            for did in r.basis_claim_ids:
                if did in by_draft:
                    want.add(by_draft[did])
        ids: list[str] = []
        for cid in want:
            for e in self._ledger.evidence_for_claim(cid):
                if e["ruling"] not in ("支持", "矛盾", "时效刷新"):
                    continue
                eid = e["evidence_id"].rsplit(":", 1)[-1]
                if eid not in ids:
                    ids.append(eid)
        seeds = [ev["evidence_id"].rsplit(":", 1)[-1] for ev in evidence
                 if ev["round"] == 0]
        ordered = seeds + [i for i in ids if i not in seeds]
        by_id = {ev["evidence_id"].rsplit(":", 1)[-1]: ev for ev in evidence}
        return [by_id[i] for i in ordered if i in by_id][:cap]

    def _render_brief(self, summary: dict, envelope, envelope_label: str,
                      oq_rows, rec_items, evidence) -> str:
        event_id = summary["event_id"]
        claims = self._ledger.all_claims(event_id)
        verdicts = self._verdict_notes(event_id)
        counts = summary["status_counts"]
        external, internal = self._split_recommendations(rec_items)

        lines = [f"# 企业情报简报:{envelope_label}", ""]
        lines.append(
            f"- 信号类型:{summary['signal_type'] or '未定性'}"
            f" | 已证实 {counts.get('已证实', 0)} / 矛盾 {counts.get('矛盾', 0)}"
            f" / 待证实 {counts.get('待证实', 0)}"
            f" | 主导假设:{self._dominant_line(summary['standings'])}")
        lines.append("")

        lines += ["## 一句话研判", "", self._gist(summary, envelope), ""]

        lines += ["## 商机卡片" if (envelope and envelope.kind == "tender")
                  else "## 主体卡片", ""]
        if envelope is None:
            lines.append("(信封上下文不可用,仅台账视图)")
        elif envelope.kind == "tender" and envelope.tender:
            lines += self._render_card(
                self._tender_card_rows(envelope.tender), claims)
        elif envelope.rumor:
            lines += self._render_card(
                self._rumor_card_rows(envelope.rumor, evidence), claims)
        lines.append("")

        flags = self._red_flags(envelope, claims, oq_rows)
        lines += ["## 红旗与信号", ""]
        lines += [f"{i}. {f}" for i, f in enumerate(flags, 1)] or \
                 ["- (无红旗信号)"]
        lines.append("")

        world = self._world_verified_lines(claims, verdicts)
        lines += ["## 已核实事实与研判状态", ""]
        lines += world or ["- (暂无外部证据核实的事实)"]
        for r in summary["relations"]:
            lines.append(f"- {r['head_name']} —{r['relation_type']}→ "
                         f"{r['tail_name']}")
        lines.append("- 竞争格局:需按采购人检索历史中标人"
                     "(定向证据源接入后本节自动填充)")
        lines.append("")

        lines += ["## 建议下一步", "",
                  "每条附依据链(claim 草案 id,核对证书可查其判决规则);"
                  "建议供决策参考,不替代企业决策。", ""]
        if external:
            for i, r in enumerate(external, 1):
                hyp = f"(依赖假设: {r.hypothesis_label})" if r.hypothesis_label else ""
                lines.append(f"{i}. **{r.action}** {hyp}")
                lines.append(f"   - 依据: {', '.join(r.basis_claim_ids)};"
                             f"{r.rationale}")
        else:
            lines.append("- (无足以支撑行动建议的已查实结论)")
        if internal:
            lines.append("")
            lines.append(f"(另有 {len(internal)} 条系统内部事项,"
                         "见运营者报告「内部待办」)")
        lines.append("")

        lines += ["## Open Questions", ""]
        if oq_rows:
            for r in oq_rows:
                note = " [来源不可达,已登记缺口]" if r["unreachable"] else ""
                lines.append(f"- **{r['draft_id']}**(优先级 {r['priority']})"
                             f"{note}:{r['text']}")
        else:
            lines.append("- (无未决问题)")
        lines.append("")

        lines += ["### 引用证据(精简)", ""]
        for ev in self._brief_evidence(claims, rec_items, oq_rows, evidence):
            tag = ev["evidence_id"].rsplit(":", 1)[-1]
            lines.append(f"- **[{tag}]** {ev['title'] or '(无标题)'} | "
                         f"来源:{ev['source'] or ev['platform']} | "
                         f"谱系根:{ev['lineage_root']} | {ev['url'] or '(无链接)'}")
        lines += ["", "---", "",
                  "*研判倾向、卡片状态与红旗清单均由台账确定性规则生成(零 LLM);"
                  "逐条判决规则命中与完整 55 项证据表见运营者报告(report.md)附录。"
                  "本简报不替代企业决策。*"]
        return "\n".join(lines)

    def _render_markdown(self, sections, evidence, verdicts, summary,
                         envelope_label, rec_items=None) -> str:
        lines = [
            f"# 企业情报调查报告:{envelope_label}",
            "",
            f"- 信号类型:{summary['signal_type']} | 停止原因:{summary['stop_reason']}",
            f"- 结论分布:{json.dumps(summary['status_counts'], ensure_ascii=False)}",
            # 主导假设交付时快照(模板生成,canonical 统计读数;分轮起草的叙事
            # 文本与此若有出入,以本行与附录 D 为准——修统计滞后 artifact)
            f"- 主导假设(交付时快照):{self._dominant_line(summary['standings'])}",
            f"- 成本:{json.dumps(summary['costs'], ensure_ascii=False)}"
            " (分级调度:代码把关+LLM 意见)",
            "",
        ]
        for sec in sections:
            lines.append(sec["draft_text"] or f"### {sec['title']}")
            lines.append("")
        _, internal = self._split_recommendations(rec_items)
        if internal:
            lines.append("## 内部待办(运营者事项,企业简报不含)")
            lines.append("")
            for i, r in enumerate(internal, 1):
                lines.append(f"{i}. **{r.action}**")
                lines.append(f"   - 依据: {', '.join(r.basis_claim_ids)};"
                             f"{r.rationale}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 附录 A:证据表(点回原文)")
        lines.append("")
        # 同题聚类标注:同题多稿不合并(可能为独立稿件),但显式标注防误读
        title_counts: dict[str, int] = {}
        for ev in evidence:
            title_counts[ev["title"] or ""] = title_counts.get(ev["title"] or "", 0) + 1
        for ev in evidence:
            tag = ev["evidence_id"].rsplit(":", 1)[-1]
            same = title_counts.get(ev["title"] or "", 0)
            mark = f"〔同题 {same} 稿〕" if same > 1 else ""
            lines.append(
                f"- **[{tag}]**{mark} {ev['title'] or '(无标题)'} | 来源:"
                f"{ev['source'] or ev['platform']} | 谱系根:{ev['lineage_root']}"
                f" | 可信度:{ev['credibility']} | {ev['url'] or '(无链接)'}")
        lines.append("")
        lines.append("## 附录 B:判决核对证书(每条结论可机器复核)")
        lines.append("")
        lines.append("| claim | 终态 | 独立根(支持) | 独立根(反方) | 判决规则 |")
        lines.append("|---|---|---|---|---|")
        for c in self._ledger.all_claims(summary["event_id"]):
            v = verdicts.get(c["draft_id"], {})
            lines.append(
                f"| {c['draft_id']} {c['text'][:30]} | {c['status']} "
                f"| {', '.join(v.get('support_roots', [])) or '-'} "
                f"| {', '.join(v.get('oppose_roots', [])) or '-'} "
                f"| {v.get('note', '-')} |")
        lines.append("")
        lines.append("## 附录 C:结论变更日志(时间机器:翻转全程留痕)")
        lines.append("")
        if summary["transitions"]:
            for t in summary["transitions"]:
                lines.append(
                    f"- R{t['round']} {t['claim']}: {t['old']} -> {t['new']}"
                    f" ({t['cause'] or '澄清'})")
        else:
            lines.append("- (无状态变更)")
        lines.append("")
        lines.append("## 附录 D:假设记分板")
        lines.append("")
        for s in summary["standings"]:
            star = " **[主导]**" if s.get("dominant") else ""
            lines.append(
                f"- {s['label']}{star}: 得分 {s['score']}"
                f"(已证实 {s['verified']} / 矛盾 {s['contradicted']}"
                f" / 待证实 {s['pending']})")

        # 附录 E:证据图谱(台账确定性投影,零 LLM;边带三态+谱系折算+人答标记)
        lines.append(_graph_section(
            build_graph_payload(self._ledger, summary["event_id"])))
        return "\n".join(lines)

    def _render_html(self, sections, evidence, verdicts, summary,
                     envelope_label) -> str:
        """演示包:红绿灯徽章/点击查账/双口径/决策卡/记分板/证书/成本行。"""
        esc = html_mod.escape
        counts = summary["status_counts"]
        top = next((s for s in summary["standings"] if s.get("dominant")), None)

        def badge(status: str) -> str:
            label, cls = _STATUS_BADGE.get(status, (status, "warn"))
            return f'<span class="badge {cls}">{esc(label)}</span>'

        cards = []
        for sec in sections:
            body = "<br>".join(esc(line) for line in
                               (sec["draft_text"] or "").splitlines() if line.strip())
            cards.append(
                f'<section><h2>{esc(sec["title"])}</h2>'
                f'<div class="body">{body}</div></section>')
        ev_rows = "".join(
            f'<tr id="ev-{esc(ev["evidence_id"].rsplit(":", 1)[-1])}">'
            f'<td>[{esc(ev["evidence_id"].rsplit(":", 1)[-1])}]</td>'
            f'<td>{esc(ev["title"] or "(无标题)")}</td>'
            f'<td>{esc(ev["source"] or ev["platform"])}</td>'
            f'<td>{esc(ev["lineage_root"])}</td>'
            f'<td>{esc(ev["credibility"])}</td>'
            f'<td>{esc(ev["url"] or "-")}</td></tr>'
            for ev in evidence)
        cert_rows = "".join(
            f'<tr><td>{esc(c["draft_id"])}</td><td>{badge(c["status"])}</td>'
            f'<td>{esc(", ".join(verdicts.get(c["draft_id"], {}).get("support_roots", [])) or "-")}</td>'
            f'<td>{esc(", ".join(verdicts.get(c["draft_id"], {}).get("oppose_roots", [])) or "-")}</td>'
            f'<td>{esc(verdicts.get(c["draft_id"], {}).get("note", "-"))}</td></tr>'
            for c in self._ledger.all_claims(summary["event_id"]))
        change_rows = "".join(
            f'<li>R{t["round"]} {esc(t["claim"])}: {esc(t["old"])} &rarr; '
            f'{esc(t["new"])} ({esc(t["cause"] or "澄清")})</li>'
            for t in summary["transitions"]) or "<li>(无变更)</li>"
        score_rows = "".join(
            f'<li>{esc(s["label"])}{" ★主导" if s.get("dominant") else ""}'
            f' —— 得分 {s["score"]}(已证实 {s["verified"]} / 矛盾 {s["contradicted"]}'
            f' / 待证实 {s["pending"]})</li>'
            for s in summary["standings"]) or "<li>(无假设)</li>"

        # 证据图谱(三图;esc 后浏览器 textContent 解码,mermaid 收到原文;
        # 占位说明不是 flowchart,渲染为小字而非 mermaid 块)
        graph = build_graph_payload(self._ledger, summary["event_id"])

        def _graph_pre(text: str) -> str:
            if text.lstrip().startswith("flowchart"):
                return '<pre class="mermaid">\n' + esc(text) + "\n</pre>"
            return '<p class="small">' + esc(text) + "</p>"

        graph_blocks = "".join(
            f"<h3>{esc(title)}</h3>" + _graph_pre(text)
            for title, text in _graph_diagrams(graph))

        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
<title>Sense2act 调查报告 · {esc(envelope_label)}</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:980px;margin:24px auto;
 padding:0 16px;color:#1a1a1a;background:#fafafa}}
.card{{background:#fff;border:1px solid #e0e0e0;border-radius:10px;padding:16px 20px;
 margin:14px 0}}
.badge{{display:inline-block;padding:1px 10px;border-radius:10px;font-size:13px}}
.badge.ok{{background:#e6f6e6;color:#1c6b1c}} .badge.bad{{background:#fde8e8;color:#a11}}
.badge.warn{{background:#fff4dd;color:#8a6100}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
td,th{{border:1px solid #ddd;padding:6px 8px;text-align:left}}
h2{{border-bottom:2px solid #2b6cb0;padding-bottom:4px;font-size:18px}}
.kpi{{display:inline-block;margin:0 18px 0 0;font-size:15px}}
.small{{color:#666;font-size:13px}}
pre.mermaid{{background:#fff;border:1px solid #eee;padding:8px;overflow:auto;
text-align:center}}
pre.mermaid svg{{max-width:none !important}}
pre.mermaid foreignObject{{overflow:visible}}
pre.mermaid foreignObject div{{white-space:nowrap}}
</style></head><body>
<div class="card">
 <h2 style="border:none;margin:0">一页决策卡 · {esc(envelope_label)}</h2>
 <p>
  <span class="kpi">{badge("已证实")} × {counts.get("已证实", 0)}</span>
  <span class="kpi">{badge("矛盾")} × {counts.get("矛盾", 0)}</span>
  <span class="kpi">{badge("待证实")} × {counts.get("待证实", 0)}</span>
 </p>
 <p><b>主导假设:</b>{esc(top["label"]) if top else "(无)"} ——
 {esc(json.dumps(summary["relations"], ensure_ascii=False)) if summary["relations"] else "无已证实关系边"}</p>
 <p class="small">停止原因:{esc(summary["stop_reason"])} |
 成本:{esc(json.dumps(summary["costs"], ensure_ascii=False))} |
 每条结论可在附录核对证书中机器复核</p>
</div>
{''.join(cards)}
<div class="card"><h2>附录:判决核对证书</h2>
<table><tr><th>claim</th><th>终态</th><th>独立根(支持)</th><th>独立根(反方)</th>
<th>判决规则</th></tr>{cert_rows}</table></div>
<div class="card"><h2>附录:证据表(点击查账)</h2>
<table><tr><th>编号</th><th>标题</th><th>来源</th><th>谱系根</th><th>可信度</th>
<th>URL</th></tr>{ev_rows}</table></div>
<div class="card"><h2>结论变更日志(时间机器)</h2><ul>{change_rows}</ul></div>
<div class="card"><h2>假设记分板</h2><ul>{score_rows}</ul></div>
<div class="card"><h2>证据图谱(边带三态 · 谱系折算 · 判别语义)</h2>
{graph_blocks}
<p class="small">在线环境自动渲染(图按自然尺寸显示,超出可横向滚动,
Ctrl+滚轮缩放);离线请查看 report.md 附录 E 的 mermaid 源码
(VSCode/Typora 原生渲染)。图由台账确定性投影生成,零 LLM:
实线=已证实,虚线=待证实,红=矛盾;证据节点〔〕内=谱系根(同根=非独立,
独立根≥2 才已证实);实线箭头=复合事实支撑结论(唯一最高分),
虚线箭头=支撑候选假设(含并列领先——共享事实同升不判别),
🏆=主导假设,👤=委托方裁决。</p></div>
<script>
// 修复 mermaid 对中文宽度的低估(根因:canvas 测 CJK 偏窄 → 节点框偏小 → 裁字)
(function() {{
  var orig = CanvasRenderingContext2D.prototype.measureText;
  CanvasRenderingContext2D.prototype.measureText = function(text) {{
    var m = orig.call(this, text);
    var s = String(text);
    var cjk = (s.match(/[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]/g) || []).length;
    if (cjk > 0) {{
      var fontSize = 14;
      var mm = String(this.font || '').match(/(\\d+(?:\\.\\d+)?)px/);
      if (mm) fontSize = parseFloat(mm[1]);
      var asciiW = orig.call(this, s.replace(/[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]/g, '')).width;
      m.width = asciiW + cjk * fontSize;
    }}
    return m;
  }};
}})();
mermaid.initialize({{startOnLoad: false,
  fontFamily: '"Microsoft YaHei", "PingFang SC", "SimSun", "Noto Sans CJK SC", sans-serif',
  fontSize: '12px',
  themeVariables: {{ fontFamily: '"Microsoft YaHei", "PingFang SC", "SimSun", "Noto Sans CJK SC", sans-serif', fontSize: '12px' }},
  flowchart: {{useMaxWidth: false, padding: 60}}}});
if (document.fonts && document.fonts.ready) {{
  document.fonts.ready.then(function() {{ mermaid.run(); }});
}} else {{
  mermaid.run();
}}</script>
</body></html>"""
