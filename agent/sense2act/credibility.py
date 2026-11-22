"""ESS 证据强度分(阶段一,2026-10-20;方案=《ESS证据强度分-实施方案-20261018.md》v0.3)。

ESS = clamp(Anchor − Decay − Oppose, 0, 100),整数输出;矛盾态=冲突不评分。

四条纪律(方案 §1.1,本模块是它们的物化):
① 分层——三态判决是唯一权威;ESS 是判决后的**只读派生**,永不回流:
   stages/s6_verdict.py 不 import 本模块(test_credibility.py 负测试锁死,P6);
② 代码算——确定性纯函数 f(台账快照, 规则表版本, as_of),零 LLM,不采信模型
   自报(HESP 实证:测量计数表 1.000 vs LLM 自填 0.069);
③ 透明可复算——整数输出不出小数;steps 结构化(因子/取值/效应逐行,渲染时才
   拼人话);规则表版本化(ess_rules.json,随 AV);ess.json 只存最新+声明可从
   db 全量重放(纯函数红利:历史曲线由重放生成,不维护第二份状态);
④ 一账两读——**分数给人读,形态(steps/锚点名)给机器用;任何行为回路永不见数**
   (S2 排序/S6 判决/企业排名三永不;诊断通道与 F1 只读形态——阶段三)。

锚点判据只用 S6 判决梯已承认的量(独立谱系根、权威登记源、corpus_bound、
unreachable/无关票),不引入判决不认识的新事实(防两套世界观)。s/o 与
LedgerRepository.independent_roots() 同口径(累积 claim_evidence 按 ruling
分组数非空 lineage_root;锚点行"待证实∧s≥2"只有此口径可达——卷宗 topk
截断/谓词门场景,方案"分数误差可容忍"),并由测试断言两口径逐 claim 相等。

查证尝试口径(诚实):unreachable ∨ 无关票(两支 db 可查);采集失败回执不落
库(只进内存观察包),"仅失败回执无关联票"的 claim 会落 5(未触及)——
方向=低估(保守侧),回执落库挂账后补第三支。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 规则表


class EssRulesError(Exception):
    """规则表缺失/损坏/schema 不合法(降级语义:整体跳过,不装 0 分)。"""


_REQUIRED_ANCHOR_NAMES = (
    "强(双根权威)", "强(权威单源)", "中(双根普通)", "中(自证)",
    "候审", "弱(孤证)", "弱(查过没有)", "未触及",
)


def load_rules(path: str | Path) -> dict:
    """加载并校验 ess_rules.json;任何缺失/损坏/不合法 → EssRulesError。

    校验即契约:13 个公开参数(8 锚值+3 桶边界+2 修正幅度)缺一不可,
    锚值严格降序(锚点表语义=降序首个命中)。
    """
    p = Path(path)
    if not p.exists():
        raise EssRulesError(f"规则表不存在: {p}")
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise EssRulesError(f"规则表不可读: {exc}") from exc
    if not isinstance(rules, dict):
        raise EssRulesError("规则表顶层必须是对象")
    version = rules.get("version")
    if not isinstance(version, str) or not version:
        raise EssRulesError("缺 version")
    anchors = rules.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != 8:
        raise EssRulesError("anchors 必须是 8 项(锚点表 v0.2)")
    names = [a.get("name") for a in anchors]
    if names != list(_REQUIRED_ANCHOR_NAMES):
        raise EssRulesError(f"锚点名序不合法: {names}")
    values = [a.get("value") for a in anchors]
    if (not all(isinstance(v, int) for v in values)
            or any(values[i] <= values[i + 1] for i in range(7))):
        raise EssRulesError(f"锚值必须为整数且严格降序: {values}")
    tiers = rules.get("tiers")
    if not isinstance(tiers, dict) or any(
            k not in tiers for k in ("强", "中", "弱")):
        raise EssRulesError("缺 tiers.强/中/弱 桶边界")
    for key in ("decay_per_window", "oppose_per_root"):
        v = rules.get(key)
        if not isinstance(v, (int, float)) or v < 0:
            raise EssRulesError(f"{key} 必须为非负数")
    if not isinstance(rules.get("oppose_cap_roots", 2), int):
        raise EssRulesError("oppose_cap_roots 必须为整数")
    return rules


# ---------------------------------------------------------------- 因子采集

_FA_SQL = (
    "SELECT e.lineage_root AS root, e.fetched_at AS fetched_at,"
    " e.evidence_id AS evidence_id, ce.ruling AS ruling, ce.round AS ce_round,"
    " e.content AS content"
    " FROM evidence e JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
    " WHERE ce.claim_id=?"
)


def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def collect_factors(ledger, event_id: str,
                    as_of_round: int | None = None,
                    near_dup_dist: int | None = None) -> list[dict]:
    """读台账 → 每 claim 因子快照(只读,零写零 LLM)。

    as_of_round:时点重算——按各表 round 列过滤(claim 取 created_round 之前
    生成、挂账取 ce.round、状态由 transitions 回放);None=最新时点。
    near_dup_dist:T1 根独立性(件 5)——非 None 时对支持根做内容级折并
    (与判决侧同函数同输入=口径一致;None=数根现行为)。
    """
    claim_rows = [dict(r) for r in ledger.all_claims(event_id)]
    trans_by_claim: dict[str, list[dict]] = {}
    if as_of_round is not None:
        for r in ledger.transitions(event_id):
            if r["round"] <= as_of_round:
                trans_by_claim.setdefault(r["claim_id"], []).append(dict(r))
    out: list[dict] = []
    for c in claim_rows:
        if as_of_round is not None and c["created_round"] > as_of_round:
            continue
        status = c["status"]
        if as_of_round is not None:
            status = "待证实"
            for t in trans_by_claim.get(c["claim_id"], []):
                status = t["new_status"]
        fa_rows = [dict(r) for r in ledger.conn.execute(_FA_SQL, (c["claim_id"],))]
        if as_of_round is not None:
            fa_rows = [r for r in fa_rows if r["ce_round"] <= as_of_round]
        roots: dict[str, set[str]] = {"支持": set(), "矛盾": set(),
                                      "时效刷新": set()}
        root_content: dict[str, str] = {}
        tried = bool(c["unreachable"])
        latest_sup_fetch = ""
        for r in fa_rows:
            if r["ruling"] == "无关":
                tried = True
            if r["ruling"] in roots and r["root"]:
                roots[r["ruling"]].add(r["root"])
                root_content.setdefault(r["root"], r["content"] or "")
                if r["ruling"] == "支持" and r["fetched_at"] > latest_sup_fetch:
                    latest_sup_fetch = r["fetched_at"]
        if near_dup_dist is not None and roots["支持"]:
            # T1 根独立性(件 5):支持根内容级折并——与判决侧同函数同输入,
            # 口径一致(test_credibility 口径断言自动延续)
            from .t1_independence import distinct_support_roots
            roots["支持"] = set(distinct_support_roots(
                sorted(roots["支持"]), lambda r: root_content.get(r, ""),
                near_dup_dist))
        out.append({
            "claim_id": c["claim_id"], "draft_id": c["draft_id"],
            "text": c["text"], "category": c["category"], "status": status,
            "corpus_bound": bool(c["corpus_bound"]),
            "unreachable": bool(c["unreachable"]),
            "valid_window_days": c["valid_window_days"],
            "priority": c["priority"],
            "support_roots": sorted(roots["支持"]),
            "oppose_roots": sorted(roots["矛盾"]),
            "tried": tried,
            "latest_support_fetched_at": latest_sup_fetch,
        })
    return out


# ---------------------------------------------------------------- 计分(纯)

def anchor_name(status: str, s: int, a: bool, cb: bool, tried: bool) -> str:
    """锚点表条件判定(降序首个命中),只产锚点名——数值查规则表。

    条件结构在代码(镜像判决梯),锚值在 ess_rules.json(版本化,V3 敏感性
    与保序重定标改表不改码)。判据只用判决梯已承认的量。已证实但四种判决
    路径都套不上的形态(理论不可达,防手工改库等脏状态)返回
    "已证实但形态未覆盖"——不装分(失败不伪装平移到分数层)。
    """
    if status == "矛盾":
        return "冲突"
    if status == "已证实":
        if s >= 2 and a:
            return "强(双根权威)"
        if s == 1 and a:
            return "强(权威单源)"
        if s >= 2:
            return "中(双根普通)"
        if cb and s >= 1:
            return "中(自证)"
        return "已证实但形态未覆盖"
    # 待证实
    if s >= 2 or (s == 1 and a):
        return "候审"
    if s == 1:
        return "弱(孤证)"
    if tried:
        return "弱(查过没有)"
    return "未触及"


def anchor_value(name: str, rules: dict) -> int | None:
    """锚点名 → 锚值(从规则表;冲突/未覆盖形态无值返回 None)。"""
    if name in ("冲突", "已证实但形态未覆盖"):
        return None
    for a in rules["anchors"]:
        if a["name"] == name:
            return a["value"]
    raise EssRulesError(f"规则表缺锚点名: {name}")


def anchor_lookup(status: str, s: int, a: bool, cb: bool, tried: bool,
                  rules: dict) -> tuple[int | None, str]:
    """一次查表双产出(锚值, 锚点名);数值由规则表提供。"""
    name = anchor_name(status, s, a, cb, tried)
    return anchor_value(name, rules), name


def tier_of(score: int | None, rules: dict) -> str | None:
    """档位=g(score) 纯读数(边界进规则表,与分数永不打架)。"""
    if score is None:
        return None
    if score >= rules["tiers"]["强"]:
        return "强"
    if score >= rules["tiers"]["中"]:
        return "中"
    if score >= rules["tiers"]["弱"]:
        return "弱"
    return "未触及"


def score_claim(factors: dict, rules: dict, as_of: datetime,
                authoritative_roots: set[str]) -> dict:
    """单 claim 计分(纯函数):ESS=clamp(Anchor−Decay−Oppose, 0, 100) 整数。

    Decay = floor(decay_per_window × min(age/W, 1));W=valid_window_days
    (无窗=无衰减依据→0;无支持证据→0;age=as_of−最新支持证据 fetched_at,
    负 age 按 0)。Oppose = oppose_per_root × min(独立反方根数, cap)。
    """
    sup = factors["support_roots"]
    s, o = len(sup), len(factors["oppose_roots"])
    a = bool(set(sup) & authoritative_roots)
    aname = anchor_name(factors["status"], s, a,
                        factors["corpus_bound"], factors["tried"])
    anchor = anchor_value(aname, rules)
    steps: list[dict[str, Any]] = [
        {"factor": "状态", "value": factors["status"], "effect": "锚点查表入口"},
        {"factor": "独立支持根 s", "value": s, "effect": "根=" + ",".join(sup)},
        {"factor": "权威根 a", "value": a,
         "effect": ("命中 " + ",".join(sorted(set(sup) & authoritative_roots)))
                   if a else "未命中"},
        {"factor": "corpus_bound", "value": factors["corpus_bound"],
         "effect": "自证形态候选" if factors["corpus_bound"] else ""},
        {"factor": "查证尝试", "value": factors["tried"],
         "effect": "unreachable∨无关票"},
    ]
    decay = 0
    age_days = 0
    w = factors["valid_window_days"] or 0
    if anchor is not None:
        steps.append({"factor": "锚点", "value": aname,
                      "effect": f"锚值 {anchor}"})
        if w > 0 and factors["latest_support_fetched_at"]:
            fetched = _parse_ts(factors["latest_support_fetched_at"])
            if fetched is not None:
                age_days = max(0, int((as_of - fetched).total_seconds() // 86400))
                ratio = min(age_days / w, 1.0)
                decay = int(math.floor(
                    rules["decay_per_window"] * ratio))
        cap = rules.get("oppose_cap_roots", 2)
        oppose = int(rules["oppose_per_root"] * min(o, cap))
        score = int(max(0, min(100, anchor - decay - oppose)))
        steps.append({"factor": "时效衰减", "value": age_days,
                      "effect": f"W={w} → decay −{decay}"})
        steps.append({"factor": "独立反方根 o", "value": o,
                      "effect": f"oppose −{oppose}"})
        steps.append({"factor": "分数", "value": score,
                      "effect": f"clamp({anchor}−{decay}−{oppose},0,100)"
                                f" → 档位 {tier_of(score, rules)}"})
    else:
        score = None
        steps.append({"factor": "锚点", "value": aname,
                      "effect": "冲突不评分" if aname == "冲突"
                                else "形态未覆盖,不装分"})
    return {
        "claim_id": factors["claim_id"], "draft_id": factors["draft_id"],
        "text": factors["text"], "category": factors["category"],
        "status": factors["status"],
        "priority": factors.get("priority", 0),  # 呈现消费(2026-10-26 门控);计分不用
        "anchor_name": aname, "anchor_value": anchor,
        "s": s, "a": a, "o": o,
        "corpus_bound": factors["corpus_bound"], "tried": factors["tried"],
        "valid_window_days": factors["valid_window_days"],
        "decay": decay if anchor is not None else 0,
        "oppose": (int(rules["oppose_per_root"] * min(o, rules.get("oppose_cap_roots", 2)))
                   if anchor is not None else 0),
        "score": score,
        "tier": tier_of(score, rules),
        "steps": steps,
    }


# ---------------------------------------------------------------- 汇总

def compute_all(ledger, event_id: str, rules: dict, as_of: datetime,
                authoritative_roots: set[str],
                as_of_round: int | None = None,
                near_dup_dist: int | None = None) -> dict:
    """全事件计分(finalize 末尾调用;P5:同 (db,规则,as_of) 重算逐字节一致)。

    near_dup_dist:T1 根独立性(件 5)——透传 collect_factors,判决侧与
    ESS 侧口径一致。返回 dict 可直接 json.dumps 为 ess.json 旁车(只存
    最新+声明可从 db 全量重放)。claims 按 claim_id 排序(确定性)。
    """
    claims = [
        score_claim(f, rules, as_of, authoritative_roots)
        for f in sorted(collect_factors(ledger, event_id, as_of_round,
                                        near_dup_dist=near_dup_dist),
                        key=lambda x: x["claim_id"])
    ]
    tier_counts: dict[str, int] = {"强": 0, "中": 0, "弱": 0, "未触及": 0,
                                   "冲突": 0}
    for c in claims:
        if c["tier"] is None:
            tier_counts["冲突"] += 1
        else:
            tier_counts[c["tier"]] += 1
    return {
        "event_id": event_id,
        "rule_version": rules["version"],
        "as_of": as_of.isoformat(timespec="seconds"),
        "as_of_round": as_of_round,
        "n_claims": len(claims),
        "tier_counts": tier_counts,
        "claims": claims,
        "replayable": "纯函数可从 db+规则表+as_of 全量重放(不维护第二份状态)",
    }
