"""台账留痕 → 后端调查时间线(契约 §7 / §9.3)。

台账的 decision_log 是 Agent 全链路的唯一写入口;这里挂在 `ledger.log` 之后,把每条决策
实时翻译成后端的 steps / questions / evidences,前端经 SSE 看到逐轮过程:

    新 claim          → POST question + questions_generated
    决策单(S2)        → round_started + tool_selected
    入账(S4/S5)       → 登记证据 + tool_completed
    判决(S6)终态      → PATCH question(clarified) + reflection_updated

每个 step 带稳定 request_id(决策行号),重试不重复记账。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from ..llm import ModelBudgetExceeded
from .client import CONFLICT, UNPROCESSABLE, BackendClient, BackendError

BUSINESS_TZ = timezone(timedelta(hours=8))

# Agent 采集平台 → 后端证据 source_type
SOURCE_TYPES = {
    "ccgp": "announcement", "tianjin_zfcg": "announcement", "yifangbao": "announcement", "ggzy": "announcement",
    "toutiao": "news", "cctv_news": "news", "south_weekend": "news", "sogou_wechat": "news",
    "official_website": "web", "internal_history": "internal_stat", "signal_layer": "internal_stat",
}
ACTION_TOOLS = {"查工具": "evidence_search", "explore": "query_explorer", "问人": "ask_user"}


class InvestigationAborted(RuntimeError):
    """后端拒绝写入(40901):调查已被用户停止或进入终态,Agent 必须立即收手。"""


def normalize_published(value: str) -> str | None:
    """后端接受 yyyy-MM-dd 或带时区 ISO;无时区的时刻按业务时区(+08:00)补齐,解析不了返回 None。"""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) == 10:
        return value
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value[:10] if len(value) >= 10 and value[4] == "-" else None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=BUSINESS_TZ)).isoformat()


class TokenMeter:
    """包住 StructuredLLM,累计 token,随下一条 step 上报给后端记预算。"""

    def __init__(self, inner):
        self._inner = inner
        self.model_name = getattr(inner, "model_name", "llm")
        self._pending = 0

    def generate_json(self, system_prompt: str, user_prompt: str, *, stage: str = "") -> dict:
        result = self._inner.generate_json(system_prompt, user_prompt, stage=stage)
        self._pending += int(getattr(self._inner, "last_total_tokens", 0) or 0)
        return result

    def take(self) -> int:
        used, self._pending = self._pending, 0
        return used

    def __getattr__(self, name):   # remaining / records 等实现细节透传
        return getattr(self._inner, name)


class StepReporter:
    def __init__(self, client: BackendClient, investigation_id: str, ledger, event_id: str,
                 document_id: str, meter: TokenMeter | None = None):
        self.client, self.inv, self.ledger, self.event_id = client, investigation_id, ledger, event_id
        self.document_id = document_id
        self.meter = meter
        self.evidence: dict[str, str] = {}        # 台账证据 id → 后端证据 id
        self.skipped: dict[str, str] = {}         # 台账证据 id → 不可登记原因
        self.questions: dict[str, str] = {}       # 台账 claim id → 后端问题 id
        self.reported: dict[str, str] = {}        # claim id → 已上报终态
        self.rounds: set[int] = set()
        self.exhausted = False
        self._selected_at = time.monotonic()
        self._tool = "evidence_search"

    # ------------------------------------------------------------ 挂载

    def attach(self) -> None:
        original = self.ledger.log

        def log(event_id, round_no, kind, payload, degraded=False):
            original(event_id, round_no, kind, payload, degraded)
            if event_id == self.event_id:
                row = self.ledger.conn.execute("SELECT max(id) FROM decision_log").fetchone()[0]
                self.on_log(int(round_no), kind, payload, f"dl{row}")

        self.ledger.log = log

    # ------------------------------------------------------------ 翻译

    def on_log(self, agent_round: int, kind: str, payload: dict, key: str) -> None:
        # 台账第 0 轮是 S1 定性/拆解,后端轮次从 1 起:并入第 1 轮
        round_no = max(agent_round, 1)
        if round_no not in self.rounds:
            self.rounds.add(round_no)
            focus = "信号定性与问题拆解" if agent_round == 0 else ""
            self._step("round_started", round_no, {"round": round_no, **({"focus": focus} if focus else {})}, f"{key}:round")
        self._sync_claims(round_no, key)
        if kind == "决策单":
            self._decision(round_no, payload, key)
        elif kind == "入账" and payload.get("new_evidence"):
            self._ingest(round_no, payload["new_evidence"], key, signal_layer=agent_round == 0)
        elif kind == "判决":
            self._verdict(round_no, payload, key)

    def _sync_claims(self, round_no: int, key: str) -> None:
        created = []
        for claim in self.ledger.all_claims(self.event_id):
            if claim["claim_id"] in self.questions:
                continue
            question = self._call(self.client.create_question, self.inv, claim["text"][:1000], round_no)
            self.questions[claim["claim_id"]] = question["id"]
            created.append({"id": question["id"], "text": claim["text"], "category": claim["category"]})
        if created:
            self._step("questions_generated", round_no, {"questions": created}, f"{key}:questions")

    def _decision(self, round_no: int, sheet: dict, key: str) -> None:
        action = sheet.get("action", "")
        if action == "停":
            self._step("reflection_updated", round_no, {
                "clarified": [], "new_questions": [],
                "judgment": f"规划器决定停止:{sheet.get('stopReason') or '无新的查证方向'}"}, f"{key}:stop")
            return
        hints = sheet.get("searchHints") or []
        gaps = [g.get("gap") for g in sheet.get("gapChain") or [] if g.get("gap")]
        target = f"{self.event_id}:{sheet.get('mainTarget')}" if sheet.get("mainTarget") else None
        self._tool = ACTION_TOOLS.get(action, action or "evidence_search")
        self._selected_at = time.monotonic()
        self._step("tool_selected", round_no, {
            "round": round_no, "tool": self._tool,
            "args": {"query": ";".join(hints), "gaps": gaps},
            "target_question_id": self.questions.get(target)}, f"{key}:tool")

    def _ingest(self, round_no: int, local_ids: list[str], key: str, signal_layer: bool) -> None:
        registered = [backend for local in local_ids if (backend := self.register(local))]
        titles = [self.ledger.evidence_row(local)["title"] for local in local_ids if local in self.evidence]
        summary = f"新增证据 {len(local_ids)} 条,登记 {len(registered)} 条"
        if titles:
            summary += ":" + ";".join(t for t in titles[:3] if t)
        missing = len(local_ids) - len(registered)
        if missing:
            summary += f"(另 {missing} 条缺来源链接或发布时间,未入证据库)"
        self._step("tool_completed", round_no, {
            "round": round_no, "tool": "signal_layer" if signal_layer else self._tool, "ok": True,
            "latency_ms": 0 if signal_layer else int((time.monotonic() - self._selected_at) * 1000),
            "result_summary": summary, "evidence_ids": registered}, f"{key}:done")

    def _verdict(self, round_no: int, verdict: dict, key: str) -> None:
        claim_id, final = verdict.get("claim_id"), verdict.get("final")
        if final not in ("已证实", "矛盾") or self.reported.get(claim_id) == final or claim_id not in self.questions:
            return
        self.reported[claim_id] = final
        evidence_ids = self.claim_evidence(claim_id, "矛盾" if final == "矛盾" else "支持")
        answer = (f"经查证不成立:{verdict.get('note', '')}" if final == "矛盾" else verdict.get("note", ""))[:1000]
        self._call(self.client.update_question, self.questions[claim_id], "clarified", answer, evidence_ids)
        self._step("reflection_updated", round_no, {
            "clarified": [{"question_id": self.questions[claim_id], "answer_summary": answer, "evidence_ids": evidence_ids}],
            "new_questions": [], "judgment": f"{final}:{verdict.get('note', '')}"}, f"{key}:verdict")

    # ------------------------------------------------------------ 证据

    def register(self, local_id: str) -> str | None:
        """登记一条台账证据;本库信号证据走 doc_id,外部证据带 Agent 读到的原文作快照。"""
        if local_id in self.evidence:
            return self.evidence[local_id]
        row = self.ledger.evidence_row(local_id)
        if row is None:
            return None
        content = (row["content"] or "").strip()
        if row["platform"] == "signal_layer":
            fields = {"source_type": "internal_stat", "doc_id": self.document_id,
                      "title": row["title"], "excerpt": content[:2000]}
        else:
            published = normalize_published(row["published_at"])
            if not row["url"] or not published:
                self.skipped[local_id] = "缺来源链接" if not row["url"] else "缺发布时间"
                return None
            fields = {"source_type": SOURCE_TYPES.get(row["platform"], "web"), "url": row["url"],
                      "title": (row["title"] or row["url"])[:500], "excerpt": content[:2000],
                      "published_at": published, "snapshot": content}
        try:
            view = self.client.register_evidence(self.inv, **fields)
        except BackendError as exc:
            if exc.code == CONFLICT:
                raise InvestigationAborted(exc.message) from exc
            self.skipped[local_id] = exc.message      # 42201/40001:这条证据不合规,不影响其余
            return None
        self.evidence[local_id] = view["id"]
        return view["id"]

    def claim_evidence(self, claim_id: str, ruling: str | None = None) -> list[str]:
        """claim 的后端证据 id:优先按裁定方向,退而取全部非“无关”挂账。"""
        rows = self.ledger.conn.execute(
            "SELECT evidence_id, ruling FROM claim_evidence WHERE claim_id=? ORDER BY round", (claim_id,)).fetchall()
        picked = [r["evidence_id"] for r in rows if ruling and r["ruling"] == ruling]
        if not picked:
            picked = [r["evidence_id"] for r in rows if r["ruling"] != "无关"]
        return list(dict.fromkeys(self.evidence[e] for e in picked if e in self.evidence))

    def close_open_questions(self) -> None:
        """交付前把仍待证实的问题标 unresolved(Open Questions),问题只在 investigating 可改。"""
        for claim in self.ledger.all_claims(self.event_id):
            if claim["status"] == "待证实" and claim["claim_id"] in self.questions:
                self._call(self.client.update_question, self.questions[claim["claim_id"]], "unresolved",
                           "截至交付仍未获足够独立证据,如实保留为待证实", self.claim_evidence(claim["claim_id"]))

    # ------------------------------------------------------------ 传输

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except BackendError as exc:
            if exc.code == CONFLICT:
                raise InvestigationAborted(exc.message) from exc
            raise

    def _step(self, event: str, round_no: int, payload: dict, key: str) -> None:
        if self.exhausted:
            return
        tokens = self.meter.take() if self.meter else 0
        try:
            self.client.step(self.inv, event, round_no, json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
                             tokens, request_id=f"{self.event_id}:{key}")
        except BackendError as exc:
            if exc.code == CONFLICT:
                raise InvestigationAborted(exc.message) from exc
            if exc.code == UNPROCESSABLE:
                # 超轮次/超预算:后端是预算权威。转成 Agent 自己的预算护栏 → 优雅停止,按现有台账出报告
                self.exhausted = True
                raise ModelBudgetExceeded(f"后端预算/轮次上限: {exc.message}") from exc
            raise
