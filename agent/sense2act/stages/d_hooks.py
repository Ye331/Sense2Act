"""D 节起草钩子(v3 交错起草):判定=纯代码,执行=辅模型。

钩子1 稳定即起草:某节全部 claim 终态(矛盾也是终态)且未起草 -> 立即起草;
钩子2 翻转即重写:已起草节快照漂移(字典比较,不靠模型记忆) -> 定向重写;
轮末统一处理(防一轮内多次重写);Open Questions / 行动建议节排除在外(永不稳定)。
状态机: PENDING -> STABLE_DRAFTED -> STALE -> REDRAFTED。
"""
from __future__ import annotations

import json
from typing import Protocol

from pydantic import Field

from ..llm import StructuredLLM, call_structured
from ..chain_graph import claim_tier, load_present_rules
from ..models.base import SchemaModel
from ..prompts import S7_CHECK_PROMPT, S7_DRAFT_PROMPT, S7_EDIT_PROMPT
from ..stages.s7_report import claim_input_row, verdict_brief
from ..store.ledger import LedgerRepository


class DraftOutput(SchemaModel):
    markdown: str


class CheckIssue(SchemaModel):
    claim_id: str
    issue: str


class CheckOutput(SchemaModel):
    issues: list[CheckIssue] = []


class EditOutput(SchemaModel):
    markdown: str


class SectionWriter(Protocol):
    def draft(self, context: dict) -> str: ...

    def check(self, section_text: str, context: dict) -> list[dict]: ...

    def edit(self, section_text: str, issues: list[dict], context: dict) -> str: ...


class TemplateSectionWriter:
    """确定性模板起草(回放模式默认;live 模式的降级兜底)。含双口径渲染。

    引用格式 [E3]:E 编号是证据入账序号,一旦分配永不重排(报告附录按此锚定)。
    """

    def draft(self, context: dict) -> str:
        title = context["title"]
        lines = [f"### {title}", ""]
        if context.get("kind") == "conclusion":
            return self._conclusion(context)
        for c in context["claims"]:
            badge = {"已证实": "[已证实]", "矛盾": "[矛盾]", "待证实": "[待证实]"}[
                c["status"]]
            refs = ", ".join(f"[{t}]" for t in c["refs"]) or "无引用"
            if c["status"] == "矛盾":
                lines.append(
                    f"- {badge} {c['text']} —— 口径冲突(双口径呈现,见证据:"
                    f" {refs})")
            else:
                lines.append(f"- {badge} {c['text']} —— 证据: {refs}")
        if not context["claims"]:
            lines.append(
                f"(本节暂无已登记 claim,依据信封信息: "
                f"{context.get('envelope_label', '')})")
        return "\n".join(lines)

    def _conclusion(self, context: dict) -> str:
        standings = context.get("standings") or []
        lines = ["### 结论", ""]
        if standings:
            # 主导 = 得分最高(与记分板/建议节同一口径)
            top = max(standings, key=lambda s: s["score"])
            others = [s for s in standings if s is not top]
            second = others[0]["label"] if others else "原假设"
            lines.append(
                f"**主导假设:{top['label']}**(得分 {top['score']};"
                f"已证实 {top['verified']} 条 / 矛盾 {top['contradicted']} 条 / "
                f"待证实 {top['pending']} 条)。")
            lines.append("")
            lines.append(
                f"研判(竞争假设式):本事件证据结构当前更支持「{top['label']}」;"
                f"翻转条件:其组成 claim 被权威登记源推翻,或待证实项证实后指向"
                f"「{second}」,届时本节将定向重写。")
            lines.append("")
            for s in others:
                lines.append(
                    f"- 备择:{s['label']}(得分 {s['score']};已证实 {s['verified']}"
                    f" / 矛盾 {s['contradicted']} / 待证实 {s['pending']})")
        else:
            lines.append("(无假设登记)")
        return "\n".join(lines)

    def check(self, section_text: str, context: dict) -> list[dict]:
        """模板检查器=确定性漂移清单(文本由模板生成,漂移即全部问题来源)。"""
        return context.get("drift", [])

    def edit(self, section_text: str, issues: list[dict], context: dict) -> str:
        """模板重写=以当前台账重新投影(定向重写的确定性等价)。"""
        if context.get("kind") == "conclusion":
            return self._conclusion(context)
        return self.draft(context)


class LLMSectionWriter:
    """辅模型三工具(live 模式):draft/check/edit;失败降级到模板(增强不阻塞)。"""

    def __init__(self, llm: StructuredLLM, fallback: TemplateSectionWriter,
                 retry: int = 2):
        self._llm = llm
        self._fallback = fallback
        self._retry = retry

    def draft(self, context: dict) -> str:
        try:
            out = call_structured(
                self._llm, stage="s7_draft", system=S7_DRAFT_PROMPT,
                user=json.dumps(context, ensure_ascii=False),
                model_cls=DraftOutput, retry=self._retry)
            return out.markdown
        except Exception:
            return self._fallback.draft(context)

    def check(self, section_text: str, context: dict) -> list[dict]:
        try:
            out = call_structured(
                self._llm, stage="s7_check", system=S7_CHECK_PROMPT,
                user="节文本:\n" + section_text + "\n\n台账上下文:\n"
                     + json.dumps(context, ensure_ascii=False),
                model_cls=CheckOutput, retry=self._retry)
            return [i.model_dump() for i in out.issues]
        except Exception:
            return self._fallback.check(section_text, context)

    def edit(self, section_text: str, issues: list[dict], context: dict) -> str:
        try:
            out = call_structured(
                self._llm, stage="s7_edit", system=S7_EDIT_PROMPT,
                user="节文本:\n" + section_text + "\n\n问题清单:\n"
                     + json.dumps(issues, ensure_ascii=False)
                     + "\n\n台账上下文:\n" + json.dumps(context, ensure_ascii=False),
                model_cls=EditOutput, retry=self._retry)
            return out.markdown
        except Exception:
            return self._fallback.edit(section_text, issues, context)


