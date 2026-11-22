# -*- coding: utf-8 -*-
"""知识库链层(C1,2026-10-26)——查询时现算纯函数,五投影+双产出。

方案=《实施方案/企业知识库-链compute_chain-设计方案-20261026.md》(§十 11
拍板点已全部照建议通过,2026-10-26)。链=kb.db 的第二种查询视图:对象卡答
"把 X 的卡调出来",链答"Y 结论的根据是什么、何时判的、何时被推翻过"。

铁律(蓝图 §二/§3.1,零改动):**链是查询时现算的纯函数,kb.db 不存链、
不存任何状态**——源库状态更新后链自动最新,零同步。本模块全程只读(主库
mode=ro + kb.db mode=ro),零 LLM、零判决、零记分(链是结论的呈现者不是
生产者:三态只读 claims/transitions,记分只读 hypothesis_standings)。

一个内核+五个投影(§三):_chain_core 六步纯函数产出结构化 dict(§四),
投影=对同一内核的编排——A 结论链(全量)/B 时序链(状态史放大)/C 话题链
(三段串编:信号轴+案卷互链+结论分区与 OQ 行)/D 证据履历链(指纹反查)/
E 假设链(研判级,standings 现成)。渲染双产出:dict(机器,K2 被调形态)
+ markdown(人看,敏感过滤内置)。

施工口径(两处 §六-8/跨库路由,方案注记同步):
- OQ 挂载消费(§五-C):"精确键命中"落地=claim_entities 锚定 join
  (canonical=别名表精确键锚定产物,相对子串宽进从严;探针实证:bench
  20 案 182 条待证实,全等=0/锚定 join=134/子串=75——全等是死判据);
- **多主库路由**:kb.db 是跨 run 聚合库,正本数据在每案一个主库——
  接口收 mains: dict[event_id, LedgerRepository],按 claim 的 event
  前缀路由;单库场景 open_mains(["x.db"]) 一行构造。

as_of 语义(§六-决策1):主轴=round(复用 collect_factors 现成语义);
None=现势;as_of(datetime,decay 用)默认取 events.updated_at(源侧
时间,确定性——禁 now() 与派生层同纪律);与 ess.json 对照时传其 as_of
即逐条可复算(纯函数红利,credibility §③)。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .credibility import (
    EssRulesError, collect_factors, load_rules as load_ess_rules, score_claim)
from .config import load_domain_config
from .kb_derive import open_main_ro
from .store.ledger import LedgerRepository

# 期望分区映射(§六-决策3:分区用现算状态,zone 快照只作对照)。
# 待复核=派生侧降权快照(病根族),对应正卡状态——不判漂移。
_ZONE_EXPECT = {"已证实": {"正卡", "待复核"}, "矛盾": {"正卡", "待复核"},
                "待证实": {"未决"}}
_ZONE_ORDER = {"矛盾": 0, "已证实": 1, "待证实": 2}


class KbChainError(Exception):
    """链查询失败(主库/kb.db 缺失、claim/卡/指纹/假设不存在——失败不伪装)。"""


# ---------------------------------------------------------------- 打开/装配

def open_kb_ro(path: str | Path) -> sqlite3.Connection:
    """kb.db 只读打开(mode=ro URI;与 open_main_ro 同款纪律)。"""
    p = Path(path)
    if not p.exists():
        raise KbChainError(f"kb.db 不存在: {p}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_mains(db_paths: list[str | Path],
               config_dir: str | Path = "data/config"
               ) -> dict[str, LedgerRepository]:
    """多主库路由表:扫每库 events 表建 {event_id: LedgerRepository(ro)}。

    同 event_id 出现在多库=同 run 多份(异常,显式报错——路由歧义不猜)。
    """
    mains: dict[str, LedgerRepository] = {}
    for p in db_paths:
        conn = open_main_ro(p)
        ids = [r["event_id"] for r in conn.execute(
            "SELECT event_id FROM events").fetchall()]
        for eid in ids:
            if eid in mains:
                conn.close()
                raise KbChainError(f"event_id 路由歧义({eid} 在多库出现): {p}")
            mains[eid] = LedgerRepository(conn, config_dir)
    return mains


def close_mains(mains: dict[str, LedgerRepository]) -> None:
    for led in mains.values():
        led.conn.close()


def _route(mains: dict[str, LedgerRepository], ref_id: str) -> LedgerRepository:
    """按 {event_id}:{draft_id} 前缀路由到正本主库。"""
    event_id = ref_id.rsplit(":", 1)[0]
    led = mains.get(event_id)
    if led is None:
        raise KbChainError(f"claim/假设的主库未提供: {ref_id}(缺 {event_id})")
    return led


def load_context(config_dir: str | Path = "data/config") -> dict:
    """便捷装配:ess_rules+authoritative_roots 一次载入(调用方传 chain_core)。"""
    d = Path(config_dir)
    try:
        ess = load_ess_rules(d / "ess_rules.json")
    except EssRulesError:
        ess = None    # 规则表缺失→如实"未评",不装 0 分(失败不伪装平移)
    domain = load_domain_config(d)
    return {"ess_rules": ess, "auth_roots": domain.authoritative_roots}


# ---------------------------------------------------------------- 内核六步

_EVIDENCE_SQL = (
    "SELECT e.evidence_id, e.platform, e.source, e.url, e.title, e.content,"
    " e.published_at, e.fetched_at, e.lineage_root, e.author_type, e.sensitive,"
    " e.round, e.simhash, ce.ruling, ce.round AS ce_round"
    " FROM evidence e JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
    " WHERE ce.claim_id=?"
)
_ROOTS_SQL = (
    "SELECT DISTINCT e.lineage_root AS root FROM evidence e"
    " JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
    " WHERE ce.claim_id=? AND ce.ruling=? AND e.lineage_root!=''"
)
_TRANS_SQL = (
    "SELECT old_status, new_status, cause, evidence_id, round, ts, id"
    " FROM claim_transitions WHERE claim_id=?"
)


def _claim_factors(mains, claim_id: str, as_of_round: int | None,
                   near_dup_dist: int | None, factors: dict | None) -> dict:
    """①③步:collect_factors 同函数取单 claim 因子(口径自动一致)。

    factors 预取(话题链批量场景)必须出自同一 (mains, as_of_round,
    near_dup_dist)——否则口径漂移,显式报错。
    """
    if factors is not None:
        if factors["claim_id"] != claim_id:
            raise KbChainError("factors 与 claim_id 不匹配")
        return factors
    led = _route(mains, claim_id)
    got = [f for f in collect_factors(led, claim_id.rsplit(":", 1)[0],
                                      as_of_round,
                                      near_dup_dist=near_dup_dist)
           if f["claim_id"] == claim_id]
    if not got:
        raise KbChainError(f"claim 不存在: {claim_id}")
    return got[0]


def chain_core(mains: dict[str, LedgerRepository],
               kb: sqlite3.Connection | None, claim_id: str, *,
               factors: dict | None = None,
               as_of: datetime | None = None,
               as_of_round: int | None = None,
               ess_rules: dict | None = None,
               auth_roots: set[str] | None = None,
               near_dup_dist: int | None = None) -> dict:
    """内核六步(§四):全确定性,同输入重算逐字节一致。

    ①claim 本体(collect_factors:状态现势/round 回放);②证据集
    (claim_evidence join evidence,无关件计入 tried 呈现);③根折并
    (T1 同函数第三消费方——原始根数与折后数并出);④强度(score_claim
    查表,ess_rules 未载→如实 None);⑤状态史(ts+id 排序;as_of_round
    时过滤 round≤N=回放不看到未来);⑥卡挂载+版本标注(kb.db 只读反查)。
    """
    event_id = claim_id.rsplit(":", 1)[0]
    led = _route(mains, claim_id)
    f = _claim_factors(mains, claim_id, as_of_round, near_dup_dist, factors)
    crow = led.claim_row(claim_id)
    if crow is None:
        raise KbChainError(f"claim 不存在: {claim_id}")

    # ② 证据集(挂账引用全量;as_of_round 同参过滤=与 collect_factors 同口径)
    ev_rows = [dict(r) for r in led.conn.execute(_EVIDENCE_SQL, (claim_id,))]
    if as_of_round is not None:
        ev_rows = [r for r in ev_rows if r["ce_round"] <= as_of_round]
    ev_rows.sort(key=lambda r: (r["ce_round"], r["evidence_id"]))
    for r in ev_rows:
        r["sensitivity"] = "internal" if r.pop("sensitive") else "public"

    # ③ 根折并(折并前件数=原始支持根−T1 折后;方案 A 样例"另 N 件同根折并")
    raw_sup = sorted(r["root"] for r in led.conn.execute(
        _ROOTS_SQL, (claim_id, "支持")))
    if as_of_round is not None:
        raw_sup = sorted({r["root"] for r in ev_rows
                          if r["ruling"] == "支持" and r["lineage_root"]})
    folded = len(raw_sup) - len(f["support_roots"])

    # ④ 强度(score_claim 查表;锚点名>数字纪律在渲染层)
    strength = None
    if ess_rules is not None:
        if as_of is None:
            erow = led.conn.execute(
                "SELECT updated_at FROM events WHERE event_id=?",
                (event_id,)).fetchone()
            as_of = datetime.fromisoformat(erow["updated_at"])
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        strength = score_claim(f, ess_rules, as_of,
                               set(auth_roots or ()))

    # ⑤ 状态史(事务时间为主轴;as_of_round=回放视角,不看到未来)
    trans = [dict(r) for r in led.conn.execute(_TRANS_SQL, (claim_id,))]
    if as_of_round is not None:
        trans = [t for t in trans if t["round"] <= as_of_round]
    trans.sort(key=lambda t: (t["ts"], t["id"]))

    # ⑥ 卡挂载+版本标注(kb.db 只读反查;链不写任何库)
    cards: list[dict] = []
    kb_version = ""
    if kb is not None:
        for r in kb.execute(
                "SELECT l.entity_id, l.zone, l.kb_version, l.note,"
                " e.kind, e.names_json FROM entity_link l"
                " JOIN entity e ON e.entity_id=l.entity_id"
                " WHERE l.ref_kind='claim' AND l.ref_id=?"
                " ORDER BY l.entity_id", (claim_id,)):
            cards.append({"entity_id": r["entity_id"], "kind": r["kind"],
                          "names": json.loads(r["names_json"]),
                          "zone_snapshot": r["zone"],
                          "link_kb_version": r["kb_version"],
                          "note": r["note"]})
        arow = kb.execute("SELECT kb_version FROM kb_anchor WHERE event_id=?",
                          (event_id,)).fetchone()
        kb_version = arow["kb_version"] if arow else ""

    return {
        "claim": {"claim_id": claim_id, "draft_id": crow["draft_id"],
                  "text": crow["text"], "category": crow["category"],
                  "priority": crow["priority"],
                  "corpus_bound": bool(crow["corpus_bound"]),
                  "unreachable": bool(crow["unreachable"]),
                  "status": f["status"],
                  "created_round": crow["created_round"],
                  "event_id": event_id},
        "evidence": ev_rows,
        "roots": {"支持": {"独立": f["support_roots"], "原始": raw_sup,
                          "折并": folded},
                  "矛盾": sorted(f["oppose_roots"])},
        "tried": f["tried"],
        "strength": strength,
        "transitions": trans,
        "cards": cards,
        "kb_version": kb_version,
        "as_of": as_of.isoformat(timespec="seconds") if as_of else None,
        "as_of_round": as_of_round,
    }


def _core_line(core: dict) -> dict:
    """成员/引用行一行式(投影 C/D/E 消费;点开=结论链 A)。"""
    st = core["strength"]
    return {"claim_id": core["claim"]["claim_id"],
            "draft_id": core["claim"]["draft_id"],
            "event_id": core["claim"]["event_id"],
            "text": core["claim"]["text"],
            "status": core["claim"]["status"],
            "anchor_name": st["anchor_name"] if st else None,
            "score": st["score"] if st else None,
            "tier": st["tier"] if st else None}


# ---------------------------------------------------------------- 五投影

def chain_conclusion(mains, kb, claim_id: str, **kw) -> dict:
    """投影 A:结论链(全量内核;"Y 结论的根据是什么")。"""
    return chain_core(mains, kb, claim_id, **kw)


def chain_timeline(mains, kb, claim_id: str, **kw) -> dict:
    """投影 B:时序链(⑤ 放大;"结论何时被推翻过")。"""
    core = chain_core(mains, kb, claim_id, **kw)
    overturned = [i for i, t in enumerate(core["transitions"])
                  if t["new_status"] == "矛盾"]
    core["overturn_indexes"] = overturned
    return core


def chain_topic(mains, kb, entity_id: str, *, ess_rules=None,
                auth_roots=None, as_of=None, as_of_round=None,
                near_dup_dist=None, with_graph=False,
                graph_seeds_path=None) -> dict:
    """投影 C:话题链(三段串编;对象卡的现势展开,§五-C)。

    三段=信号轴(到站未调查)+案卷互链(已开卷 run)+结论分区(逐条跑
    内核,**分区用现算状态**,zone 快照只作对照+漂移计数)+OQ 行殿后。
    未调查项目卡=信号轴非空、结论分区空(§八-⑧)。

    with_graph(2026-11-05 链形态图方案,加法式):True 时附 "graph" 键
    ={event_id: graph JSON}(chain_graph.build,逐 event 现算);缺省 False=
    返回结构与既有消费方逐字节一致(五投影返回结构零改动纪律)。
    """
    if kb is None:
        raise KbChainError("话题链需要 kb.db(卡与挂载关系所在)")
    ent = kb.execute("SELECT * FROM entity WHERE entity_id=?",
                     (entity_id,)).fetchone()
    if ent is None:
        raise KbChainError(f"卡不存在: {entity_id}")
    links = [dict(r) for r in kb.execute(
        "SELECT * FROM entity_link WHERE entity_id=?"
        " ORDER BY ref_kind, ref_id", (entity_id,))]

    signals = [l for l in links if l["ref_kind"] == "signal"]
    events = [l for l in links if l["ref_kind"] == "event"]
    oq_links = [l for l in links if l["ref_kind"] == "oq"]
    claim_links = [l for l in links if l["ref_kind"] == "claim"]

    # 批量取 factors(每 event 一次 collect_factors,内核复用同函数)
    factors_by_claim: dict[str, dict] = {}
    by_event: dict[str, list] = {}
    for l in claim_links:
        by_event.setdefault(l["ref_id"].rsplit(":", 1)[0], []).append(l)
    for eid, ls in by_event.items():
        led = _route(mains, ls[0]["ref_id"])
        for f in collect_factors(led, eid, as_of_round,
                                 near_dup_dist=near_dup_dist):
            factors_by_claim[f["claim_id"]] = f

    members, drift = [], 0
    for l in claim_links:
        core = chain_core(mains, kb, l["ref_id"],
                          factors=factors_by_claim.get(l["ref_id"]),
                          as_of=as_of, as_of_round=as_of_round,
                          ess_rules=ess_rules, auth_roots=auth_roots,
                          near_dup_dist=near_dup_dist)
        line = _core_line(core)
        line["cards"] = core["cards"]
        expect = _ZONE_EXPECT.get(line["status"], set())
        if l["zone"] and l["zone"] not in expect:
            drift += 1
        members.append(line)

    def _sort(m: dict):
        return (_ZONE_ORDER.get(m["status"], 9),
                0 if m["score"] is None else -m["score"], m["claim_id"])

    members.sort(key=_sort)
    zones = {"正卡": [m for m in members if m["status"] in ("矛盾", "已证实")],
             "未决": [m for m in members if m["status"] == "待证实"]}
    oq_rows = []
    for l in oq_links:
        cid = l["ref_id"]
        text, draft = "", ""
        led = mains.get(cid.rsplit(":", 1)[0])
        if led is not None:
            row = led.claim_row(cid)
            if row is not None:
                text, draft = row["text"] or "", row["draft_id"] or ""
        oq_rows.append({"claim_id": cid,
                        "event_id": cid.rsplit(":", 1)[0],
                        "draft_id": draft, "text": text,
                        "note": l["note"], "zone_snapshot": l["zone"]})
    oq_rows.sort(key=lambda r: (r["event_id"], r["claim_id"]))
    kbv = sorted({l["kb_version"] for l in links if l["kb_version"]})
    out = {"entity": {"entity_id": ent["entity_id"], "kind": ent["kind"],
                      "names": json.loads(ent["names_json"]),
                      "attributes": json.loads(ent["attributes_json"])},
           "signals": signals, "events": events,
           "members": members, "zones": zones,
           "zone_drift": drift,
           "oq": oq_rows, "kb_versions": kbv,
           "as_of_round": as_of_round}
    if with_graph:
        # 末位懒导入(链形态图方案,2026-11-05):默认路径零 import 零开销
        from .chain_graph import build_chain_graph
        out["graph"] = {
            eid: build_chain_graph(mains, kb, eid, ess_rules=ess_rules,
                                   auth_roots=auth_roots,
                                   near_dup_dist=near_dup_dist,
                                   as_of_round=as_of_round,
                                   seeds_path=graph_seeds_path)
            for eid in sorted(by_event)}
    return out


def chain_provenance(mains, kb, fingerprint: str, *, ess_rules=None,
                     auth_roots=None, as_of=None, near_dup_dist=None) -> dict:
    """投影 D:证据履历链(谱系视角;"这条内容被哪些结论引用过/在哪些 run
    复曝过",§五-D)。纯反查:evidence_index+claim_evidence,零新建逻辑。
    """
    if kb is None:
        raise KbChainError("证据履历链需要 kb.db(evidence_index 所在)")
    row = kb.execute("SELECT * FROM evidence_index WHERE fingerprint=?",
                     (fingerprint,)).fetchone()
    if row is None:
        raise KbChainError(f"指纹不存在: {fingerprint}")
    roots = json.loads(row["lineage_roots_json"])
    refs = json.loads(row["origin_refs_json"])
    # 同根成员指纹行数(谱系折算数给你看:同 root 下指纹行数=折并前件数)
    sibling_counts: dict[str, int] = {rt: 0 for rt in roots}
    if roots:
        for r in kb.execute("SELECT lineage_roots_json FROM evidence_index"):
            for rt in json.loads(r["lineage_roots_json"]):
                if rt in sibling_counts:
                    sibling_counts[rt] += 1
    cited = []
    for ref in sorted(refs):
        eid = ref.rsplit(":", 1)[0]
        led = mains.get(eid)
        if led is None:
            continue    # 该 run 主库未提供:如实留"未提供主库"注记,不猜
        for cr in led.conn.execute(
                "SELECT ce.claim_id, ce.ruling FROM claim_evidence ce"
                " WHERE ce.evidence_id=? ORDER BY ce.claim_id", (ref,)):
            core = chain_core(mains, kb, cr["claim_id"], as_of=as_of,
                              ess_rules=ess_rules, auth_roots=auth_roots)
            line = _core_line(core)
            line["via_evidence"] = ref
            line["ruling"] = cr["ruling"]
            cited.append(line)
    cited.sort(key=lambda m: m["claim_id"])
    return {"fingerprint": fingerprint,
            "source_type": row["source_type"],
            "sensitivity": row["sensitivity"],
            "observed_at": row["observed_at"],
            "url": row["url"], "simhash": row["simhash"],
            "lineage_roots": roots,
            "origin_refs": refs,
            "sibling_fingerprints": {k: v for k, v in
                                     sorted(sibling_counts.items())},
            "cited_by": cited,
            "kb_version": row["kb_version"]}


def chain_hypothesis(mains, kb, event_id: str, hypothesis_id: str, *,
                     ess_rules=None, auth_roots=None, as_of=None,
                     as_of_round=None, near_dup_dist=None) -> dict:
    """投影 E:假设链(研判级;"H1 这个研判结论现在站不站得住",§五-E)。

    记分行=hypothesis_standings 现成返回(含人裁钉死 adjudicated/dominant
    旗标——分权呈现:人裁只钉假设层不动 claim 层);成员行=每条成员 claim
    跑内核取一行式(点开=结论链 A)。op 只呈现不计逻辑组合(§五-E 边界)。
    """
    led = mains.get(event_id)
    if led is None:
        raise KbChainError(f"主库未提供: {event_id}")
    hrow = led.conn.execute(
        "SELECT * FROM hypotheses WHERE hypothesis_id=?",
        (hypothesis_id,)).fetchone()
    if hrow is None:
        raise KbChainError(f"假设不存在: {hypothesis_id}")
    standing = next((s for s in led.hypothesis_standings(event_id)
                     if s["hypothesis_id"] == hypothesis_id), None)
    members, ops = [], {}
    for r in led.conn.execute(
            "SELECT claim_id, op FROM hypothesis_claims WHERE hypothesis_id=?"
            " ORDER BY claim_id", (hypothesis_id,)):
        ops[r["claim_id"]] = r["op"]
        core = chain_core(mains, kb, r["claim_id"], as_of=as_of,
                          as_of_round=as_of_round, ess_rules=ess_rules,
                          auth_roots=auth_roots, near_dup_dist=near_dup_dist)
        members.append(_core_line(core))
    members.sort(key=lambda m: (0 if m["score"] is None else -m["score"],
                                m["claim_id"]))
    kb_version = ""
    if kb is not None:
        arow = kb.execute("SELECT kb_version FROM kb_anchor WHERE event_id=?",
                          (event_id,)).fetchone()
        kb_version = arow["kb_version"] if arow else ""
    return {"hypothesis_id": hypothesis_id, "event_id": event_id,
            "label": hrow["label"], "description": hrow["description"] or "",
            "standing": standing, "members": members,
            "ops": {k: ops[k] for k in sorted(ops)},
            "kb_version": kb_version,
            "as_of": as_of.isoformat(timespec="seconds") if as_of else None}


# ---------------------------------------------------------------- 渲染器

def _clip(text: str, n: int = 44) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def _ev_line(ev: dict) -> str:
    """证据行(internal 敏感件=存在性+定位符,§六-决策5;失败不伪装)。"""
    ruling = ev.get("ruling") or "未裁"
    if ev.get("sensitivity") == "internal":
        return (f"{ev['evidence_id']} {ruling} · r{ev['ce_round']} · "
                "〔internal 敏感件:存在性留痕,内容不出境〕")
    pub = f" · 发布 {ev['published_at']}" if ev["published_at"] else ""
    src = ev.get("source") or ev.get("platform") or ""
    return (f"{ev['evidence_id']} {ruling} · r{ev['ce_round']}{pub} · "
            f"{src} · {_clip(ev.get('title') or ev.get('content') or '', 36)}")


def _badge(strength: dict | None) -> str:
    """锚点名>数字,数字必带 /100(ess_present 同款纪律)。"""
    if strength is None:
        return "未评(规则表未载)"
    if strength["score"] is None:
        return strength["anchor_name"]
    return f"{strength['anchor_name']}·{strength['score']}/100"


_RULING_ORDER = {"支持": 0, "矛盾": 1, "时效刷新": 2, None: 3, "无关": 4}


def render_conclusion(core: dict) -> str:
    c = core["claim"]
    head = f"结论链 · {c['claim_id']} 〔{c['status']}〕"
    if core["kb_version"]:
        head += f" · {core['kb_version']}"
    lines = [head, f"  断言:{_clip(c['text'], 60)}"]
    if c["corpus_bound"]:
        lines.append("  〔自证复述族→卡面待复核区〕")
    r = core["roots"]
    sup = r["支持"]
    note = f"(另 {sup['折并']} 根同根折并)" if sup["折并"] else ""
    tried = ";含查证尝试(无关票/unreachable)" if core["tried"] else ""
    lines.append(f"  证据 {len(core['evidence'])} 件 · "
                 f"支持独立根 {len(sup['独立'])}{note}{tried}")
    for ev in sorted(core["evidence"], key=lambda e: (
            _RULING_ORDER.get(e.get("ruling"), 3),
            e["ce_round"], e["evidence_id"])):
        lines.append(f"   ├ {_ev_line(ev)}")
    lines.append(f"  强度:{_badge(core['strength'])}")
    if core["transitions"]:
        lines.append("  状态史:")
        for t in core["transitions"]:
            cause = f" {t['cause']}" if t["cause"] else ""
            lines.append(f"   ├ {t['ts']}  {t['old_status']} → "
                         f"{t['new_status']}{cause}"
                         + (f" ({t['evidence_id']})" if t["evidence_id"] else ""))
    else:
        lines.append("  状态史:无翻转记录")
    if core["cards"]:
        ids = ",".join(f"{cd['entity_id']}"
                       + (f"[{cd['zone_snapshot']}]" if cd["zone_snapshot"] else "")
                       for cd in core["cards"])
        lines.append(f"  卡:{ids}")
    if core["as_of"]:
        lines.append(f"  (as_of={core['as_of']}"
                     + (f" round={core['as_of_round']}"
                        if core["as_of_round"] is not None else "") + ")")
    return "\n".join(lines)


def render_timeline(core: dict) -> str:
    c = core["claim"]
    lines = [f"时序链 · {c['claim_id']} 〔现势:{c['status']}〕"]
    if not core["transitions"]:
        lines.append("  (无状态变化记录)")
        return "\n".join(lines)
    for i, t in enumerate(core["transitions"]):
        cause = f"  {t['cause']}" if t["cause"] else "  状态刷新"
        mark = "  ← 推翻点" if i in core.get("overturn_indexes", []) else ""
        ev = f"  ({t['evidence_id']})" if t["evidence_id"] else ""
        lines.append(f"  {t['ts']}  {t['old_status']} → "
                     f"{t['new_status']}{cause}{ev}{mark}")
    return "\n".join(lines)


def render_topic(t: dict) -> str:
    e = t["entity"]
    names = e["names"]
    n_sig, n_ev = len(t["signals"]), len(t["events"])
    lines = [f"话题链 · {e['kind']}卡 {names.get('canonical', e['entity_id'])}"
             f" 〔现势视图 · {'/'.join(t['kb_versions'] or ['?'])}"
             f" · {n_ev} run {n_sig} 信号〕"]
    lines.append("  信号轴(到站序)")
    if t["signals"]:
        for s in t["signals"]:
            lines.append(f"   ├ {s['ref_id']}"
                         + (f"({s['note']})" if s["note"] else ""))
    else:
        lines.append("   ├ (无信号)")
    lines.append("  案卷互链")
    if t["events"]:
        for ev in t["events"]:
            lines.append(f"   ├ {ev['ref_id']}"
                         + (f"({ev['note']})" if ev["note"] else ""))
    else:
        lines.append("   ├ (未开卷)")
    lines.append("  结论分区(现算:正卡 矛盾>已证实;未决 按强度降序)")
    if t["zones"]["正卡"]:
        for m in t["zones"]["正卡"]:
            lines.append(f"   ├ 〔{_badge_from_line(m)}〕{_clip(m['text'], 40)}")
    else:
        lines.append("   ├ (空)")
    if t["zones"]["未决"]:
        for m in t["zones"]["未决"]:
            lines.append(f"   ├ 〔{_badge_from_line(m)}〕{_clip(m['text'], 40)}")
    else:
        lines.append("   ├ (未决区空)")
    lines.append("  未决问题(OQ)")
    if t["oq"]:
        for r in t["oq"]:
            tag = r.get("draft_id") or r["claim_id"]
            lines.append(f"   ├ {tag}〔{r['event_id']}〕"
                         f"{_clip(r.get('text') or '', 40)}")
    else:
        lines.append("   ├ (无)")
    if t["zone_drift"]:
        lines.append(f"  (卡面快照对照:zone 快照出自 "
                     f"{'/'.join(t['kb_versions'] or ['?'])}派生,"
                     f"{t['zone_drift']} 条分区已漂移——快照过期非矛盾)")
    return "\n".join(lines)


def _badge_from_line(m: dict) -> str:
    if m["score"] is None:
        return f"{m['status']}"
    return f"{m['status']}·{_tier_cn(m)}"


def _tier_cn(m: dict) -> str:
    return m.get("tier") or "—"


def render_provenance(p: dict) -> str:
    lines = [f"证据履历 · fp={p['fingerprint'][:8]}… "
             f"〔{p['source_type'] or '?'} · {p['sensitivity']} · "
             f"首曝 {p['observed_at'] or '?'}〕"]
    if p["sensitivity"] == "internal":
        lines.append("  〔internal 敏感件:存在性留痕,内容/链接不出境〕")
    elif p["url"]:
        lines.append(f"  url:{p['url']}")
    lines.append(f"  复曝史:{len(p['origin_refs'])} 处"
                 + ("(" + " / ".join(p["origin_refs"]) + ")" if p["origin_refs"] else ""))
    for rt, n in p["sibling_fingerprints"].items():
        lines.append(f"   └ lineage_root={rt}(同根成员指纹 {n} 行=折并前件数)")
    lines.append("  引用它的结论(现势一行式,点开=结论链)")
    if p["cited_by"]:
        for m in p["cited_by"]:
            lines.append(f"   ├ {m['claim_id']}〔{m['status']}〕· "
                         f"{m['ruling'] or '未裁'}")
    else:
        lines.append("   ├ (无结论引用)")
    return "\n".join(lines)


def render_hypothesis(h: dict) -> str:
    st = h["standing"] or {}
    pin = "人裁已钉" if st.get("adjudicated") else "人裁未钉"
    dom = " · dominant" if st.get("dominant") else ""
    score = st.get("score", 0)
    lines = [f"假设链 · {h['hypothesis_id']} {h['label']} "
             f"〔score={score:+d}{dom} · {pin}〕"]
    if h["description"]:
        lines.append(f"  {_clip(h['description'], 60)}")
    if st:
        lines.append(f"  记分:已证实 {st.get('verified', 0)} / "
                     f"矛盾 {st.get('contradicted', 0)} / "
                     f"待证实 {st.get('pending', 0)}"
                     f"(成员 {len(h['members'])} 条)")
    for m in h["members"]:
        op = h["ops"].get(m["claim_id"], "")
        lines.append(f"  ├ [{op}] {m['claim_id']}〔{m['status']}·"
                     f"{_tier_cn(m)}〕{_clip(m['text'], 40)}")
    return "\n".join(lines)


RENDERERS = {"conclusion": render_conclusion, "timeline": render_timeline,
             "topic": render_topic, "provenance": render_provenance,
             "hypothesis": render_hypothesis}


def render(kind: str, data: dict, fmt: str = "md") -> str:
    """双产出(§六-决策4):md=人看(敏感过滤内置);json=机器(K2 被调形态)。

    json 路径=ensure_ascii=False+sort_keys+缩进(确定性序列化);同一内核
    零分叉——dict 即投影产物,md 只是它的渲染。
    """
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
    fn = RENDERERS.get(kind)
    if fn is None:
        raise KbChainError(f"未知投影: {kind}")
    return fn(data)
