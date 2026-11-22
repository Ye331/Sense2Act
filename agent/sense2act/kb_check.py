# -*- coding: utf-8 -*-
"""K5 规则卡比对(2026-10-27)——查表比对纯函数(数据层)。

蓝图=《实施方案/企业知识库-实施方案-20261025.md》§三要点5/6 + §6.2 路径③ +
§6.3 认识论四分:规则卡=独立规则表(尺子≠证据),**规则本身不参与三态**;
比对表每格走三态——已证实=载明∧比较真 / 矛盾=载明∧比较假 / 待证实=
无载明("查不到→Open Questions"纪律延伸,禁止伪装满足)。

纪律:
- **确定性零 LLM**:谓词/阈值全在规则表(数值进表),比对=机械比较;
- **证据优先级**=attributes 显式值 > 正卡区 claim 载明 > 未决区不作依据
  (未证实不能当合规证据,§七消费通道纪律同源;zones 正卡映射查
  kb_rules.json,参数从规则表消费);
- **holds 只证正不证负的分层语义**:attributes 显式值不含要求值=矛盾
  (结构化记录是断言,可判异);claim 文本不含=待证实(沉默≠否认)——
  v1 不做文本否定检测(零 LLM 约束下否定语义不可靠,永不产出假"矛盾");
- **数值谓词 v1 只消费 attributes 显式值**:claim 文本数值提取不可靠
  (一文多数),文本载明∧无法可靠取值→待证实如实呈现(演进项=
  predicate_form 落库后接谓词槽位);
- 金额归一 _money_yuan 与 crawler/adapters/ccgp.money_to_yuan 同款正则
  (注记同源零漂移;不跨层 import——kb 层不依赖 crawler adapters)。

呈现让位渲染批次(§十一-4):本模块 md 出口仅为数据层自检,资质核对表
正式呈现并入渲染批次(与 ESS 决策卡/K5 资质核对表同批单人执行)。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .kb_chain import close_mains, open_mains
from .kb_store import KbStore
from .store.ledger import LedgerRepository, alias_key

# 金额/数值归一(与 ccgp.money_to_yuan 同源同款;独立实现防跨层依赖)
_MONEY_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|万元|万|元|人民币)")


class KbCheckError(Exception):
    """比对输入不合法(规则/对象/参数缺失)。"""


def _money_yuan(text) -> float | None:
    """'800万元'->8000000.0;不可解析返回 None(不猜,与 ccgp 同款)。"""
    if text is None:
        return None
    s = str(text).replace(",", "").replace("，", "").replace(" ", "")
    m = _MONEY_RE.search(s)
    if not m:
        bare = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
        return float(bare.group(1)) if bare else None
    v = float(m.group(1))
    if m.group(2) in ("万元", "万"):
        v *= 10000
    elif m.group(2) in ("亿元", "亿"):
        v *= 100000000
    return v


def _norm(s: str) -> str:
    """规范化子串(alias_key 同源——别名归一/自证匹配同一套口径)。"""
    return alias_key(str(s or ""))


# ---------------------------------------------------------------- 组装

def assemble_subject(store: KbStore, mains: dict[str, LedgerRepository],
                     entity_id: str, zones: dict) -> dict:
    """比对被告对象组装:kb.db 读卡(attributes/claim 关联),主库读
    claim 文本+状态(三态现场读正本——铁律,kb.db 不存状态)。

    zones=kb_rules.json zones 节(正卡/未决分区映射,参数从规则表消费);
    claim 关联照 entity_link 正本路由(event 前缀,open_mains 同款)。"""
    ents = {e["entity_id"]: e for e in store.entities()}
    ent = ents.get(entity_id)
    if ent is None:
        raise KbCheckError(f"比对对象卡不存在: {entity_id}")
    positive = set(zones.get("正卡") or [])
    claims: list[dict] = []
    for l in sorted(store.links(entity_id, "claim"),
                    key=lambda x: x["ref_id"]):
        ref = l["ref_id"]
        event_id = ref.rsplit(":", 1)[0]
        led = mains.get(event_id)
        if led is None:
            raise KbCheckError(f"claim 主库未提供: {ref}(缺 {event_id})")
        row = led.conn.execute(
            "SELECT claim_id, text, status FROM claims WHERE claim_id=?",
            (ref,)).fetchone()
        if row is None:
            raise KbCheckError(f"claim 不在主库: {ref}")
        claims.append({"claim_id": row["claim_id"], "text": row["text"],
                       "status": row["status"],
                       "in_positive": row["status"] in positive})
    return {"entity_id": entity_id,
            "canonical": ent["names"].get("canonical", ""),
            "attributes": dict(ent.get("attributes") or {}),
            "claims": claims}


# ---------------------------------------------------------------- 比对内核

def _check_one(item: dict, subject: dict, positive: set) -> dict:
    """单格比对(一条规则条目×一个对象)→ 三态+依据。纯函数。"""
    field, pred, value = item["field"], item["predicate"], item["value"]
    attr_val = subject["attributes"].get(field)
    attr_val = str(attr_val).strip() if attr_val not in (None, "") else ""
    claim_hits = [c for c in subject["claims"] if c["in_positive"]
                  and _norm(field) and _norm(field) in _norm(c["text"])]
    basis, observed = "", ""
    if attr_val:
        basis, observed = f"attributes:{field}={attr_val}", attr_val
    elif claim_hits:
        c = claim_hits[0]
        basis, observed = f"claim:{c['claim_id']}", c["text"]

    def _cell(status: str, reason: str) -> dict:
        return {"status": status, "basis": basis, "reason": reason}

    if pred in ("gte", "lte"):
        if not observed:
            return _cell("待证实",
                         f"未载明「{field}」(文本提及无显式数值不作数,"
                         f"数值谓词只消费结构化属性)")
        lhs, rhs = _money_yuan(observed), _money_yuan(value)
        if lhs is None or rhs is None:
            return _cell("待证实",
                         f"数值不可比(载明值「{observed}」或阈值"
                         f"「{value}」不可解析,不猜)")
        ok = lhs >= rhs if pred == "gte" else lhs <= rhs
        op = "≥" if pred == "gte" else "≤"
        return _cell("已证实" if ok else "矛盾",
                     f"载明 {field}={observed}({lhs:g}) {op} "
                     f"{value}({rhs:g})→{'满足' if ok else '不满足'}")
    if pred == "holds":
        if not observed:
            return _cell("待证实",
                         f"未载明「{field}」(正卡区无相关 claim,"
                         f"沉默≠否认,不判矛盾)")
        has = _norm(value) in _norm(observed)
        if has:
            return _cell("已证实", f"载明含「{value}」→满足")
        if basis.startswith("attributes:"):
            return _cell("矛盾",
                         f"显式记录 {field}={observed} 不含"
                         f"「{value}」→不满足(结构化记录是断言,可判异)")
        return _cell("待证实",
                     f"claim 文本未含「{value}」(沉默≠否认,不判矛盾)")
    if pred == "exists":
        if observed:
            return _cell("已证实", f"「{field}」已载明")
        return _cell("待证实", f"「{field}」无载明记录")
    raise KbCheckError(f"谓词不识别: {pred}(白名单查 kb_rules.json "
                       f"rule_check.predicates)")


def compare_rules(rules: list[dict], subjects: list[dict],
                  zones: dict) -> list[dict]:
    """查表比对纯函数:规则条目×对象 → 每格三态(确定性排序:
    rule_id→rid→entity_id;同输入逐字节一致)。

    rules=[{rule_id,name,items:[{rid,kind,field,predicate,value,text}]}];
    subjects=assemble_subject 产出;zones=kb_rules.json zones 节。"""
    if not isinstance(zones, dict) or not (zones.get("正卡") or []):
        raise KbCheckError("zones 缺正卡分区映射(参数从 kb_rules.json 消费)")
    positive = set(zones["正卡"])
    rows: list[dict] = []
    for rule in sorted(rules, key=lambda r: r["rule_id"]):
        for item in sorted(rule.get("items") or [],
                           key=lambda x: x.get("rid", "")):
            for sub in sorted(subjects, key=lambda s: s["entity_id"]):
                cell = _check_one(item, sub, positive)
                rows.append({
                    "rule_id": rule["rule_id"], "rule_name": rule["name"],
                    "rid": item["rid"], "kind": item.get("kind", "准入"),
                    "field": item["field"], "predicate": item["predicate"],
                    "value": item["value"], "requirement": item.get("text", ""),
                    "subject_id": sub["entity_id"],
                    "subject_name": sub.get("canonical", ""),
                    **cell})
    return rows


# ---------------------------------------------------------------- 渲染(数据层自检出口)

_STATUS_BADGE = {"已证实": "✓", "矛盾": "✗", "待证实": "?"}


def render_check(rows: list[dict], fmt: str = "md") -> str:
    """md=人看表格(资质核对表雏形,正式呈现=渲染批次);json=机器。
    双产出同一数据零分叉(照 kb_chain 渲染器纪律)。"""
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True)
    by_sub: dict[str, list[dict]] = {}
    for r in rows:
        by_sub.setdefault(
            (r["subject_id"], r["subject_name"]), []).append(r)
    out = ["# 资质核对表(K5 数据层出口;正式呈现=渲染批次)", ""]
    for (sid, sname), items in sorted(by_sub.items()):
        out.append(f"## {sname or sid}(`{sid}`)")
        out.append("")
        out.append("| rid | 种类 | 要求 | 结论 | 依据 |")
        out.append("|---|---|---|---|---|")
        for r in items:
            badge = _STATUS_BADGE.get(r["status"], "?")
            basis = r["basis"] or "—"
            req = r["requirement"] or f"{r['field']} {r['predicate']} {r['value']}"
            out.append(f"| {r['rid']} | {r['kind']} | {req} "
                       f"| {badge} {r['status']} | {basis} |")
        out.append("")
    n_pos = sum(1 for r in rows if r["status"] == "已证实")
    n_neg = sum(1 for r in rows if r["status"] == "矛盾")
    n_unk = sum(1 for r in rows if r["status"] == "待证实")
    out.append(f"> 合计 {len(rows)} 格:已证实 {n_pos}/矛盾 {n_neg}/"
               f"待证实 {n_unk}(待证实=查不到→Open Questions,不伪装满足)")
    return "\n".join(out)


# ---------------------------------------------------------------- CLI 装配

def run_check(kb_path: str | Path, db_paths: list[str | Path],
              rule_name: str, subject_id: str, zones: dict,
              config_dir: str | Path = "data/config") -> list[dict]:
    """CLI 便捷装配:kb.db 读规则+卡,主库路由读 claim 正本,出比对格。"""
    if not Path(kb_path).exists():
        raise KbCheckError(f"kb.db 不存在: {kb_path}(先 kb-derive 派生)")
    store = KbStore.open(kb_path)
    try:
        rules = ([r for r in store.rules() if not rule_name
                  or r["name"] == rule_name])
        if rule_name and not rules:
            raise KbCheckError(f"规则卡不存在: {rule_name}(已有="
                               f"{[r['name'] for r in store.rules()]})")
        if not rules:
            raise KbCheckError("kb.db 无规则卡(先 kb-import --rules 上传)")
        mains = open_mains(db_paths, config_dir)
        try:
            subjects = [assemble_subject(store, mains, subject_id, zones)]
        finally:
            close_mains(mains)
        return compare_rules(rules, subjects, zones)
    finally:
        store.conn.close()
