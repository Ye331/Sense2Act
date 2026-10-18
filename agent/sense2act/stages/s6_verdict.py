"""S6 Claim 级验证环(五步流水线,分权:模型出意见,代码出判决)。

(a) 代码组装卷宗 -> (b) LLM 单次温度 0 意见 -> (c) 代码验票(证据 id 归属 +
    quote 必须为原文连续片段;失败 re-prompt<=2 后维持原态,不卡死) ->
(d) 代码判决(独立谱系根计数;LLM verdict 仅意见可被否决) -> (e) 事务归档。

定级规则:独立根>=2 -> 已证实;权威登记源单源例外 -> 已证实;
自证例外(corpus_bound 且有支持根且无反方)-> 已证实;
孤证非权威 -> 维持待证实;反方含权威登记源 -> 矛盾。
not-enough-info 落在代码侧(数根数的结果),故 direction 无"证据不足"值。

自证例外的认识论依据:corpus_bound claim 的主语是信封/语料自身("信封载明 X"
"线索 k3 记载 Y"),任何一份忠实文本单根即证"语料这么说";双独立根约束的
是世界断言("X 真的发生了"),不适用于存在性断言。标注由 S1b 模型给出,
误标会被金标核对暴露(误标世界断言 -> 单根放行 -> 过度断言)。
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Protocol

from ..llm import StructuredLLM, call_structured
from ..models.base import ClaimStatus
from ..models.verdict import EvidenceJudgment, Opinion, VerdictResult
from ..prompts import S6_OPINION_PROMPT
from ..store.ledger import LedgerRepository


def _norm_ws(text: str) -> str:
    """引文比对口径:空白折叠 + NFKC(保留 CJK 语义)。"""
    norm = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", norm)


class OpinionProvider(Protocol):
    def opinion(self, claim: dict, evidence: list[dict],
                feedback: str = "") -> Opinion: ...


class LLMOpinionProvider:
    def __init__(self, llm: StructuredLLM, retry: int = 2):
        self._llm = llm
        self._retry = retry

    def opinion(self, claim: dict, evidence: list[dict],
                feedback: str = "") -> Opinion:
        user = (
            "claim(草案 id " + claim["draft_id"] + "):\n"
            + json.dumps({k: claim[k] for k in ("draft_id", "text", "category",
                                                "status")}, ensure_ascii=False)
            + "\n\n名下证据(逐条给方向;quote 从 content 逐字复制):\n"
            + json.dumps([
                {"evidence_id": e["evidence_id"], "title": e["title"],
                 "content": e["content"][:800],
                 "lineage_root": e["lineage_root"], "credibility": e["credibility"],
                 "author_type": e["author_type"], "platform": e["platform"]}
                for e in evidence], ensure_ascii=False)
            + (f"\n\n上一次意见验票失败,请修正:\n{feedback}" if feedback else "")
        )
        return call_structured(
            self._llm, stage="s6_opinion", system=S6_OPINION_PROMPT, user=user,
            model_cls=Opinion, retry=self._retry)


class VerdictEngine:
    def __init__(self, ledger: LedgerRepository, authoritative_roots: set[str],
                 provider: OpinionProvider, retry: int = 2,
                 evidence_topk: int = 8,
                 ss_blocklist: list[str] | None = None):
        self._ledger = ledger
        self._auth = set(authoritative_roots)
        self._provider = provider
        self._retry = retry
        self._topk = evidence_topk   # 卷宗上限(宽进关联下防意见爆炸)
        # 单源例外谓词负清单(2026-10-16 发现四):claim 文本命中时不适用
        # 权威登记源单源例外——比较/依赖性谓词需要的证据形态超出单份文件
        # 所能承载,须走独立根>=2 慢路。空/None=现行为(零迁移)。
        self._ss_blocklist = list(ss_blocklist or [])

    def _ss_blocked(self, claim_text: str) -> bool:
        """信源条件谓词检查(纯子串,确定性;宁窄勿宽:错拦=晚点证实,
        漏拦=过度断言,故清单从窄)。"""
        if not self._ss_blocklist:
            return False
        return any(p in (claim_text or "") for p in self._ss_blocklist)

    # ------------------------------------------------------------------ 主入口

    def verify(self, claim_id: str, round_no: int) -> VerdictResult:
        # (a) 卷宗(代码组装;(b)(c)(d)(e) 的公共输入包,可离线复算)
        claim_row = self._ledger.claim_row(claim_id)
        if claim_row is None:
            raise KeyError(claim_id)
        evidence = [dict(r)
                    for r in self._ledger.evidence_for_claim(claim_id)][: self._topk]
        claim = dict(claim_row)
        self._ledger.log(claim["event_id"], round_no, "卷宗", {
            "claim_id": claim_id, "evidence_ids": [e["evidence_id"] for e in evidence],
        })

        if not evidence:  # 无证据可裁:维持原态(诚实状态)
            return VerdictResult(
                claim_id=claim_id, final_status=ClaimStatus(claim["status"]),
                note="无证据,维持原态")

        # (b)+(c) 意见与验票(语义校验在此层:quote 连续片段 + 证据归属 + 全覆盖)
        opinion: Opinion | None = None
        feedback = ""
        last_err = ""
        for _ in range(self._retry + 1):
            try:
                cand = self._provider.opinion(claim, evidence, feedback)
            except Exception as exc:
                last_err = str(exc)[:200]
                break  # 意见生成失败(含降级重试耗尽):维持原态,不卡死
            errs = self._check_ballot(cand, evidence, claim_id)
            if not errs:
                opinion = cand
                break
            feedback = "; ".join(errs)
            last_err = feedback
        if opinion is None:
            result = VerdictResult(
                claim_id=claim_id, final_status=ClaimStatus(claim["status"]),
                skipped=True, note=f"验票失败维持原态: {last_err[:120]}")
            self._archive(claim, result, opinion=None, round_no=round_no)
            return result

        # (d) 代码判决:数独立谱系根(模型意见可被否决)
        roots = {
            "支持": self._distinct_roots(opinion, "支持", evidence),
            "矛盾": self._distinct_roots(opinion, "矛盾", evidence),
            "时效刷新": self._distinct_roots(opinion, "时效刷新", evidence),
        }
        auth_opp = bool(set(roots["矛盾"]) & self._auth)
        auth_sup = bool(set(roots["支持"]) & self._auth)
        ss_blocked = self._ss_blocked(claim.get("text", ""))
        first_party_sup = (bool(claim.get("corpus_bound")) and bool(roots["支持"])
                           and not roots["矛盾"])
        if auth_opp:
            # 权威登记源反证 -> 矛盾(若支持方同为权威,属登记源冲突,
            # 三态纪律判矛盾并双口径呈现,而非压成已证实)
            final = ClaimStatus.CONTRADICTED
        elif len(roots["支持"]) >= 2:
            final = ClaimStatus.VERIFIED
        elif auth_sup and roots["支持"] and not ss_blocked:
            final = ClaimStatus.VERIFIED  # 权威登记源单源例外(谓词门后)
        elif first_party_sup:
            # 自证例外:claim 是信封/语料内自证型断言(S1b 标注,代码判决),
            # "语料这么说"由任一忠实文本单根即证;有反方根时不放行(保守优先)
            final = ClaimStatus.VERIFIED
        elif len(roots["矛盾"]) >= 2 and not roots["支持"]:
            final = ClaimStatus.CONTRADICTED
        else:
            final = ClaimStatus.PENDING   # 孤证非权威:维持待证实(可否决模型)

        # 成因分类:仅"翻转"记成因;待证实->已证实是澄清,记 NULL
        cause = None
        if final == ClaimStatus.CONTRADICTED and claim["status"] != "矛盾":
            if claim["category"] == "时效" or roots["时效刷新"]:
                cause = "时效翻转"
            else:
                cause = "矛盾翻转"

        result = VerdictResult(
            claim_id=claim_id, final_status=final,
            support_roots=roots["支持"], oppose_roots=roots["矛盾"],
            authoritative_oppose=auth_opp,
            overridden=(opinion.verdict != final),
            note=self._rule_note(roots, auth_opp, auth_sup, final,
                                 bool(claim.get("corpus_bound")),
                                 ss_blocked),
        )
        # (e) 归档
        self._archive(claim, result, opinion=opinion, round_no=round_no, cause=cause)
        # (f) 部分支撑派生(发现四):复合 claim 整体待证实而部分被撑时,模型
        #     可提案收窄断言;代码闸门全过才派生子 claim(拆 claim 不拆态)。
        #     子 claim 挂引用证据,下一轮 touched 判决自然接管(本函数不递归裁)。
        if final == ClaimStatus.PENDING and opinion is not None:
            self._maybe_derive_subclaim(claim, opinion, round_no)
        return result

    # ------------------------------------------------------------------ 内部

    def _maybe_derive_subclaim(self, claim: dict, opinion: Opinion,
                               round_no: int) -> None:
        """部分支撑派生(发现四,拆 claim 不拆态):复合 claim(命中单源例外
        谓词负清单)整体待证实、但模型提案了可被支持证据完整撑起的收窄断言时,
        按 C1R 模式派生子 claim(原条丝纹不动,复用 P1-8 口径重述的落账模式)。
        闸门宁少勿滥,任一不过即审计记 partial_derive_blocked 后放弃:
        ①父条命中谓词负清单(复合条才有"部分被撑"问题);
        ②提案文本非空、异于父条且确实更短(收窄);
        ③规范化后不与任何既有 claim 重复;
        ④引用证据非空且 ⊆ 本次判"支持"的证据(防幻觉引用)。
        分权:提案=模型意见,派生=代码判决。子 claim 挂引用证据后,下一轮
        touched 判决自然接管(本函数不递归裁)。"""
        prop = opinion.subclaim
        if prop is None:
            return
        text = (prop.text or "").strip()
        parent_text = claim.get("text", "")
        support_ids = {j.evidence_id for j in opinion.evidence_judgments
                       if j.direction.value == "支持"}
        reasons: list[str] = []
        if not self._ss_blocked(parent_text):
            reasons.append("父条不含信源条件谓词")
        if not text or text == parent_text or len(text) >= len(parent_text):
            reasons.append("提案文本非法(空/同父/未收窄)")
        elif _norm_ws(text) in {
                _norm_ws(r["text"])
                for r in self._ledger.all_claims(claim["event_id"])}:
            reasons.append("与既有 claim 重复")
        if not prop.evidence_ids:
            reasons.append("未引用支持证据")
        else:
            bad_ev = [e for e in prop.evidence_ids if e not in support_ids]
            if bad_ev:
                reasons.append(f"引用了非支持证据: {bad_ev}")
        if reasons:
            self._ledger.log(claim["event_id"], round_no, "其他", {
                "partial_derive_blocked": {
                    "parent": claim["claim_id"], "text": text[:80],
                    "reasons": reasons}})
            return
        existing = {r["draft_id"]
                    for r in self._ledger.all_claims(claim["event_id"])}
        new_draft_id = claim["draft_id"] + "a"
        while new_draft_id in existing:              # 确定性避撞(同 C1R)
            new_draft_id += "a"
        draft = {
            "id": new_draft_id,
            "text": text,
            "category": claim["category"],           # 原类目必合法(表 CHECK 已保证)
            "section_index": int(claim["section_id"].rsplit(":", 1)[-1][1:]),
            "priority": claim["priority"],
            "priority_reason": claim.get("priority_reason") or "部分支撑收窄派生",
            "relation_type": claim.get("relation_type"),
            "valid_window_days": claim.get("valid_window_days"),
            "refresh_cycle_days": claim.get("refresh_cycle_days"),
            "parent": claim["draft_id"],
            "entity_names": [],
        }
        new_cid = self._ledger.add_claim(claim["event_id"], draft, round_no)
        for link in self._ledger.conn.execute(       # 复制实体链接(role 保留)
                "SELECT entity_id, role FROM claim_entities WHERE claim_id=?",
                (claim["claim_id"],)).fetchall():
            self._ledger.link_claim_entity(new_cid, link["entity_id"], link["role"])
        for eid in prop.evidence_ids:                # 引用证据挂子 claim -> 下轮 touched
            self._ledger.associate(f"{claim['event_id']}:{new_draft_id}",
                                   eid, round_no)
        self._ledger.log(claim["event_id"], round_no, "其他", {
            "partial_derive": {"parent": claim["claim_id"], "child": new_cid,
                               "text": text[:120],
                               "evidence_ids": list(prop.evidence_ids)}})

    def _check_ballot(
        self, opinion: Opinion, evidence: list[dict], claim_id: str
    ) -> list[str]:
        errs: list[str] = []
        known = {e["evidence_id"]: e for e in evidence}
        for j in opinion.evidence_judgments:
            ev = known.get(j.evidence_id)
            if ev is None:
                errs.append(f"{j.evidence_id} 不挂在该 claim 名下(防幻觉 id)")
                continue
            hay = _norm_ws((ev.get("title") or "") + (ev.get("content") or ""))
            if _norm_ws(j.quote) not in hay:
                errs.append(
                    f"{j.evidence_id} 的 quote 不是原文连续片段(疑似编造/改写)")
        covered = {j.evidence_id for j in opinion.evidence_judgments}
        missing = set(known) - covered
        if missing:
            errs.append(f"意见未覆盖全部证据: {sorted(missing)}")
        return errs

    def _distinct_roots(
        self, opinion: Opinion, direction: str, evidence: list[dict]
    ) -> list[str]:
        roots = {
            j.evidence_id: (e.get("lineage_root") or "")
            for e in evidence for j in opinion.evidence_judgments
            if j.evidence_id == e["evidence_id"]
        }
        out = {
            roots[j.evidence_id]
            for j in opinion.evidence_judgments
            if j.direction.value == direction and roots.get(j.evidence_id)
        }
        return sorted(out)

    def _rule_note(self, roots: dict, auth_opp: bool, auth_sup: bool,
                   final: ClaimStatus, corpus_bound: bool = False,
                   ss_blocked: bool = False) -> str:
        if auth_opp:
            if auth_sup:
                return "支持方与反方均含权威登记源(登记源冲突) -> 矛盾(双口径)"
            return "反方含权威登记源 -> 矛盾"
        if final == ClaimStatus.VERIFIED and len(roots["支持"]) >= 2:
            return f"独立谱系根 {len(roots['支持'])} 个(>=2) -> 已证实"
        if final == ClaimStatus.VERIFIED and auth_sup:
            return "权威登记源单源例外 -> 已证实"
        if final == ClaimStatus.VERIFIED and corpus_bound:
            return "自证例外(语料/信封内可核对,单根放行) -> 已证实"
        if final == ClaimStatus.CONTRADICTED:
            return f"反方独立根 {len(roots['矛盾'])} 个且无支持根 -> 矛盾"
        if ss_blocked and auth_sup:
            return (f"含信源条件谓词(单源例外不适用),权威支持根 "
                    f"{len(roots['支持'])} 个不足 2 -> 维持待证实")
        return (f"支持根 {len(roots['支持'])} 个(不足 2 且非权威登记源)"
                f" -> 维持待证实")

    def _archive(
        self, claim: dict, result: VerdictResult, opinion: Opinion | None,
        round_no: int, cause: str | None = None,
    ) -> None:
        event_id = claim["event_id"]
        if opinion is not None:
            self._ledger.log(event_id, round_no, "意见", {
                "claim_id": claim["claim_id"], "verdict": opinion.verdict.value,
                "judgments": [
                    {"evidence_id": j.evidence_id, "direction": j.direction.value}
                    for j in opinion.evidence_judgments],
            })
            for j in opinion.evidence_judgments:  # 逐条裁定入账
                self._ledger.set_ruling(
                    claim["claim_id"], j.evidence_id, j.direction.value, round_no)
        if result.final_status.value != claim["status"]:
            self._ledger.update_claim_status(
                claim["claim_id"], result.final_status.value, cause, round_no)
        self._ledger.log(event_id, round_no, "判决", {
            "claim_id": claim["claim_id"], "final": result.final_status.value,
            "support_roots": result.support_roots,
            "oppose_roots": result.oppose_roots,
            "overridden": result.overridden, "skipped": result.skipped,
            "note": result.note,
        })
