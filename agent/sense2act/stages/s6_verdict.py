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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from ..llm import StructuredLLM, call_structured
from ..models.base import ClaimStatus
from ..models.verdict import EvidenceJudgment, Opinion, VerdictResult
from ..predicate_form import merge as pf_merge
from ..prompts import S6_OPINION_PROMPT
from ..store.ledger import LedgerRepository
from ..t1_independence import distinct_support_roots


def load_flip_rules(config_dir: str | Path = "data/config") -> dict | None:
    """翻转规则表(D 线 D4,2026-11-08):flip_rules.json 版本化,改表即生效
    +测试锁(照 load_present_rules/ess_rules 数值参数纪律)。缺文件/非对象
    =None=修正通道全关(--flip-guard off 同形态,确定性零行为变化)。
    键:downgrade/hysteresis/cooldown_days/jitter_alarm;缺键=对应闸全关。
    独立于 claim_present_rules.json(判决规则与呈现规则分离)。"""
    p = Path(config_dir) / "flip_rules.json"
    if not p.exists():
        return None
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError, ValueError):
        return None
    return rules if isinstance(rules, dict) and rules else None


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
                 ss_blocklist: list[str] | None = None,
                 t1_near_dup_dist: int | None = None,
                 predicate_checker=None,
                 flip_rules: dict | None = None):
        self._ledger = ledger
        self._auth = set(authoritative_roots)
        self._provider = provider
        self._retry = retry
        self._topk = evidence_topk   # 卷宗上限(宽进关联下防意见爆炸)
        # 单源例外谓词负清单(2026-10-16 发现四):claim 文本命中时不适用
        # 权威登记源单源例外——比较/依赖性谓词需要的证据形态超出单份文件
        # 所能承载,须走独立根>=2 慢路。空/None=现行为(零迁移)。
        self._ss_blocklist = list(ss_blocklist or [])
        self._pf_forms: dict = {}    # draft_id -> 归一化 predicate_form(件 4)
        # T1 根独立性(件 5):None=off 数根现行为;int=支持根内容级折并阈值
        self._t1_dist = t1_near_dup_dist
        # 谓词充分性核对器(2026-10-25):None=off 不装配=零调用零痕迹;
        # 协议=blocks(claim_text, auth_evidence_rows) -> (blocked, detail)
        self._pc = predicate_checker
        # 翻转规则表(D 线 D4,2026-11-08):None=--flip-guard off=闸全关
        # (全链零行为变化,旧录制件回放零扰动);dict=on,缺键=单闸关。
        self._flip_rules = flip_rules

    def _ss_blocked(self, claim_text: str) -> bool:
        """信源条件谓词检查(纯子串,确定性;宁窄勿宽:错拦=晚点证实,
        漏拦=过度断言,故清单从窄)。"""
        if not self._ss_blocklist:
            return False
        return any(p in (claim_text or "") for p in self._ss_blocklist)

    def set_predicate_forms(self, forms: dict) -> None:
        """谓词形态标注注入(手术包件 4,2026-10-22):{draft_id: form dict}。

        由 investigation 在 S1b 入账后调用(--predicate-form on);空/缺 =
        现行为(纯 blocklist)。加法保守合并见 predicate_form.merge。
        """
        self._pf_forms = dict(forms or {})

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
        if self._t1_dist is not None:
            # T1 根独立性查检(件 5):支持根内容级折并(近重复根只计 1;
            # 判决侧与 ESS 侧同函数同输入=口径一致)。矛盾根不折(第一版)。
            by_root: dict = {}
            for e in evidence:
                by_root.setdefault(e["lineage_root"], e["content"])
            roots["支持"] = set(distinct_support_roots(
                sorted(roots["支持"]), lambda r: by_root.get(r, ""),
                self._t1_dist))
        auth_opp = bool(set(roots["矛盾"]) & self._auth)
        auth_sup = bool(set(roots["支持"]) & self._auth)
        ss_blocked = self._ss_blocked(claim.get("text", ""))
        # 谓词门消费(件 4,2026-10-22):加法保守合并 blocklist∨S1b 标注——
        # LLM 只能加拦不能解除(零回归);corpus_bound 自证例外分支在本门
        # 之后且不经此门(仲裁:谓词门不适用于自证路径)
        pf = self._pf_forms.get(claim.get("draft_id") or "")
        pf_hit = pf_merge(ss_blocked, pf)
        first_party_sup = (bool(claim.get("corpus_bound")) and bool(roots["支持"])
                           and not roots["矛盾"])
        # 谓词充分性核对闸(2026-10-25,定标探针七轮定标+用户拍板):仅权威
        # 放行路径(权威双根∨单源例外)且 claim 非 corpus_bound 时,核对证据
        # 内容是否载明 claim 谓词;不载明 → 维持待证实(三态纪律,不伪造终态;
        # 核对结论不删不改证据账本)。cb=1 自证例外豁免(探针定标:不豁免 36
        # 错杀);纯民间双根不闸(D1:19 条 over 全在权威路径,民间 0 条);
        # 谓词门(pf)先行不动——本闸加法保守,只能再加拦。
        pc_detail = None
        pc_blocked = False
        if (self._pc is not None and auth_sup and not auth_opp
                and not claim.get("corpus_bound") and roots["支持"]
                and (len(roots["支持"]) >= 2 or not pf_hit)):
            pc_blocked, pc_detail = self._pc.blocks(
                claim.get("text", ""),
                [e for e in evidence
                 if (e.get("lineage_root") or "") in self._auth])
        if auth_opp:
            # 权威登记源反证 -> 矛盾(若支持方同为权威,属登记源冲突,
            # 三态纪律判矛盾并双口径呈现,而非压成已证实)
            final = ClaimStatus.CONTRADICTED
        elif pc_blocked:
            # 充分性核对:核心谓词不载明(证据对题但断言超出),维持待证实
            final = ClaimStatus.PENDING
        elif len(roots["支持"]) >= 2:
            final = ClaimStatus.VERIFIED
        elif auth_sup and roots["支持"] and not pf_hit:
            final = ClaimStatus.VERIFIED  # 权威登记源单源例外(谓词门后)
        elif first_party_sup:
            # 自证例外:claim 是信封/语料内自证型断言(S1b 标注,代码判决),
            # "语料这么说"由任一忠实文本单根即证;有反方根时不放行(保守优先)
            final = ClaimStatus.VERIFIED
        elif len(roots["矛盾"]) >= 2 and not roots["支持"]:
            final = ClaimStatus.CONTRADICTED
        else:
            final = ClaimStatus.PENDING   # 孤证非权威:维持待证实(可否决模型)

        # (d2) 维持偏置闸(D 线 D4,2026-11-08,--flip-guard on 装配;None=
        # off=全链零行为):只对复验场景生效(claim 已有终态再判),首次判决
        # 零影响(回归锁)。降级需硬反证/冷却窗外拦;升回已证实有滞后。拦截
        # =维持当前终态+判决 payload 挂观察标(不加 decision_log 新 kind);
        # 放行=零额外痕迹(payload 形态与 off 一致)。抖动报警同源挂 payload。
        flip_meta = None
        jitter = None
        if self._flip_rules is not None:
            contra_rows = [e for e in evidence
                           if any(j.evidence_id == e["evidence_id"]
                                  and j.direction.value == "矛盾"
                                  for j in opinion.evidence_judgments)]
            final, flip_meta = self._flip_guard(
                claim, final, roots, contra_rows, auth_opp)
            jitter = self._jitter_alarm(claim)

        # 成因分类:仅"翻转"记成因;待证实->已证实是澄清,记 NULL
        cause = None
        if final == ClaimStatus.CONTRADICTED and claim["status"] != "矛盾":
            if claim["category"] == "时效" or roots["时效刷新"]:
                cause = "时效翻转"
            else:
                cause = "矛盾翻转"

        note = self._rule_note(roots, auth_opp, auth_sup, final,
                               bool(claim.get("corpus_bound")),
                               ss_blocked, pf, pc_blocked)
        if flip_meta is not None:
            note = flip_meta.get("note") or note   # 拦截=维持语义人话(读者不知情)
        result = VerdictResult(
            claim_id=claim_id, final_status=final,
            support_roots=roots["支持"], oppose_roots=roots["矛盾"],
            authoritative_oppose=auth_opp,
            overridden=(opinion.verdict != final),
            predicate_form=pf,
            note=note,
        )
        # (e) 归档
        self._archive(claim, result, opinion=opinion, round_no=round_no,
                      cause=cause, pc_detail=pc_detail,
                      flip_meta=flip_meta, jitter=jitter)
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
                   ss_blocked: bool = False, pf: dict | None = None,
                   pc_blocked: bool = False) -> str:
        if pc_blocked:
            return ("谓词充分性核对:证据不载明 claim 核心谓词"
                    "(权威放行路径,不载明→维持待证实)")
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
            src = "blocklist∨S1b标注" if (pf and pf.get("beyond_single_doc")) \
                else "blocklist"
            return (f"含信源条件谓词[{src}](单源例外不适用),权威支持根 "
                    f"{len(roots['支持'])} 个不足 2 -> 维持待证实")
        if pf and pf.get("beyond_single_doc") and auth_sup:
            return (f"谓词形态标注命中[S1b标注](单源例外不适用),权威支持根 "
                    f"{len(roots['支持'])} 个不足 2 -> 维持待证实")
        return (f"支持根 {len(roots['支持'])} 个(不足 2 且非权威登记源)"
                f" -> 维持待证实")

    def _archive(
        self, claim: dict, result: VerdictResult, opinion: Opinion | None,
        round_no: int, cause: str | None = None, pc_detail: dict | None = None,
        flip_meta: dict | None = None, jitter: dict | None = None,
    ) -> None:
        event_id = claim["event_id"]
        if opinion is not None:
            # 件 5(呈现完整化 B-4,2026-11-09 拍板带上):judgments 落
            # quote/reason——模型意见本就产出(quote 验票必为原文连续片段,
            # reason 随意见),此前归档丢弃=裁定清单无理由可展示的病根。
            # 加法式:旧回放对账不比 decision_log payload=安全;旧台账
            # 无 quote 键=清单自动回落只列方向(两态各自如实)。
            self._ledger.log(event_id, round_no, "意见", {
                "claim_id": claim["claim_id"], "verdict": opinion.verdict.value,
                "judgments": [
                    {"evidence_id": j.evidence_id, "direction": j.direction.value,
                     "quote": j.quote, "reason": j.reason}
                    for j in opinion.evidence_judgments],
            })
            for j in opinion.evidence_judgments:  # 逐条裁定入账
                self._ledger.set_ruling(
                    claim["claim_id"], j.evidence_id, j.direction.value, round_no)
        if result.final_status.value != claim["status"]:
            self._ledger.update_claim_status(
                claim["claim_id"], result.final_status.value, cause, round_no)
        payload = {
            "claim_id": claim["claim_id"], "final": result.final_status.value,
            "support_roots": result.support_roots,
            "oppose_roots": result.oppose_roots,
            "overridden": result.overridden, "skipped": result.skipped,
            "note": result.note,
        }
        if result.predicate_form is not None:   # 件 4 留痕(缺省省键=旧形态稳定)
            payload["predicate_form"] = result.predicate_form
        if pc_detail is not None:               # 充分性核对留痕(off=零痕迹)
            payload["predicate_check"] = pc_detail
        if flip_meta is not None:               # D4 维持留痕(off/放行=零痕迹;
            payload["flip_guard"] = flip_meta   #  拍板6:不加 decision_log 新 kind)
        if jitter is not None:                  # 抖动报警(拍板6 同载体)
            payload["jitter_alarm"] = jitter
        self._ledger.log(event_id, round_no, "判决", payload)

    # ------------------------------------------------------- D4 维持偏置闸

    _OFFICIAL_AUTHOR_TYPES = ("官方", "官方公告", "官方媒体")

    def _flip_guard(self, claim: dict, final: ClaimStatus, roots: dict,
                    contra_rows: list[dict], auth_opp: bool
                    ) -> tuple[ClaimStatus, dict | None]:
        """D4 维持偏置闸(拍板2 宁漏翻不误翻):复验场景的降级/升回过闸。

        边界(照施工单字面,不引申):只拦「claim 已有终态再判」的降级方向,
        与「降级后再升回已证实」的滞后;首次判决(待证实→X)零影响(回归锁);
        final==current 无变更零痕迹。拦截=维持当前终态+返回留痕 dict
        (payload 挂 flip_guard,note=维持语义人话);放行=返回 (final, None)
        (零额外痕迹,回放对账稳定)。

        降级方向(已证实→矛盾/待证实,矛盾→待证实):
          - 冷却窗:末次证态变更(transitions 末条,含升/降)后 cooldown_days
            内拦;权威反证豁免(法定公示变更非噪音)。「同强度证据不再二次
            翻转」的落地=非权威反证一律视为同强度(施工内定,实录备考);
          - 硬反证门槛:hard 根(权威登记源根∨一手官方高可信根;当事方自述
            =利益相关陈述不算 hard,author_type 精确等值与链装配侧当事方
            判据同源;其余=转述级)≥ downgrade.hard_root,∨ 权威反证单源
            (downgrade.authoritative_single;内部权威≠免检:只豁免单源门槛
            不豁免复验本身)。
        升级方向(→已证实)滞后闸:hysteresis.promote_indep_roots——「降级后
          再升回」判据=transitions 存在 new_status='已证实'(到过已证实态,
          含时钟降级与矛盾翻转两种离开路径)∧ 当前不在已证实;不对称 2:1=
          升回需独立支持根达阈值(单源例外/自证例外单根放行路径在升回场景
          停用,双根路径天然满足)。矛盾态首次洗白(从未到过已证实)不在闸
          范围(照施工单字面不引申),由抖动监控兜底观察,实录备考。
        """
        rules = self._flip_rules or {}
        current = ClaimStatus(claim["status"])
        if final == current:
            return final, None                      # 无变更:零痕迹
        downgrades = {
            (ClaimStatus.VERIFIED, ClaimStatus.CONTRADICTED),
            (ClaimStatus.VERIFIED, ClaimStatus.PENDING),
            (ClaimStatus.CONTRADICTED, ClaimStatus.PENDING),
        }
        if (current, final) in downgrades:
            dg = rules.get("downgrade") or {}
            if not dg:
                return final, None                  # 缺键=降级闸关
            cd = rules.get("cooldown_days")
            if cd is not None and not auth_opp:
                last = self._last_flip_ts(claim["claim_id"])
                if last and self._within_cooldown(last, int(cd)):
                    return current, {
                        "action": "cooldown_hold",
                        "note": "既有结论维持(新出现的信息尚不足以构成变更)",
                        "last_flip_ts": last,
                        "cooldown_days": int(cd)}
            hard = self._hard_oppose_roots(roots["矛盾"], contra_rows)
            enough = False
            need = dg.get("hard_root")
            if need is not None and len(hard) >= int(need):
                enough = True
            if dg.get("authoritative_single") and auth_opp:
                enough = True
            if not enough:
                return current, {
                    "action": "observe_only",
                    "note": "新反证不足以推翻既有结论,继续观察(维持)",
                    "hearsay_roots": [r for r in roots["矛盾"] if r not in hard],
                    "hard_roots": hard}
            return final, None                      # 硬反证达门槛:放行
        if final == ClaimStatus.VERIFIED:
            # 升回场景(矛盾→已证实 / 降级后待证实→已证实):滞后闸
            hy = rules.get("hysteresis") or {}
            need = hy.get("promote_indep_roots")
            if need is None:
                return final, None                  # 缺键=滞后闸关
            if (self._reached_verified(claim["claim_id"])
                    and len(roots["支持"]) < int(need)):
                return current, {
                    "action": "hysteresis_hold",
                    "note": "既有结论维持(重新确立需更多独立来源相互印证)",
                    "support_roots": list(roots["支持"]),
                    "promote_indep_roots": int(need)}
        return final, None

    def _hard_oppose_roots(self, opp_roots: list[str],
                           contra_rows: list[dict]) -> list[str]:
        """硬反证根(D4-B 施工内定,测试锁):独立根∧非纯转述。

        hard = 权威登记源根(lineage_root ∈ 权威清单)∨ 一手官方高可信根
        (该根名下判「矛盾」的全部证据 author_type 均属官方系∧credibility=
        高);当事方自述(author_type 精确等值「当事方」,与链装配侧当事方
        判据同源)=利益相关陈述,不算 hard;其余(自媒体/媒体/聚合平台/
        认证账号等)=转述级 observe_only。
        """
        hard: list[str] = []
        for r in opp_roots:
            if r in self._auth:
                hard.append(r)
                continue
            rows = [e for e in contra_rows
                    if (e.get("lineage_root") or "") == r]
            if rows and all(
                    (e.get("author_type") or "") in self._OFFICIAL_AUTHOR_TYPES
                    and (e.get("credibility") or "") == "高"
                    for e in rows):
                hard.append(r)
        return hard

    def _last_flip_ts(self, claim_id: str) -> str:
        """末次证态变更时刻(transitions 末条 ts,含升/降;零 schema 改动)。"""
        row = self._ledger.conn.execute(
            "SELECT ts FROM claim_transitions WHERE claim_id=?"
            " ORDER BY id DESC LIMIT 1", (claim_id,)).fetchone()
        return row["ts"] if row else ""

    def _within_cooldown(self, ts: str, days: int) -> bool:
        try:
            t0 = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            return False
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t0) < timedelta(days=int(days))

    def _reached_verified(self, claim_id: str) -> bool:
        """「降级后再升回」判据:该 claim 曾到过已证实态(transitions 存在
        new_status='已证实'——含时钟降级[已证实→待证实]与矛盾翻转
        [已证实→矛盾]两种离开路径)。"""
        row = self._ledger.conn.execute(
            "SELECT 1 FROM claim_transitions WHERE claim_id=?"
            " AND new_status='已证实' LIMIT 1", (claim_id,)).fetchone()
        return row is not None

    def _jitter_alarm(self, claim: dict) -> dict | None:
        """抖动监控(D4-C):30 天窗口内证态变更次数超阈值 → 报警 dict
        (挂判决 payload,不静默不阻断;不上报告人看层,机器层可对账)。
        flips 计数取本次判决前已落账的 transitions(报警=历史已达阈值仍在
        翻转的形态)。缺键=监控关。"""
        ja = (self._flip_rules or {}).get("jitter_alarm") or {}
        limit = ja.get("flips_per_claim_per_30d")
        if limit is None:
            return None
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=30)).isoformat()
        rows = self._ledger.conn.execute(
            "SELECT ts FROM claim_transitions WHERE claim_id=? AND ts>=?",
            (claim["claim_id"], cutoff)).fetchall()
        if len(rows) <= int(limit):
            return None
        return {"flips_30d": len(rows), "threshold": int(limit),
                "note": "该结论近期反复变动,建议人工复核"}