class DraftHooks:
    def __init__(self, ledger: LedgerRepository, writer: SectionWriter,
                 evidence_topk: int = 5):
        self._ledger = ledger
        self._writer = writer
        self._topk = evidence_topk
        # 卷面呈现模式表(P 波:起草输入侧 M1 滤/剥壳同表同源;缺文件=空表
        # =分流全关,确定性零行为变化,照 load_present_rules 纪律)
        self._rules = load_present_rules(ledger.config_dir)

    def run_round_end(self, event_id: str, round_no: int) -> dict:
        """轮末钩子(不消耗查证轮数,成本走 token 账)。"""
        drafted = self._hook1(event_id, round_no)
        redrafted = self._hook2(event_id, round_no)
        report = {"drafted": drafted, "redrafted": redrafted}
        if drafted or redrafted:
            self._ledger.log(event_id, round_no, "钩子", report)
        return report

    # ------------------------------------------------------------------

    def _hook1(self, event_id: str, round_no: int) -> list[str]:
        """稳定即起草:全部终态(矛盾也是终态)且未起草且非 OQ/建议节。"""
        out: list[str] = []
        for sec in self._ledger.stable_undrafted_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            context = self._context(event_id, sec, claims)
            text = self._writer.draft(context)
            snapshot = {c["draft_id"]: c["status"] for c in claims}
            self._ledger.set_draft(sec["section_id"], text, snapshot, round_no)
            out.append(sec["title"])
        return out

    def _hook2(self, event_id: str, round_no: int) -> list[str]:
        """翻转即重写:快照漂移 -> check 定位 -> edit 只改该段 -> 刷新快照。"""
        out: list[str] = []
        for sec in self._ledger.stale_sections(event_id):
            claims = self._ledger.claims_for_section(sec["section_id"])
            context = self._context(event_id, sec, claims)
            snapshot = json.loads(sec["snapshot_json"] or "{}")
            drift = [
                {"claim_id": cid, "issue": f"claim {cid} 状态由 {old} 变为 "
                 f"{self._status_of(claims, cid)},对应表述需更新"}
                for cid, old in snapshot.items()
                if self._status_of(claims, cid) != old
            ]
            if not drift:
                continue
            issues = self._writer.check(sec["draft_text"] or "", context)
            issues = issues or drift  # check 只读;以漂移清单兜底驱动 edit
            text = self._writer.edit(sec["draft_text"] or "", issues, context)
            new_snapshot = {c["draft_id"]: c["status"] for c in claims}
            self._ledger.set_draft(
                sec["section_id"], text, new_snapshot, round_no, redraft=True)
            out.append(sec["title"])
        return out

    def _refs(self, claim_id: str) -> list[str]:
        """引用优先级:已被裁定 支持/矛盾/时效刷新 的证据优先(它们才是判决
        实际使用的),未被裁定或无关的仅在有余量时补位。"""
        rows = self._ledger.evidence_for_claim(claim_id)
        strong = [e for e in rows
                  if e["ruling"] in ("支持", "矛盾", "时效刷新")]
        weak = [e for e in rows
                if e["ruling"] is None or e["ruling"] == "无关"]
        ordered = strong + weak
        return [e["evidence_id"].rsplit(":", 1)[-1] for e in ordered[: self._topk]]

    def _status_of(self, claims, draft_id: str) -> str:
        for c in claims:
            if c["draft_id"] == draft_id:
                return c["status"]
        return "(已移除)"

    def _context(self, event_id: str, sec, claims) -> dict:
        """起草输入(P 波改造,2026-11-08):M1 三档滤+P2 附 gist/plain,
        与 finalize 补节/终稿核对/建议同源(claim_input_row 公共函数)——
        挡除/转写类不进 LLM 输入(快照与节归属语义不变,滤只动输入)。
        模式表经 ledger.config_dir 一次装载(构造期)。"""
        rules = self._rules
        gists = self._ledger.claim_gists(event_id)
        claim_payload = []
        for c in claims:
            if claim_tier(c["text"] or "", rules):
                continue
            claim_payload.append(
                claim_input_row(c, self._refs(c["claim_id"]), rules, gists))
        sec = dict(sec)   # sqlite3.Row/测试合成 dict 双形态兼容(ord 缺省防御)
        ctx = {
            "event_id": event_id, "title": sec.get("title"),
            "kind": sec.get("kind"),
            "claims": claim_payload,
            "standings": self._ledger.hypothesis_standings(event_id),
        }
        if sec.get("kind") == "conclusion" or sec.get("ord") == 3:
            # I2 研判说明素材包(判据×本案;与 finalize 补节同源=输入侧口径
            # 不分叉;空表=无主导=键缺席零痕迹。结论节判据=finalize 同款
            # [kind 存 standard,ord 3=结论])
            brief = verdict_brief(self._ledger, event_id, rules, gists)
            if brief:
                ctx["verdict_brief"] = brief
        return ctx
