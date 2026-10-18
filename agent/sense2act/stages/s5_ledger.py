"""S5 知识更新与判断修正(七环记账)——全系统唯一写入管线。

①接收 ②查重(dedupKey 双键=文本哈希+实体id) ③消歧锚定(S4 已做,此处入账)
④证据入账(本体入 evidence,claim 挂指针) ⑤矛盾检测(确定性翻转:实体分叉标记;
  语义矛盾经 S6 判决,成因分类落 transitions) ⑥派生(唯一 LLM 核心使用点;
  claim/调查圈) ⑦状态刷新(异步流的同步接口,watchlist 轮询触发)。

三环节一句话:查重问"记过没有",消歧问"说的是谁",矛盾检测问"跟旧账顶不顶"。
"""
from __future__ import annotations

import json
from typing import Protocol

from pydantic import Field

from ..config import DomainConfig
from ..llm import StructuredLLM, call_structured
from ..models.base import SchemaModel
from ..prompts import S5_DERIVE_PROMPT
from ..store.ledger import LedgerRepository, normalize_text


class DerivedClaimDraft(SchemaModel):
    id: str
    text: str
    category: str                     # 五类目之一(字符串,代码再校验词表)
    section_index: int = Field(default=2, ge=0, le=3)
    priority: int = Field(default=3, ge=1, le=5)
    relation_type: str | None = None
    parent: str | None = None
    entity_names: list[str] = []
    hypothesis_links: list[str] = []  # 派生 claim 挂回假设(如 H1)


class CohortDraft(SchemaModel):
    """调查圈派生:单事件 -> 群体调查(如同市同类医院)。"""

    name: str
    criteria: dict = {}
    members: list[str] = []


class DeriveResult(SchemaModel):
    derived_claims: list[DerivedClaimDraft] = []
    cohorts: list[CohortDraft] = []


class DerivationHook(Protocol):
    def propose(self, context: dict) -> DeriveResult: ...


class LLMDeriver:
    def __init__(self, llm: StructuredLLM, relation_vocab: list[str], retry: int = 2):
        self._llm = llm
        self._vocab = relation_vocab
        self._retry = retry

    def propose(self, context: dict) -> DeriveResult:
        user = (
            "本轮新证据(含原文与实体):\n"
            + json.dumps(context.get("new_evidence", []), ensure_ascii=False)
            + "\n\n现有未决 claim:\n"
            + json.dumps(context.get("open_claims", []), ensure_ascii=False)
            + "\n\n现有 claim 草案 id(新 id 须续接编号,不得冲突):\n"
            + json.dumps(context.get("existing_ids", []), ensure_ascii=False)
            + "\n\n关系类型词表(关系类 relationType 须命中):\n"
            + json.dumps(self._vocab, ensure_ascii=False)
        )
        return call_structured(
            self._llm, stage="s5_derive", system=S5_DERIVE_PROMPT, user=user,
            model_cls=DeriveResult, retry=self._retry)


