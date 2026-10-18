"""主动澄清 P0:答案→台账传导(纯确定性,零 LLM;模型只出选项,代码出裁决)。

裁决三分支(问题包 adjudicationTarget 声明,2026-10-09 方案 §四):
  hypothesis=假设覆写:只钉 standings(latest-wins),**不动 claims/transitions**——
    分权:claim 层=证据账本只归 S6,假设层=研判可被人裁钉死;
  claim=口径重述:派生新条(原条文丝不动,parent 指原条,归同一节),不挂回
    hypothesis_claims(防双重记分);新条无证据,verify 早退零 LLM,只产卷宗行;
  evidence=证据登记:人答作为一份证言入账(单根 human,权威性零特判——
    默认非权威登记源=1 个普通独立根,域配置 credibility_rules 可显式收编)。

审计:全部路径落 kind='澄清' 第二行(不加 decision_log 新 kind,成文纪律),
payload.adjudication.source="human_adjudication"。目标存在性校验失败=整体拦截
不部分写,落 adjudication_blocked 审计,不抛异常(不阻断主流程,沿 calibrate 风格)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models.decision import AdjudicationTarget, QuestionPackage


@dataclass
class ApplyResult:
    ok: bool
    affected_claim_ids: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)   # 落地动作描述(审计与测试断言用)
    blocked_reason: str = ""


class Adjudicator:
    """裁决应用器:问题包 adjudicationTarget + 人答 → 台账写操作。"""

    def __init__(self, ledger):
        self._ledger = ledger

    # ------------------------------------------------------------------

    def apply(self, event_id: str, round_no: int,
              package: QuestionPackage, answer: str) -> ApplyResult:
        target = package.adjudication_target
        if target is None:
            return ApplyResult(ok=True)          # 未声明目标:旧件路径,P0 不传导(不落审计)
        mapping = {m.option: m.target_id for m in target.map}
        if answer not in mapping:
            return self._blocked(event_id, round_no, package, answer, target,
                                 f"答案 {answer!r} 不在裁决映射选项内")
        missing = self._missing_targets(event_id, target, mapping)
        if missing:
            return self._blocked(event_id, round_no, package, answer, target,
                                 "裁决目标不存在: " + ", ".join(missing))

        affected: list[str] = []
        applied: list[str] = []
        if target.kind == "hypothesis":
            chosen = mapping[answer]
            for option, tid in mapping.items():
                self._ledger.record_adjudication(
                    event_id, round_no, "hypothesis", f"{event_id}:{tid}",
                    option, selected=(tid == chosen))
            applied.append(
                f"假设覆写: 选中 {chosen},否决其余 {len(mapping) - 1} 项"
                "(只钉 standings,不改 claim 现态)")
        elif target.kind == "claim":
            new_cid, desc = self._restate_claim(
                event_id, round_no, package, answer, mapping[answer])
            affected.append(new_cid)
            applied.append(desc)
        else:  # evidence
            affected_evidence = self._register_evidence(
                event_id, round_no, package, answer, mapping[answer])
            applied.extend(affected_evidence)
            if applied and applied[-1].startswith("人答证据入账"):
                affected.append(f"{event_id}:{mapping[answer]}")

        self._log(event_id, round_no, package, answer, target,
                  "human_adjudication", applied, affected)
        return ApplyResult(ok=True, affected_claim_ids=affected,
                           applied=applied)

    # ------------------------------------------------------------------

    def _missing_targets(self, event_id: str, target: AdjudicationTarget,
                         mapping: dict[str, str]) -> list[str]:
        """目标存在性预检(整体拦截不部分写;hypothesis 查表,claim/evidence 查 claims)。"""
        missing: list[str] = []
        for tid in sorted(set(mapping.values())):
            if target.kind == "hypothesis":
                row = self._ledger.conn.execute(
                    "SELECT 1 FROM hypotheses WHERE hypothesis_id=?",
                    (f"{event_id}:{tid}",)).fetchone()
            else:
                row = self._ledger.claim_row(f"{event_id}:{tid}")
            if row is None:
                missing.append(tid)
        return missing

    def _restate_claim(self, event_id: str, round_no: int,
                       package: QuestionPackage, answer: str,
                       draft_id: str) -> tuple[str, str]:
        """口径重述:派生新条(文本加口径后缀,parent 指原条),原条不动。"""
        orig = self._ledger.claim_row(f"{event_id}:{draft_id}")
        existing = {r["draft_id"] for r in self._ledger.all_claims(event_id)}
        new_draft_id = draft_id + "R"
        while new_draft_id in existing:              # 确定性避撞(S5 派生可能已占 C1R)
            new_draft_id += "R"
        draft = {
            "id": new_draft_id,
            "text": f"{orig['text']}(口径:{answer[:80]})",
            "category": orig["category"],            # 原类目必合法(表 CHECK 已保证)
            "section_index": int(orig["section_id"].rsplit(":", 1)[-1][1:]),
            "priority": orig["priority"],
            "priority_reason": orig["priority_reason"] or "人工澄清口径重述",
            "relation_type": orig["relation_type"],
            "valid_window_days": orig["valid_window_days"],
            "refresh_cycle_days": orig["refresh_cycle_days"],
            "parent": orig["draft_id"],
            "entity_names": [],
        }
        new_cid = self._ledger.add_claim(event_id, draft, round_no)
        for link in self._ledger.conn.execute(       # 复制实体链接(role 保留)
                "SELECT entity_id, role FROM claim_entities WHERE claim_id=?",
                (orig["claim_id"],)).fetchall():
            self._ledger.link_claim_entity(new_cid, link["entity_id"], link["role"])
        self._ledger.record_adjudication(event_id, round_no, "claim", new_cid, answer)
        return new_cid, f"口径重述: {draft_id} -> {new_draft_id}(原条保留,新条入 open)"

    def _register_evidence(self, event_id: str, round_no: int,
                           package: QuestionPackage, answer: str,
                           draft_id: str) -> list[str]:
        """人答证据入账(单根 human)+挂目标 claim;dedup 撞键=不重复入账/复验。"""
        item = {
            "platform": "human",
            "source": "interactive_clarify",
            "url": "",
            "title": f"人工澄清:{package.question[:40]}",
            "content": f"问题:{package.question}\n用户裁决:{answer}",
            "published_at": "",
            "author_type": "用户裁决",
            "lineage_root": "human",                 # 单根;权威与否交域配置+S6 既有规则
            "credibility": "中",
            "entity_ids": [],
        }
        evidence_id = self._ledger.insert_evidence(event_id, item, round_no)
        if evidence_id is None:
            return ["人答证据与既有记录重复(dedup),不重复入账/复验"]
        self._ledger.associate(f"{event_id}:{draft_id}", evidence_id, round_no)
        return [f"人答证据入账 {evidence_id} -> {draft_id}(单根 human)"]

    def _blocked(self, event_id: str, round_no: int, package: QuestionPackage,
                 answer: str, target: AdjudicationTarget, reason: str) -> ApplyResult:
        self._log(event_id, round_no, package, answer, target,
                  "adjudication_blocked", [], [], blocked_reason=reason)
        return ApplyResult(ok=False, blocked_reason=reason)

    def _log(self, event_id: str, round_no: int, package: QuestionPackage,
             answer: str, target: AdjudicationTarget, mode: str,
             applied: list[str], affected: list[str],
             blocked_reason: str = "") -> None:
        """裁决应用审计(kind='澄清' 第二行;不加新 kind——CHECK 枚举纪律)。"""
        self._ledger.log(event_id, round_no, "澄清", {
            "mode": mode,
            "answer": answer,
            "question": package.question,
            "adjudication": {
                "target_kind": target.kind,
                "mappings": [m.model_dump(by_alias=True) for m in target.map],
                "applied": applied,
                "affected": affected,
                "blocked_reason": blocked_reason,
                "source": "human_adjudication",
            },
        })