class LedgerWriter:
    def __init__(self, ledger: LedgerRepository, domain: DomainConfig,
                 deriver: DerivationHook | None = None, assoc_mode: str = "entity"):
        self._ledger = ledger
        self._domain = domain
        self._deriver = deriver
        self._assoc_mode = assoc_mode

    # ------------------------------------------------------------- 主入账

    def commit(self, event_id: str, round_no: int, processed: list[dict],
               derive: bool = True) -> dict:
        """环①-⑥(⑦ 走 commit_refresh)。derive=False 用于轮 0(信封种子,
        此时 claim 尚未生成,派生无落点)。返回入账报告(评测与日志原料)。"""
        new_evidence: list[dict] = []
        associations: list[tuple[str, str]] = []
        dup_count = 0

        for item in processed:  # ①②③④: 接收->查重->(锚定已在 S4)->入账
            eid = self._ledger.insert_evidence(event_id, item, round_no)
            if eid is None:
                dup_count += 1
                continue
            new_evidence.append({
                "evidence_id": eid, "title": item.get("title", ""),
                "source": item.get("source", ""), "root": item.get("lineage_root", ""),
                "excerpt": (item.get("content", "") or "")[:120],
            })
            # 挂账(关联宽进,S6 方向严判):实体名交集;真跑档加词面重合通道
            candidates = set(self._claims_sharing_entities(event_id, item))
            if self._assoc_mode == "entity_text":
                candidates.update(self._claims_by_text_overlap(event_id, item))
            for claim_id in sorted(candidates):
                self._ledger.associate(claim_id, eid, round_no)
                associations.append((claim_id, eid))
            self._detect_forks(item)

        # ⑥ 派生(增强步骤:失败降级公告,不阻塞)
        derived: list[str] = []
        cohorts: list[str] = []
        derive_error = ""
        if self._deriver is not None and new_evidence and derive:
            try:
                result = self._deriver.propose(self._derive_context(
                    event_id, new_evidence))
                derived, cohorts = self._apply_derivations(event_id, round_no, result)
            except Exception as exc:
                derive_error = str(exc)[:200]

        report = {
            "new_evidence": [e["evidence_id"] for e in new_evidence],
            "associations": [f"{c}->{e}" for c, e in associations],
            "duplicates": dup_count, "derived_claims": derived,
            "cohorts": cohorts, "derive_error": derive_error,
        }
        self._ledger.log(event_id, round_no, "入账", report)
        return report

    def commit_refresh(self, event_id: str, round_no: int, changes: list[dict]) -> list[str]:
        """环⑦ 状态刷新:世界变化的确定性翻转(追踪模式常态负载)。"""
        return self._ledger.refresh_states(event_id, changes, round_no)

    def associate_backlog(self, event_id: str, round_no: int = 0) -> list[str]:
        """回溯挂账:轮 0 的信封证据(种子)在 claim 落库后补挂。

        两道:①实体交集挂账(rumor 线索卡等有实体的证据);②corpus_bound
        claim 直接挂 signal_layer 证据——signal_layer 证据(信封字段/检测器
        解释)entity_ids 恒空,实体交集永远挂不上;而 corpus_bound claim 的
        语料来源正是信封,其自证例外的"任一忠实单根"即此(2026-10-06 实证:
        tender 无种子形态 0/19 挂账 → 自证例外失效 → 全待证实)。signal_layer
        同谱系根,全挂不改变独立根数(数根=1),判决等价;rumor 线索卡有独立
        谱系根,不入此道(避免反方污染)。
        """
        rows = self._ledger.conn.execute(
            "SELECT evidence_id, entity_ids FROM evidence WHERE event_id=? AND round=?",
            (event_id, round_no)).fetchall()
        linked: list[str] = []
        for row in rows:
            try:
                eids = json.loads(row["entity_ids"] or "[]")
            except json.JSONDecodeError:
                eids = []
            if not eids:
                continue
            marks = ",".join("?" * len(eids))
            targets = self._ledger.conn.execute(
                f"SELECT DISTINCT c.claim_id FROM claims c"
                f" JOIN claim_entities ce ON ce.claim_id=c.claim_id"
                f" WHERE c.event_id=? AND ce.entity_id IN ({marks})",
                (event_id, *eids)).fetchall()
            for t in targets:
                self._ledger.associate(t["claim_id"], row["evidence_id"], round_no)
                linked.append(f"{t['claim_id']}->{row['evidence_id']}")

        # ② corpus_bound claim 挂 signal_layer 信封证据(语料来源单根自证)
        corpus = [r["claim_id"] for r in self._ledger.conn.execute(
            "SELECT claim_id FROM claims WHERE event_id=? AND corpus_bound=1",
            (event_id,)).fetchall()]
        if corpus:
            sig_evs = [r["evidence_id"] for r in self._ledger.conn.execute(
                "SELECT evidence_id FROM evidence WHERE event_id=? AND round=?"
                " AND lineage_root='signal_layer'",
                (event_id, round_no)).fetchall()]
            for cid in corpus:
                for eid in sig_evs:
                    self._ledger.associate(cid, eid, round_no)
                    linked.append(f"{cid}->{eid}(corpus_bound)")
        return linked

    # ------------------------------------------------------------- 内部

    def _claims_by_text_overlap(self, event_id: str, item: dict) -> list[str]:
        """词面重合关联(宽进第二通道):行业级模糊信号与真实新闻常无实体名
        交集,但词面重合(如"电池/产能/标准")。阈值=2 个共享词元。"""
        from ..collect.expansion import text_overlap
        ev_text = f"{item.get('title', '')} {item.get('content', '')}"
        out: list[str] = []
        for row in self._ledger.all_claims(event_id):
            if text_overlap(row["text"], ev_text, min_shared=2):
                out.append(row["claim_id"])
        return out

    def _claims_sharing_entities(self, event_id: str, item: dict) -> list[str]:
        """挂账面向**全部** claim(不只未决)——已证实 claim 也可能被新证据
        推翻(矛盾翻转的语义),关联宽进、S6 方向严判。"""
        eids = item.get("entity_ids") or []
        if not eids:
            return []
        marks = ",".join("?" * len(eids))
        rows = self._ledger.conn.execute(
            f"SELECT DISTINCT c.claim_id FROM claims c"
            f" JOIN claim_entities ce ON ce.claim_id=c.claim_id"
            f" WHERE c.event_id=? AND ce.entity_id IN ({marks})",
            (event_id, *eids),
        ).fetchall()
        return sorted(r["claim_id"] for r in rows)

    def _detect_forks(self, item: dict) -> None:
        """环⑤ 确定性分叉:同名实体在本条证据中映射到不同规范名 -> 标分叉。"""
        by_name: dict[str, set[str]] = {}
        for ent in item.get("entities", []):
            by_name.setdefault(normalize_text(ent["name"]).casefold(), set()).add(
                ent["canonical"])
        for name, canons in by_name.items():
            if len(canons) > 1:
                for ent in item.get("entities", []):
                    if normalize_text(ent["name"]).casefold() == name:
                        self._ledger.mark_entity_forked(ent["entity_id"])

    def _derive_context(self, event_id: str, new_evidence: list[dict]) -> dict:
        return {
            "new_evidence": new_evidence,
            "open_claims": [
                {"id": r["draft_id"], "text": r["text"]}
                for r in self._ledger.open_claims(event_id)
            ],
            "existing_ids": [r["draft_id"] for r in self._ledger.all_claims(event_id)],
        }

    def _apply_derivations(
        self, event_id: str, round_no: int, result: DeriveResult
    ) -> tuple[list[str], list[str]]:
        """派生落地(代码硬校验:id 不冲突/关系类命中词表/类目合法)。"""
        existing = {r["draft_id"] for r in self._ledger.all_claims(event_id)}
        derived: list[str] = []
        for draft in result.derived_claims:
            if draft.id in existing:
                continue
            if draft.category not in ("事实", "实体", "关系", "时效", "矛盾"):
                continue
            if draft.category == "关系" and (
                    draft.relation_type not in self._domain.relation_vocab
                    or len(draft.entity_names) < 2):
                continue  # 关系类硬校验:头尾+词表命中
            self._ledger.add_claim(event_id, draft.model_dump(), round_no,
                                   aliases=self._domain.aliases)
            for hyp in draft.hypothesis_links:
                self._ledger.add_hypothesis_claim(event_id, hyp, draft.id)
            derived.append(draft.id)
        cohorts: list[str] = []
        for coh in result.cohorts:
            cid = self._ledger.register_cohort(
                event_id, coh.name, coh.criteria, round_no)
            member_ids = []
            for m in coh.members:
                eid, _, _ = self._ledger.ensure_entity(
                    m, "company", self._domain.aliases, event_id)
                member_ids.append(eid)
            self._ledger.add_cohort_entities(cid, member_ids)
            cohorts.append(coh.name)
        return derived, cohorts
