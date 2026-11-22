# -*- coding: utf-8 -*-
"""chain_web.v1 交互契约适配层(2026-11-09;方案=《实施方案/证据链交互化与
专业化-设计方案-20261109.md》§四,Phase 0)。

纯 Python 只读投影:消费 build_chain_graph 现成 graph(机器层第一公民,零
改动)+ ledger 证据明细/主语实体/状态史补查,输出版本化 chain_web.v1
JSON——前端交互式证据工作台的数据源。五泳道(方案 §3.2):source(来源
材料)→fact(抽取事实)→verification(交叉验证)→reasoning(推理关系)→
conclusion(结论假设);节点带 provenance_ids、边带 evidence_ids/claim_ids/
ruling_ids,双向可反查(结论→论点→事实→来源,来源→事实→验证→结论)。

铁律(照仓库纪律):
- 零 LLM 零网络;chain_graph.py/chain_svg.py/ledger.py 零改动;本模块对
  传入 graph 只读(零 mutate,负测试锁);
- 不写台账(七表 COUNT 前后相等测试锁);契约一次给全,前端不查主库;
- internal 敏感件内容不出境:只出定位符与存在性(evidence_id/num/谱系根/
 敏感性),title/url/摘录/时间/平台/附件全压空——照 kb_chain `_ev_line`
 「存在性留痕,内容不出境」+ graph 证据卡 summary 覆写同款口径;
- LLM 旁车永不冒充裁定:节点/边带 sidecar 诚实标注(manual|sidecar|
 fallback),漂移计数进 omissions 显式呈现,不静默裁剪;
- 确定性:全数组显式排序,零 now()/零随机,同输入两次序列化逐字节一致
 (payload_to_json 复用 chain_graph 同款 sort_keys 约定)。

三个核实偏差备考(照施工单实录先例,方案字面 → 实际落法):
1. ledger 无「按 event 列全量证据」现成方法 → 自带最小只读 SELECT(照
   chain_graph._CE_SQL 风格直查 led.conn);
2. graph 证据卡不带 url/发布时间/附件 → evidence[] 按上条 SELECT 补齐;
3. claims 表有 category 列(事实/实体/关系/时效/矛盾)→ 直接取台账现成
   值(方案 §四 category 字段=此列,非 interpretive 路由)。
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .chain_graph import (ChainGraphError, build_chain_graph, classify_claim,
                          load_present_rules)  # noqa: F401 (再导出)
from .conclusion_present import build_conclusion_views


class ChainWebError(Exception):
    """chain_web.v1 装配/校验失败(失败不伪装,照 ChainGraphError 先例)。"""


SCHEMA_VERSION = "chain_web.v1"
PROJECTION_VERSION = "chain-web.semantic.v1"


def _generator_revision() -> str:
    """可复现的生成器标识:两个纯投影模块字节指纹。

    工作树尚未 commit 时 git HEAD 不能代表实际生成代码；源码指纹
    能让用户精确区分「同一数据、不同投影实现」。
    """
    h = hashlib.sha256()
    for p in (Path(__file__), Path(__file__).with_name("chain_graph.py"),
              Path(__file__).with_name("conclusion_present.py")):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]

# 五泳道(方案 §3.2;顺序=推理方向;前端布点只消费 position_hint,不改拓扑)
LANES = (
    {"id": "source", "label": "来源材料",
     "description": "原始材料/来源页;同根转载折叠见同源数,十家转载=一根"},
    {"id": "fact", "label": "抽取事实",
     "description": "从材料抽取的事实命题;三态如实,已证实剥壳直陈,"
                    "待证实/矛盾不伪装"},
    {"id": "verification", "label": "交叉验证",
     "description": "每命题的验证单元:独立根数/同源折叠/支持反证计数,"
                    "只显示判决已承认的量,不创造三态"},
    {"id": "reasoning", "label": "推理关系",
     "description": "论点组/派生/融合/解读桥;LLM 旁车只标建议或已验票,"
                    "不冒充裁定"},
    {"id": "conclusion", "label": "结论假设",
     "description": "假设卡:资格四态+票型(凭什么支持/与谁比较/差什么),"
                    "关联全部成员命题"},
)
_LANE_ORDER = {lane["id"]: i for i, lane in enumerate(LANES)}

DETAIL_CLIP = 160          # details 摘录裁宽(全文留台账,契约不内嵌)
_INTERNAL_TITLE = "〔internal〕材料"

# 边极性呈现(supports;反证=反向唯一红源,时效刷新单独标不冒充正负)
_REL_POLARITY = {"支持": "正向", "矛盾": "反向", "时效刷新": "时效"}

# omissions 显式省略清单(键名与 graph.meta 一致=校验器可对账;0 也列出=
# 契约键集稳定;永远显示省略数量,不静默裁剪)
_OMISSION_NOTES = {
    "n_seed_hidden": "信封背景/检测器字段陈述不计材料层(卷面改造 §4-4)",
    "n_tried_hidden": "查过未见载明(无关票),留痕不逐条上图",
    "n_unadjudicated": "挂账未裁材料(宽进关联,S6 未裁定)",
    "n_unruled_evidence": "在案材料未入裁定(证据级去重)",
    "n_lineage_families": "同文谱系家族数(归并投影,正本台账全量在)",
    "n_lineage_merged": "同文谱系归并折叠件数",
    "n_fanout_merged_edges": "扇出汇流并边省线数",
    "n_fusion_cards": "命题级多源印证卡数(被融合材料人看层折叠)",
    "n_fusion_drifted": "融合旁车漂移弃用数(不落卡)",
    "n_fusion_blocked": "融合卡被机器闸拦下数(不落卡)",
    "n_bridges_blocked": "解读桥被机器闸拦下数(不落卡)",
    "n_inferences_blocked": "结构化推理桥未通过前提/目标/指纹门禁数",
    "n_semantic_routed": "Web 语义分流 claim 数(正本仍在 claims/details)",
    "n_routed_unbound": "解读型 claim 未绑定可审计推理桥而不上主图数",
    "n_insight_drifted": "解读旁车镜像漂移数(不分流)",
    "sidecar_drifted": "极性/通道旁车指纹漂移弃用数",
    "n_summary_drifted": "材料概括旁车漂移数(词典短语兜底)",
    "n_gen_drifted": "分组旁车漂移数(机械平铺兜底)",
    "channels_dropped": "通道建议白名单外丢弃数",
    "derives_skipped": "前提边父条不在装配范围跳过数",
    "n_no_edge_claims": "无边命题不进主图(含信封自证元陈述)",
    "n_zone": "待查证小区命题数(上图不上线)",
    "co_root_omitted": "共据边超上限省略(1=是)",
}

# 关系词白名单(校验器;前五=supports 派生,次四=其他机器边,后四=泳道从属线化)
_RELATION_WORDS = ("直接证明", "印证", "佐证", "反证", "时效更新",
                   "前提", "同源共据", "传导", "核验", "入组", "依据")


# ---------------------------------------------------------------- 读取

_EVIDENCE_SQL = (
    "SELECT evidence_id, title, content, source, url, platform,"
    " published_at, fetched_at, sensitive, assets"
    " FROM evidence WHERE event_id=? ORDER BY evidence_id")

_ENTITIES_SQL = (
    "SELECT ce.claim_id, ce.role, ent.canonical_name AS cname"
    " FROM claim_entities ce JOIN entities ent ON ent.entity_id=ce.entity_id"
    " WHERE ce.claim_id LIKE ? ORDER BY ce.claim_id, ent.entity_id")

_ROLE_RANK = {"头": 0, "普通": 1, "尾": 2}   # 与 chain_graph 主语排序同源


def _evidence_rows(led, event_id: str) -> dict[str, dict]:
    """全量证据明细(最小只读 SELECT;核实偏差 1——ledger 无按 event 列
    证据的现成方法,照 chain_graph._CE_SQL 风格直查,零写台账)。"""
    return {r["evidence_id"]: dict(r)
            for r in led.conn.execute(_EVIDENCE_SQL, (event_id,))}


def _entities_by_claim(led, event_id: str) -> dict[str, list[str]]:
    """claim→实体规范名列表(照 _claim_subject 同源 SQL;角色 头>普通>尾
    再名升序,去重;无实体=[]如实空,不造桶)。"""
    out: dict[str, list[str]] = {}
    for r in led.conn.execute(_ENTITIES_SQL, (event_id + ":%",)):
        name = (r["cname"] or "").strip()
        if name:
            out.setdefault(r["claim_id"], []).append(
                (_ROLE_RANK.get(r["role"], 1), name))
    return {cid: [n for _, n in sorted(pairs)] for cid, pairs in out.items()}


def _event_kind(led, event_id: str) -> str:
    """信封 kind(v2 统一提交外层 tender/rumor;v1 批量/旧库=空串如实)。"""
    try:
        raw = led.event_envelope_json(event_id)
        if not raw:
            return ""
        v = json.loads(raw)
        return str(v.get("kind") or "") if isinstance(v, dict) else ""
    except (ValueError, TypeError):
        return ""


# ---------------------------------------------------------------- 装配

def build_chain_web(mains, kb, event_id: str, *, ess_rules=None,
                    auth_roots=None, near_dup_dist=None, seeds_path=None,
                    config_dir: str | Path = "data/config",
                    as_of_round: int | None = None,
                    generated_at: str | None = None) -> dict:
    """同型签名主入口:透传 build_chain_graph → from_graph(测试/程序化
    调用;CLI 走 from_graph 复用已建 graph 不二次建图)。"""
    graph = build_chain_graph(
        mains, kb, event_id, ess_rules=ess_rules, auth_roots=auth_roots,
        near_dup_dist=near_dup_dist, seeds_path=seeds_path,
        config_dir=config_dir, as_of_round=as_of_round)
    return from_graph(graph, mains, kb, event_id, as_of_round=as_of_round,
                      config_dir=config_dir, generated_at=generated_at)


def from_graph(graph: dict, mains, kb, event_id: str, *,
               as_of_round: int | None = None,
               config_dir: str | Path = "data/config",
               generated_at: str | None = None) -> dict:
    """低层纯装配:graph 只读(零 mutate);mains[event_id] 补查证据明细/
    实体/状态史;kb 仅备签名对称(graph 已消费完旁车)。"""
    led = mains.get(event_id) if hasattr(mains, "get") else None
    if led is None:
        raise ChainWebError(f"主库未提供: {event_id}")

    cards = graph["nodes"]["claims"]
    claim_by = {c["claim_id"]: c for c in cards}
    drawn = graph["nodes"]["evidence"]
    sup_edges = graph["edges"]["supports"]
    rulings = graph.get("rulings") or []
    fusions = graph.get("fusions") or []
    bridges = graph.get("bridges") or []
    inferences = graph.get("inferences") or []
    argument = graph.get("argument")
    ov_hyps = (graph.get("overview") or {}).get("hypotheses") or []
    meta = dict(graph["meta"])
    rules = load_present_rules(config_dir)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    meta.update({"generated_at": generated_at,
                 "generator_revision": _generator_revision(),
                 "projection_version": PROJECTION_VERSION})

    ev_rows = _evidence_rows(led, event_id)
    ents = _entities_by_claim(led, event_id)

    # ---- Web 语义分流(只读投影,绝不反向影响 graph 的论证区资格/
    #      chosen/runner-up):有效 sidecar 分支优先，否则用窄前缀规则表
    #      保守归位。这正是「不改正本，只改五泳道投影」的边界。
    branch_alias = {"bridge": "interpretation", "clue": "gap_hint",
                    "meta": "meta_info", "interpretation": "interpretation",
                    "gap_hint": "gap_hint", "meta_info": "meta_info"}
    branch_by_claim: dict[str, str] = {}
    for c in cards:
        branch = branch_alias.get(c.get("interpretive") or "")
        if branch is None:
            branch = classify_claim(c.get("text") or "", rules)
        if branch:
            branch_by_claim[c["claim_id"]] = branch
    routed_bound = {cid for inf in inferences
                    for cid in inf.get("routed_claim_ids") or []}
    context_bound = {cid for inf in inferences
                     for cid in inf.get("context_claim_ids") or []}
    n_routed_unbound = sum(1 for cid, branch in branch_by_claim.items()
                           if branch == "interpretation" and cid not in routed_bound)
    meta["n_semantic_routed"] = len(branch_by_claim)
    meta["n_routed_unbound"] = n_routed_unbound

    # ---- 家族成员映射(代表→全家族含代表;照 chain_cards 证据并集口径)
    fam_members: dict[str, list[str]] = {c["claim_id"]: [c["claim_id"]]
                                         for c in cards}
    for c in cards:
        rep = c.get("family_rep") or ""
        if rep:
            fam_members.setdefault(rep, [rep]).append(c["claim_id"])

    sup_by_dst: dict[str, list[dict]] = {}
    sup_by_src: dict[str, list[dict]] = {}
    for e in sup_edges:
        sup_by_dst.setdefault(e["dst"], []).append(e)
        sup_by_src.setdefault(e["src"], []).append(e)
    rulings_by: dict[str, dict] = {r["claim_id"]: r for r in rulings}
    fusion_by_source: dict[str, dict] = {f["source_claim_id"]: f
                                         for f in fusions}
    fusion_all: set[str] = set()
    for f in fusions:
        fusion_all.update(f["all_members"])

    # ---- 直接证据状态:只认已被裁定承认且真正画入 supports 的边，
    #      provenance 只是「可反查到材料」，绝不再充当 supported 判据。
    evidence_metrics: dict[str, dict] = {}
    for c in cards:
        cid = c["claim_id"]
        accepted = sup_by_dst.get(cid, [])
        r = rulings_by.get(cid) or {}
        items = r.get("items") or []
        n_direct_support = sum(1 for e in accepted
                               if e.get("ruling") in ("支持", "时效刷新"))
        n_counter = sum(1 for e in accepted if e.get("ruling") == "矛盾")
        n_unadjudicated = len(r.get("unruled_nums") or [])
        n_irrelevant_tried = sum(1 for it in items
                                 if it.get("ruling") == "无关")
        if n_counter:
            state = "contested"
        elif n_direct_support:
            state = "supported"
        elif n_unadjudicated:
            state = "unadjudicated"
        else:
            state = "no_direct"
        evidence_metrics[cid] = {
            "evidence_state": state,
            "n_direct_support": n_direct_support,
            "n_counter": n_counter,
            "n_unadjudicated": n_unadjudicated,
            "n_irrelevant_tried": n_irrelevant_tried,
        }

    # ---- 命题证据闭包(fact 正向 provenance):本命题+家族成员的支撑
    #      ∪ 裁定挂账(含信封种子件——rulings 全量口径)∪ 被融合材料
    def _claim_prov(cid: str) -> list[str]:
        eids: set[str] = set()
        for target in fam_members.get(cid, [cid]):
            for e in sup_by_dst.get(target, []):
                eids.add(e["src"])
        r = rulings_by.get(cid)
        if r:
            eids.update(it["evidence_id"] for it in r["items"])
        f = fusion_by_source.get(cid)
        if f:
            eids.update(f["all_members"])
        return sorted(eids)

    prov_by_claim = {c["claim_id"]: _claim_prov(c["claim_id"]) for c in cards}
    internal_ids = {eid for eid, row in ev_rows.items() if row["sensitive"]}

    # ---- fanout_merges → Web edge_groups(原始 supports 仍全量保留)。
    #      graph 元数据可能是语义分流前算出，因此 Web 层只剪掉已
    #      routed claim，关系词/来源/根绝不重分组。
    edge_groups: list[dict] = []
    edge_group_ids_by_claim: dict[str, list[str]] = {}
    try:
        fanin_min = int(rules.get("fanin_merge_min", 0))
    except (TypeError, ValueError):
        fanin_min = 0
    for merge in graph.get("fanout_merges") or []:
        ev = merge.get("evidence_id") or ""
        rel = merge.get("relation") or ""
        projected = set(merge.get("members") or [])
        raw = [e for e in sup_edges
               if e.get("src") == ev and e.get("relation") == rel
               and ((claim_by.get(e.get("dst"), {}).get("family_rep")
                     or e.get("dst")) in projected)
               and e.get("dst") not in branch_by_claim]
        raw_members = sorted({e["dst"] for e in raw})
        members = sorted({claim_by.get(e["dst"], {}).get("family_rep")
                          or e["dst"] for e in raw})
        if len(raw) < max(2, fanin_min) or not members:
            continue
        digest = hashlib.sha256((ev + "\0" + rel + "\0"
                                 + str(merge.get("group") or "")).encode(
                                     "utf-8")).hexdigest()[:12]
        gid = f"eg:{digest}"
        node_id = f"fg:{digest}"
        status_dist = {s: sum(1 for cid in raw_members
                              if claim_by.get(cid, {}).get("status") == s)
                       for s in ("已证实", "待证实", "矛盾")}
        state_dist = {s: sum(1 for cid in raw_members
                             if evidence_metrics.get(cid, {}).get(
                                 "evidence_state") == s)
                      for s in ("supported", "contested", "unadjudicated",
                                "no_direct")}
        group = {
            "id": gid, "node_id": node_id, "source": f"ev:{ev}",
            "evidence_id": ev, "relation_type": rel,
            "group": str(merge.get("group") or "事实簇"),
            "members": members,
            "raw_member_claim_ids": raw_members,
            "raw_edge_ids": [f"sup:{e['src']}|{e['dst']}" for e in raw],
            "n": len(raw), "status_dist": status_dist,
            "evidence_state_dist": state_dist,
        }
        edge_groups.append(group)
        for cid in members:
            edge_group_ids_by_claim.setdefault(cid, []).append(gid)
    edge_groups.sort(key=lambda x: x["id"])

    # ---- evidence[] 目录 = 上图材料 ∪ 裁定挂账件(含信封种子件;未裁/
    #      无关不进目录,只留计数与短号定位——「未裁不冒充方向」)
    evidence_ids: set[str] = {e["evidence_id"] for e in drawn}
    for r in rulings:
        for it in r["items"]:
            evidence_ids.add(it["evidence_id"])

    nodes: list[dict] = []
    edges: list[dict] = []
    details: dict[str, dict] = {}
    collapsed_n = 0

    def _node(nid: str, ntype: str, label: str, status: str, hint: str,
              *, collapsed: bool = False, prov: list[str] | None = None,
              detail_ref: str = "", sub_type: str = "", **extra) -> dict:
        nonlocal collapsed_n
        if collapsed:
            collapsed_n += 1
        n = {"id": nid, "type": ntype, "label": label, "status": status,
             "position_hint": hint, "collapsed": collapsed,
             "provenance_ids": prov or [], "detail_ref": detail_ref,
             "sub_type": sub_type}
        n.update(extra)
        nodes.append(n)
        return n

    # ---- source 泳道(上图材料;label=证据卡 summary,敏感件已被
    #      chain_graph 覆写为〔internal〕,原样带出=单口径)
    for i, e in enumerate(drawn):
        eid = e["evidence_id"]
        targets = {x["dst"] for x in sup_by_src.get(eid, [])}
        # 折叠口径:全部支撑目标都是家族非代表(随代表折叠),或本件已被
        # 融合卡收编(被融合材料在人看主图替代式折叠——chain_cards 同口径)
        folded = ((bool(targets)
                   and all(claim_by.get(t, {}).get("family_rep")
                           for t in targets))
                  or eid in fusion_all)
        _node(f"ev:{eid}", "source", e["summary"], "", f"source:{i:03d}",
              collapsed=folded,
              detail_ref=("" if eid in internal_ids
                          else f"evidence:{eid}"),   # internal 无内容可看
              num=e["num"], lineage_root=e["root"])

    # ---- fact 泳道:只放客观命题；interpretation/gap/meta 严禁重复
    #      生成事实卡。高扇出成员和 family 非代表仍保留节点，但
    #      缺省 collapsed，前端「展开明细/显示折叠件」才恢复。
    row_ids = [r["claim_id"] for r in (graph.get("rows") or [])
               if r["claim_id"] not in branch_by_claim]
    zone_ids = [c["claim_id"] for c in cards if c.get("in_zone")
                and c["claim_id"] not in branch_by_claim]
    rest_ids = sorted(c["claim_id"] for c in cards
                      if not c["in_main"] and not c.get("in_zone")
                      and c["claim_id"] not in branch_by_claim)
    fact_seq = list(dict.fromkeys(row_ids + zone_ids + rest_ids))
    for i, cid in enumerate(fact_seq):
        c = claim_by[cid]
        # 折叠口径(第八波人看语义):家族非代表随代表折叠;小区命题
        # 上图不上线缺省折叠;融合目标是命题本身不折叠(融合折叠的是
        # 证据成员,见 source 泳道)
        group_ids = sorted(edge_group_ids_by_claim.get(cid, []))
        folded = bool(c.get("family_rep")) or bool(group_ids)
        prov = prov_by_claim[cid]
        family = fam_members.get(cid, [cid])
        family_status_dist = {s: sum(1 for mid in family
                                     if claim_by.get(mid, {}).get("status") == s)
                              for s in ("已证实", "待证实", "矛盾")}
        family_state_dist = {s: sum(1 for mid in family
                                    if evidence_metrics.get(mid, {}).get(
                                        "evidence_state") == s)
                             for s in ("supported", "contested",
                                       "unadjudicated", "no_direct")}
        _node(f"cl:{cid}", "fact", c["label"], c["status"], f"fact:{i:03d}",
              collapsed=folded, prov=prov,
              detail_ref=f"claim:{cid}",
              sub_type=("zone" if c.get("in_zone")
                        else "family" if c.get("family_rep")
                        else "fact"),
              num=c.get("draft_id", ""), edge_group_ids=group_ids,
              family_members=family,
              family_status_dist=family_status_dist,
              family_evidence_state_dist=family_state_dist,
              **evidence_metrics[cid],
              **({"no_direct_evidence": True}
                 if evidence_metrics[cid]["evidence_state"] in (
                     "unadjudicated", "no_direct")
                 else {}))

    # 高扇出事实簇是 fact 泳道的虚拟投影卡；成员/原边全在
    # claims/raw_edges/details 可反查，卡本身不是新 claim。
    fact_base = len(fact_seq)
    for i, group in enumerate(edge_groups):
        details[f"edge_group:{group['id']}"] = dict(group)
        _node(group["node_id"], "fact", group["group"], "",
              f"fact:{fact_base + i:03d}",
              prov=[group["evidence_id"]],
              detail_ref=f"edge_group:{group['id']}",
              sub_type="fact_cluster", edge_group_id=group["id"],
              member_claim_ids=list(group["members"]),
              aggregate_n=group["n"], relation_type=group["relation_type"],
              status_dist=dict(group["status_dist"]),
              evidence_state_dist=dict(group["evidence_state_dist"]))

    # ---- verification 泳道:普通 claim 逐条核验；gap_hint 独立缺口卡;
    #      meta_info 同类合并为「信封字段覆盖缺口」，不再冒充事实。
    for i, r in enumerate(rulings):
        cid = r["claim_id"]
        if cid in branch_by_claim:
            continue
        vprov = sorted({it["evidence_id"] for it in r["items"]})
        group_ids = sorted(edge_group_ids_by_claim.get(cid, []))
        _node(f"vf:{cid}", "verification", r["label"], r["status"],
              f"verification:{i:03d}", collapsed=bool(group_ids), prov=vprov,
              detail_ref=f"verification:{cid}",
              edge_group_ids=group_ids, **evidence_metrics[cid],
              **({"no_direct_evidence": True}
                 if evidence_metrics[cid]["evidence_state"] in (
                     "unadjudicated", "no_direct")
                 else {}))

    verification_gap_node_by_claim: dict[str, str] = {}
    vg_index = len(rulings)
    for cid in sorted(k for k, branch in branch_by_claim.items()
                      if branch == "gap_hint"):
        c = claim_by[cid]
        nid = f"vg:{cid}"
        verification_gap_node_by_claim[cid] = nid
        details[f"routed_gap:{cid}"] = {
            "kind": "gap_hint", "claim_ids": [cid],
            "items": [{"claim_id": cid, "text": c["text"],
                       "status": c["status"]}],
        }
        _node(nid, "verification", "基础核验缺口", "",
              f"verification:{vg_index:03d}", prov=prov_by_claim[cid],
              detail_ref=f"routed_gap:{cid}", sub_type="gap_hint",
              member_claim_ids=[cid], evidence_state="no_direct",
              n_direct_support=0, n_counter=0, n_unadjudicated=0,
              n_irrelevant_tried=0, no_direct_evidence=True,
              intentional_orphan=cid not in context_bound)
        vg_index += 1

    meta_claim_ids = sorted(k for k, branch in branch_by_claim.items()
                            if branch == "meta_info")
    if meta_claim_ids:
        nid = f"vg:meta:{event_id}"
        for cid in meta_claim_ids:
            verification_gap_node_by_claim[cid] = nid
        details[f"routed_meta:{event_id}"] = {
            "kind": "meta_info", "claim_ids": meta_claim_ids,
            "items": [{"claim_id": cid, "text": claim_by[cid]["text"],
                       "status": claim_by[cid]["status"]}
                      for cid in meta_claim_ids],
        }
        prov = sorted({eid for cid in meta_claim_ids
                       for eid in prov_by_claim.get(cid, [])})
        _node(nid, "verification", "信封字段覆盖缺口", "",
              f"verification:{vg_index:03d}", prov=prov,
              detail_ref=f"routed_meta:{event_id}", sub_type="meta_info",
              member_claim_ids=meta_claim_ids, evidence_state="no_direct",
              n_direct_support=0, n_counter=0,
              n_unadjudicated=sum(evidence_metrics[cid]["n_unadjudicated"]
                                  for cid in meta_claim_ids),
              n_irrelevant_tried=sum(
                  evidence_metrics[cid]["n_irrelevant_tried"]
                  for cid in meta_claim_ids), no_direct_evidence=True,
              intentional_orphan=not bool(set(meta_claim_ids) & context_bound))

    # ---- reasoning 泳道:论点组 → 融合卡 → legacy 解读桥 →
    #      结构化推理桥。规则命中的 interpretation claim 不再单独复制卡;
    #      已绑定者进推理桥 details，未绑定者进 omissions。
    j = 0
    reasoning_group_node_by_name: dict[str, str] = {}
    if argument:
        gp = {g["group"]: g for g in argument.get("group_polarity") or []}
        for gi, g in enumerate(argument["groups"]):
            visible_members = [cid for cid in g["members"]
                               if cid not in branch_by_claim]
            if not visible_members:
                continue
            rgid = f"{argument['hypothesis_id']}#{gi:02d}"
            reasoning_group_node_by_name[g["name"]] = f"rg:{rgid}"
            prov: set[str] = set()
            for cid in visible_members:
                prov.update(prov_by_claim.get(cid, []))
            details[f"group:{rgid}"] = {
                "name": g["name"], "members": list(g["members"]),
                "visible_members": visible_members,
                "polarity": gp.get(g["name"],
                                   {"n_pos": 0, "n_rev": 0, "n_none": 0})}
            _node(f"rg:{rgid}", "reasoning", g["name"], "",
                  f"reasoning:{j:03d}", prov=sorted(prov),
                  detail_ref=f"group:{rgid}", sub_type="论点组",
                  members=visible_members)
            j += 1
    for f in fusions:
        sid = f["source_claim_id"]
        _node(f"fu:{sid}", "reasoning", f["text"], "",
              f"reasoning:{j:03d}", prov=sorted(set(f["all_members"])),
              detail_ref=f"fusion:{sid}", sub_type="融合",
              sidecar={"kind": "llm_sidecar", "verified": True})
        j += 1
    for b in bridges:
        prov: set[str] = set()
        for cid in b["basis"]:
            prov.update(prov_by_claim.get(cid, []))
        _node(f"br:{b['insight_id']}", "reasoning", b["text"], "",
              f"reasoning:{j:03d}", prov=sorted(prov),
              detail_ref=f"bridge:{b['insight_id']}", sub_type="解读桥",
              sidecar={"kind": "structure", "n_verified": b["n_verified"]})
        j += 1
    for inf in inferences:
        iid = inf["inference_id"]
        prov = sorted({eid for cid in inf["basis_claim_ids"]
                       for eid in prov_by_claim.get(cid, [])})
        details[f"inference:{iid}"] = dict(inf)
        _node(f"ir:{iid}", "reasoning", inf["label"], "",
              f"reasoning:{j:03d}", prov=prov,
              detail_ref=f"inference:{iid}", sub_type="结构化推理",
              inference_text=inf["inference_text"],
              basis_claim_ids=list(inf["basis_claim_ids"]),
              rule=inf["rule"],
              target_hypothesis_ids=list(inf["target_hypothesis_ids"]),
              premise_status_summary=dict(inf["premise_status_summary"]),
              condition_or_gap=inf["condition_or_gap"],
              fingerprint=inf["fingerprint"],
              sidecar_provenance=inf["sidecar_provenance"],
              fingerprint_verified=bool(inf["fingerprint_verified"]))
        j += 1

    # ---- conclusion 泳道(总览四态;成员=conveys 反查)
    conv_by_hyp: dict[str, list[str]] = {}
    for e in graph["edges"]["conveys"]:
        conv_by_hyp.setdefault(e["dst"], []).append(e["src"])
    for i, h in enumerate(ov_hyps):
        hid = h["hypothesis_id"]
        prov: set[str] = set()
        for cid in conv_by_hyp.get(hid, []):
            prov.update(prov_by_claim.get(cid, []))
        _node(f"hy:{hid}", "conclusion", h["label"], h["state"],
              f"conclusion:{i:03d}", prov=sorted(prov),
              detail_ref=f"hypothesis:{hid}",
              **({"no_direct_evidence": True} if not prov else {}))

    # ---- edges:raw_edges 是机器对账全量;edges 是可见/可展开投影。
    #      routed claim 的原边仍在 raw_edges+verification details，但不再造
    #      一张伪事实卡来承接端点。
    raw_edges: list[dict] = []
    raw_group_by_edge = {eid: g["id"] for g in edge_groups
                         for eid in g["raw_edge_ids"]}
    for e in sup_edges:
        raw = {
            "id": f"sup:{e['src']}|{e['dst']}", "source": f"ev:{e['src']}",
            "target": f"cl:{e['dst']}", "relation_type": e["relation"],
            "status": e["ruling"], "claim_ids": [e["dst"]],
            "evidence_ids": [e["src"]],
            "ruling_ids": [f"ruling:{e['dst']}|{e['src']}"],
            "polarity": _REL_POLARITY.get(e["ruling"]),
            "root": e["root"], "root_no": e["root_no"],
            "authoritative": e["authoritative"], "flip": e["flip"]}
        if raw["id"] in raw_group_by_edge:
            raw["edge_group_id"] = raw_group_by_edge[raw["id"]]
        raw_edges.append(raw)
        if e["dst"] not in branch_by_claim:
            edges.append(dict(raw))
    # 汇流聚合边:原边不删，前端依 edge_group_id 在聚合/展开两态切换。
    for group in edge_groups:
        ruling_ids = [f"ruling:{rid.split('|', 1)[1]}|{group['evidence_id']}"
                      for rid in group["raw_edge_ids"]]
        edges.append({
            "id": f"agg:{group['id']}", "source": group["source"],
            "target": group["node_id"],
            "relation_type": group["relation_type"], "status": None,
            "claim_ids": list(group["raw_member_claim_ids"]),
            "evidence_ids": [group["evidence_id"]],
            "ruling_ids": ruling_ids, "polarity": "正向",
            "edge_group_id": group["id"], "aggregate": True,
            "aggregate_n": group["n"],
            "relation_label": f"{group['relation_type']} ×{group['n']}",
            "raw_edge_ids": list(group["raw_edge_ids"]),
        })
    # ---- 泳道从属边(2026-11-10 交互反馈批:verification/reasoning 此前
    #      0 边悬空;claim_id/members/basis 均为出件已有字段,线化=可视化
    #      已有从属,不创造新判定;空 ruling 非支持族=校验器放行)
    for r in rulings:
        cid = r["claim_id"]
        if cid in branch_by_claim:
            continue
        edges.append({
            "id": f"vfc:{cid}", "source": f"cl:{cid}",
            "target": f"vf:{cid}", "relation_type": "核验",
            "status": None, "claim_ids": [cid],
            "evidence_ids": [], "ruling_ids": [], "polarity": None})
    if argument:
        for gi, g in enumerate(argument["groups"]):
            rgid = f"{argument['hypothesis_id']}#{gi:02d}"
            rg_node = reasoning_group_node_by_name.get(g["name"])
            if not rg_node:
                continue
            visible_members = [cid for cid in g["members"]
                               if cid not in branch_by_claim]
            for cid in visible_members:
                edges.append({
                    "id": f"rgb:{rgid}|{cid}", "source": f"cl:{cid}",
                    "target": rg_node, "relation_type": "入组",
                    "status": None, "claim_ids": [cid],
                    "evidence_ids": [], "ruling_ids": [], "polarity": None})
            # 论点组不再是只进不出的装饰卡:明确传导到当前论证结论。
            gp = next((x for x in argument.get("group_polarity") or []
                       if x["group"] == g["name"]), None) or {}
            polarity = ("反向" if gp.get("n_rev", 0) else
                        "正向" if gp.get("n_pos", 0) else None)
            edges.append({
                "id": f"rgc:{rgid}", "source": rg_node,
                "target": f"hy:{argument['hypothesis_id']}",
                "relation_type": "传导", "status": None,
                "claim_ids": visible_members, "evidence_ids": [],
                "ruling_ids": [], "polarity": polarity,
                "rationale": "论点组成员向当前论证结论的聚合传导"})
    for b in bridges:
        for cid in b["basis"]:
            if cid in branch_by_claim:
                continue
            edges.append({
                "id": f"brb:{b['insight_id']}|{cid}", "source": f"cl:{cid}",
                "target": f"br:{b['insight_id']}", "relation_type": "依据",
                "status": None, "claim_ids": [cid],
                "evidence_ids": [], "ruling_ids": [], "polarity": None})
        if argument:
            edges.append({
                "id": f"brc:{b['insight_id']}",
                "source": f"br:{b['insight_id']}",
                "target": f"hy:{argument['hypothesis_id']}",
                "relation_type": "传导", "status": None,
                "claim_ids": [b["conclusion"]], "evidence_ids": [],
                "ruling_ids": [], "polarity": None,
                "rationale": b.get("rationale") or "解读桥条件传导"})
    # 结构化推理:前提/缺口→推理桥→假设。家族 basis 连到代表卡，
    # 但 claim_ids 仍保留原始前提，详情可完整反查。
    edge_group_by_id = {g["id"]: g for g in edge_groups}
    for inf in inferences:
        iid = inf["inference_id"]
        target_node = f"ir:{iid}"
        projected_basis = {claim_by.get(cid, {}).get("family_rep") or cid: cid
                           for cid in inf["basis_claim_ids"]}
        grouped_basis: set[str] = set()
        for gid, group in edge_group_by_id.items():
            overlap = sorted(set(group["members"]) & set(projected_basis))
            if not overlap:
                continue
            grouped_basis.update(overlap)
            raw_claims = sorted(projected_basis[cid] for cid in overlap)
            edges.append({
                "id": f"egb:{gid}|{iid}", "source": group["node_id"],
                "target": target_node, "relation_type": "依据",
                "status": None, "claim_ids": raw_claims,
                "evidence_ids": [group["evidence_id"]],
                "ruling_ids": [], "polarity": None,
                "edge_group_id": gid, "aggregate": True,
                "aggregate_n": len(raw_claims),
                "relation_label": f"依据 ×{len(raw_claims)}",
            })
        for projected, raw_cid in sorted(projected_basis.items()):
            edges.append({
                "id": f"irb:{iid}|{raw_cid}",
                "source": f"cl:{projected}", "target": target_node,
                "relation_type": "依据", "status": None,
                "claim_ids": [raw_cid],
                "evidence_ids": prov_by_claim.get(raw_cid, []),
                "ruling_ids": [], "polarity": None,
                **({"edge_group_id": next(
                    (gid for gid, group in edge_group_by_id.items()
                     if projected in group["members"]), "")}
                   if projected in grouped_basis else {}),
            })
        context_nodes: dict[str, list[str]] = {}
        for cid in inf.get("context_claim_ids") or []:
            nid = verification_gap_node_by_claim.get(cid)
            if nid:
                context_nodes.setdefault(nid, []).append(cid)
        for nid, cids in sorted(context_nodes.items()):
            edges.append({
                "id": f"irc:{iid}|{nid}", "source": nid,
                "target": target_node, "relation_type": "依据",
                "status": None, "claim_ids": sorted(cids),
                "evidence_ids": [], "ruling_ids": [], "polarity": None,
            })
        for target in inf["targets"]:
            hid = target["hypothesis_id"]
            edges.append({
                "id": f"irt:{iid}|{hid}", "source": target_node,
                "target": f"hy:{hid}", "relation_type": "传导",
                "status": None, "claim_ids": list(inf["basis_claim_ids"]),
                "evidence_ids": [], "ruling_ids": [],
                "polarity": target.get("polarity"),
                "rationale": target.get("rationale") or "",
            })
    for d in graph["edges"]["derives"]:
        if d["src"] in branch_by_claim or d["dst"] in branch_by_claim:
            continue
        edges.append({
            "id": f"der:{d['src']}|{d['dst']}", "source": f"cl:{d['src']}",
            "target": f"cl:{d['dst']}", "relation_type": "前提",
            "status": None, "claim_ids": [d["src"], d["dst"]],
            "evidence_ids": prov_by_claim.get(d["src"], []),
            "ruling_ids": [], "polarity": None})
    for cr in graph["edges"]["co_roots"]:
        if cr["a"] in branch_by_claim or cr["b"] in branch_by_claim:
            continue
        shared = sorted({e["src"] for e in sup_edges
                         if e["root"] in set(cr["roots"])
                         and e["dst"] in (cr["a"], cr["b"])})
        edges.append({
            "id": f"cor:{cr['a']}|{cr['b']}", "source": f"cl:{cr['a']}",
            "target": f"cl:{cr['b']}", "relation_type": "同源共据",
            "status": None, "claim_ids": [cr["a"], cr["b"]],
            "evidence_ids": shared, "ruling_ids": [], "polarity": None})
    for cv in graph["edges"]["conveys"]:
        if cv["src"] in branch_by_claim:
            continue
        edges.append({
            "id": f"con:{cv['src']}|{cv['dst']}", "source": f"cl:{cv['src']}",
            "target": f"hy:{cv['dst']}", "relation_type": "传导",
            "status": None, "claim_ids": [cv["src"]],
            "evidence_ids": prov_by_claim.get(cv["src"], []),
            "ruling_ids": [], "polarity": cv["polarity"],
            "rationale": cv["rationale"], "manual": cv["manual"]})
    for f in fusions:
        for eid in sorted(set(f["all_members"])):
            edges.append({
                "id": f"fub:{f['source_claim_id']}|{eid}",
                "source": f"ev:{eid}",
                "target": f"fu:{f['source_claim_id']}",
                "relation_type": "依据", "status": None,
                "claim_ids": [f["source_claim_id"]],
                "evidence_ids": [eid], "ruling_ids": [],
                "polarity": None,
            })
        edges.append({
            "id": f"fue:{f['source_claim_id']}",
            "source": f"fu:{f['source_claim_id']}",
            "target": f"cl:{f['claim_id']}", "relation_type": f["relation"],
            "status": None, "claim_ids": [f["claim_id"]],
            "evidence_ids": sorted(set(f["all_members"])),
            "ruling_ids": [], "polarity": "正向",
            "sidecar": {"kind": "llm_sidecar", "verified": True}})
    edges.sort(key=lambda e: (e["source"], e["target"], e["relation_type"],
                              e["id"]))

    # ---- evidence[] 目录(敏感压净=纪律;public 才落 details 内容键)
    drawn_by = {x["evidence_id"]: x for x in drawn}
    evidence_out = []
    for eid in sorted(evidence_ids):
        row = ev_rows.get(eid)
        if row is None:   # 防御:挂账件缺台账行(不应发生),如实降级
            evidence_out.append({"evidence_id": eid,
                                 "num": eid.rsplit(":", 1)[-1],
                                 "title": "", "excerpt": None,
                                 "source_ref": None, "source_url": None,
                                 "platform": None, "published_at": None,
                                 "collected_at": None, "lineage_root": "",
                                 "sensitivity": "public",
                                 "authoritative": False, "party": False,
                                 "same_root_count": 1,
                                 "attachment_ref": None})
            continue
        internal = bool(row["sensitive"])
        card = drawn_by.get(eid)
        assets = json.loads(row["assets"] or "[]") if row["assets"] else []
        evidence_out.append({
            "evidence_id": eid, "num": eid.rsplit(":", 1)[-1],
            "title": _INTERNAL_TITLE if internal else (row["title"] or ""),
            "excerpt": None if internal else (row["content"]
                                              or "")[:DETAIL_CLIP] or None,
            "source_ref": None if internal else (row["source"] or "") or None,
            "source_url": None if internal else (row["url"] or "") or None,
            "platform": None if internal else (row["platform"] or "") or None,
            "published_at": None if internal else (row["published_at"] or "") or None,
            "collected_at": None if internal else (row["fetched_at"] or "") or None,
            "lineage_root": (card or {}).get("root", ""),
            "sensitivity": "internal" if internal else "public",
            "authoritative": bool((card or {}).get("authoritative")),
            "party": bool((card or {}).get("party")),
            "same_root_count": (card or {}).get("same_root_count", 1),
            "attachment_ref": (None if internal or not assets
                               else f"assets:{eid}")})
        if not internal:
            details[f"evidence:{eid}"] = {
                "title": row["title"] or "",
                "excerpt": (row["content"] or "")[:DETAIL_CLIP] or None,
                "source_url": row["url"] or "",
                "platform": row["platform"] or "",
                "published_at": row["published_at"] or "",
                "collected_at": row["fetched_at"] or ""}
            if assets:
                details[f"assets:{eid}"] = assets

    # ---- claims[](台账全量口径;category=claims 表现成列,核实偏差 3)
    try:
        cat_map = {x["claim_id"]: x["category"] or ""
                   for x in led.conn.execute(
                       "SELECT claim_id, category FROM claims")}
    except Exception:   # 旧库缺列:全空如实降级(失败不伪装)
        cat_map = {}
    claims_out = []
    for c in sorted(cards, key=lambda x: x["claim_id"]):
        cid = c["claim_id"]
        r = rulings_by.get(cid)
        counts: dict[str, int] = {"支持": 0, "矛盾": 0, "时效刷新": 0}
        n_dup = 0
        if r:
            for it in r["items"]:
                if it["ruling"] in counts:
                    counts[it["ruling"]] += 1
                    if it.get("dup_root"):
                        n_dup += 1
        claims_out.append({
            "claim_id": cid, "draft_id": c["draft_id"],
            "gist": c["gist"], "text": c["text"], "label": c["label"],
            "status": c["status"], "category": _category_of(led, cid),
            "entities": ents.get(cid, []),
            "history": c["history"],
            "ruling_summary": {
                "n_items": r["n_items"] if r else 0,
                "n_roots": r["n_roots"] if r else 0,
                "n_dup_root": n_dup,
                "support": counts["支持"], "contra": counts["矛盾"],
                "temporal": counts["时效刷新"],
                "unruled": len(r["unruled_nums"]) if r else 0},
            "priority": c["priority"], "anchor_name": c["anchor_name"],
            "corpus_bound": c["corpus_bound"],
            "interpretive": branch_by_claim.get(cid),
            "reborn_from": c["reborn_from"], "gap": c["gap"],
            "in_main": c["in_main"], "in_zone": bool(c.get("in_zone")),
            "family_rep": c.get("family_rep", ""),
            "family_members": list(fam_members.get(cid, [cid])),
            **evidence_metrics[cid],
            "evidence_morph": c.get("evidence_morph"),
            "flipback_note": c.get("flipback_note")})
        family = fam_members.get(cid, [cid])
        details[f"claim:{cid}"] = {
            "text": c["text"], "gist": c["gist"],
            "evidence_state": evidence_metrics[cid]["evidence_state"],
            "evidence_state_reason": _evidence_state_reason(
                evidence_metrics[cid]),
            "family_members": [{
                "claim_id": mid, "text": claim_by[mid]["text"],
                "status": claim_by[mid]["status"],
                "evidence_state": evidence_metrics[mid]["evidence_state"],
                "provenance_ids": prov_by_claim.get(mid, []),
            } for mid in family],
        }

    # ---- verification[](逐命题验证单元;rep_evidence=每根最小 id 代表)
    verifications = []
    for r in rulings:
        cid = r["claim_id"]
        c = claim_by.get(cid, {})
        reps: dict[str, str] = {}
        for it in r["items"]:
            root = it.get("root") or ""
            if root and (root not in reps or it["evidence_id"] < reps[root]):
                reps[root] = it["evidence_id"]
        gap = c.get("gap") or {}
        if c.get("status") == "待证实" and gap:
            note = gap.get("label", "")
            if gap.get("next"):
                note += f"·尚差{gap['next']}"
            if gap.get("channel"):
                note += f"·建议通道{gap['channel']}"
        elif c.get("status") == "矛盾":
            note = c.get("flipback_note") or ""
        else:
            note = ""
        items = []
        for it in r["items"]:
            internal = it["evidence_id"] in internal_ids
            legacy = "quote" not in it and "reason" not in it
            items.append({
                "evidence_id": it["evidence_id"], "num": it["num"],
                "title": _INTERNAL_TITLE if internal else it["title"],
                "src": "" if internal else it["src"],
                "ruling": it["ruling"], "root": it["root"],
                "dup_root": bool(it.get("dup_root")),
                "quote": None if internal else it.get("quote"),
                "reason": None if internal else it.get("reason"),
                "masked": internal,
                "legacy": legacy})
            details[f"ruling:{cid}|{it['evidence_id']}"] = {
                "claim_id": cid, "evidence_id": it["evidence_id"],
                "num": it["num"], "ruling": it["ruling"], "root": it["root"],
                "dup_root": bool(it.get("dup_root")),
                "title": _INTERNAL_TITLE if internal else it["title"],
                "src": "" if internal else it["src"],
                "quote": None if internal else it.get("quote"),
                "reason": None if internal else it.get("reason"),
                "masked": internal, "legacy": legacy}
        verifications.append({
            "claim_id": cid, "status": r["status"],
            **evidence_metrics[cid],
            "n_independent_roots": r["n_roots"], "n_items": r["n_items"],
            "n_dup_root": sum(1 for x in items if x["dup_root"]),
            "n_support": counts_of(items, "支持"),
            "n_contra": counts_of(items, "矛盾"),
            "n_temporal": counts_of(items, "时效刷新"),
            "rep_evidence": [reps[k] for k in sorted(reps)],
            "unruled_nums": list(r["unruled_nums"]),
            "morph": c.get("evidence_morph"), "threshold_note": note})
        details[f"verification:{cid}"] = {
            "threshold_note": note, "items": items,
            "evidence_state": evidence_metrics[cid]["evidence_state"],
            "evidence_state_reason": _evidence_state_reason(
                evidence_metrics[cid])}

    # ---- conclusions[](报告/Web 同源的结构化结论视图)
    # 假设说明与全量成员直接读台账；chosen/runner-up 仍只消费
    # graph overview/argument，本层不改记分与裁定。
    hyp_rows: dict[str, dict] = {}
    for row in led.conn.execute(
            "SELECT h.hypothesis_id,h.label,h.description,hc.claim_id "
            "FROM hypotheses h LEFT JOIN hypothesis_claims hc "
            "ON hc.hypothesis_id=h.hypothesis_id WHERE h.event_id=? "
            "ORDER BY h.registered_round,h.hypothesis_id,hc.claim_id",
            (event_id,)):
        rec = hyp_rows.setdefault(row["hypothesis_id"], {
            "hypothesis_id": row["hypothesis_id"], "label": row["label"],
            "description": row["description"] or "", "member_ids": []})
        if row["claim_id"]:
            rec["member_ids"].append(row["claim_id"])
    ov_by_hid = {h["hypothesis_id"]: h for h in ov_hyps}
    arg_hid = argument.get("hypothesis_id") if argument else ""
    hyp_inputs = []
    for h in ov_hyps:
        hid = h["hypothesis_id"]
        rec = dict(hyp_rows.get(hid) or {
            "hypothesis_id": hid, "label": h.get("label", hid),
            "description": "", "member_ids": h.get("member_ids") or []})
        rec.update({"state": h.get("state", ""),
                    "score": h.get("score", 0),
                    "chosen": bool(argument and hid == arg_hid)})
        hyp_inputs.append(rec)
    snapshot_version = (f"{meta.get('as_of') or 'unknown'}|"
                        f"round:{meta.get('as_of_round') if meta.get('as_of_round') is not None else 'latest'}")
    excluded_from_conclusion = {
        cid for cid, branch in branch_by_claim.items()
        if branch in ("interpretation", "gap_hint", "meta_info")}
    conclusion_views = build_conclusion_views(
        graph, claims_out, hyp_inputs,
        excluded_claim_ids=excluded_from_conclusion,
        source_section_id=f"{event_id}:S3",
        snapshot_version=snapshot_version)
    view_by_hid = {v["hypothesis_id"]: v for v in conclusion_views}

    conclusions = []
    runner_ids = [h["hypothesis_id"] for h in ov_hyps
                  if h["state"] == "runner_up"]
    for h in ov_hyps:
        hid = h["hypothesis_id"]
        entry = dict(h)
        entry["chosen"] = bool(argument
                               and hid == argument["hypothesis_id"])
        member_ids = list((hyp_rows.get(hid) or {}).get("member_ids") or
                          entry.get("member_ids") or [])
        member_status = [claim_by[cid]["status"] for cid in member_ids
                         if cid in claim_by]
        entry.setdefault("member_ids", member_ids)
        entry.setdefault("n_members", len(member_ids))
        entry.setdefault("n_verified", member_status.count("已证实"))
        entry.setdefault("ticket", {
            "support": member_status.count("已证实"),
            "counter": member_status.count("矛盾"),
            "checking": member_status.count("待证实")})
        entry.setdefault("verdict_line",
                         f"已证实 {entry['n_verified']}/{entry['n_members']} 项")
        entry.setdefault("group_polarity", [])
        entry.setdefault("grouping_source", "ledger")
        entry.setdefault("runners_up", runner_ids if entry["chosen"] else [])
        if entry["chosen"]:
            entry["verdict_line"] = argument["verdict_line"]
            entry["ticket"] = argument["ticket"]
            entry["group_polarity"] = argument["group_polarity"]
            entry["grouping_source"] = argument["grouping_source"]
            entry["member_ids"] = list(argument["member_ids"])
            entry["runners_up"] = runner_ids
            if argument.get("narrative"):
                entry["narrative"] = argument["narrative"]
        present = view_by_hid.get(hid)
        if present:
            entry.update({
                "description": present["description"],
                "summary": present["current_judgment"],
                "supporting_claim_ids": present["supporting_claim_ids"],
                "pending_claim_ids": present["pending_claim_ids"],
                "contradicted_claim_ids": present["contradicted_claim_ids"],
                "alternatives": present["alternatives"],
                "change_conditions": present["change_conditions"],
                "next_steps": present["next_steps"],
                "source_ref": f"section:{present['source_section_id']}",
                "source_snapshot_version": present["snapshot_version"],
            })
        conclusions.append(entry)
        details[f"hypothesis:{hid}"] = entry
    conclusions.sort(key=lambda h: (not h["chosen"], h["hypothesis_id"]))

    # ---- timeline[](台账 transitions 现成只读;零新 SQL,核实偏差已核)
    claim_ids = set(claim_by)
    tl = []
    for row in led.transitions(event_id):
        cid = row["claim_id"]
        if cid not in claim_ids:
            continue
        rnd = row["round"]
        if as_of_round is not None and (rnd or 0) > as_of_round:
            continue
        tl.append({"ts": row["ts"] or "", "claim_id": cid,
                   "old_status": row["old_status"] or "",
                   "new_status": row["new_status"] or "",
                   "cause": row["cause"] or "",
                   "evidence_id": row["evidence_id"] or "",
                   "round": rnd})
    tl.sort(key=lambda x: (x["ts"] or "9999", x["claim_id"],
                           x["new_status"]))
    tl_by: dict[str, list[dict]] = {}
    for x in tl:
        tl_by.setdefault(x["claim_id"], []).append(x)
    for c in claims_out:   # claim 详情补全(类别/实体/全史转移)
        details[f"claim:{c['claim_id']}"].update({
            "category": c["category"], "entities": c["entities"],
            "transitions": tl_by.get(c["claim_id"], [])})

    # ---- 桥/融合 details
    for b in bridges:
        details[f"bridge:{b['insight_id']}"] = {
            "text": b["text"], "conclusion": b["conclusion"],
            "basis": list(b["basis"]), "n_basis": b["n_basis"],
            "n_verified": b["n_verified"], "target_group": b["target_group"],
            "rationale": b["rationale"]}
    for f in fusions:
        details[f"fusion:{f['source_claim_id']}"] = {
            "text": f["text"], "members": list(f["members"]),
            "all_members": sorted(set(f["all_members"])), "n": f["n"],
            "n_items": f["n_items"], "anchors": list(f["anchors"]),
            "party": f["party"], "group": f["group"]}

    # ---- stats(材料数≠独立根数显式三字段;各分布计数)
    lane_counts: dict[str, int] = {}
    for n in nodes:
        lane_counts[n["type"]] = lane_counts.get(n["type"], 0) + 1
    status_dist: dict[str, int] = {}
    for c in cards:
        status_dist[c["status"]] = status_dist.get(c["status"], 0) + 1
    n_sup = {"支持": 0, "矛盾": 0, "时效刷新": 0}
    unruled_n = 0
    for r in rulings:
        for it in r["items"]:
            if it["ruling"] in n_sup:
                n_sup[it["ruling"]] += 1
        unruled_n += len(r["unruled_nums"])
    platform_dist: dict[str, int] = {"internal": len(internal_ids
                                                     & evidence_ids)}
    for x in evidence_out:
        if x["sensitivity"] != "internal":
            key = x["platform"] or "未知"
            platform_dist[key] = platform_dist.get(key, 0) + 1
    n_roots_all = len({e["root"] for e in drawn})
    stats = {
        "n_materials": len(drawn),
        "n_independent_roots": n_roots_all,
        "n_same_root_collapsed": len(drawn) - n_roots_all,
        "lanes": {lane["id"]: lane_counts.get(lane["id"], 0)
                  for lane in LANES},
        "status_dist": status_dist,
        "evidence_state_dist": {
            state: sum(1 for cid, metric in evidence_metrics.items()
                       if cid not in branch_by_claim
                       and metric["evidence_state"] == state)
            for state in ("supported", "contested", "unadjudicated",
                          "no_direct")},
        "ruling_dist": {"支持": n_sup["支持"], "矛盾": n_sup["矛盾"],
                        "时效刷新": n_sup["时效刷新"],
                        "无关票": meta.get("n_tried_hidden", 0),
                        "挂账未裁": meta.get("n_unadjudicated", 0),
                        "未裁关联": unruled_n},
        "platform_dist": dict(sorted(platform_dist.items())),
    }

    # ---- omissions(全量映射 meta 现成计数;键序=字典序)
    omissions = [{"key": k, "n": int(meta.get(k, 0)), "note": note}
                 for k, note in sorted(_OMISSION_NOTES.items())]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "event": {"event_id": event_id, "kind": _event_kind(led, event_id),
                  "as_of": meta.get("as_of", ""),
                  "as_of_round": meta.get("as_of_round"),
                  "kb_version": meta.get("kb_version", "")},
        "meta": meta,
        "layers": [dict(l) for l in LANES],
        "nodes": nodes,
        "edges": edges,
        "raw_edges": sorted(raw_edges, key=lambda e: e["id"]),
        "edge_groups": edge_groups,
        "evidence": evidence_out,
        "claims": claims_out,
        "verification": verifications,
        "conclusions": conclusions,
        "timeline": tl,
        "stats": stats,
        "omissions": omissions,
        "details": details,
    }
    payload["meta"]["web"] = {
        "n_nodes": len(nodes), "n_edges": len(edges),
        "n_raw_edges": len(raw_edges), "n_edge_groups": len(edge_groups),
        "n_evidence": len(evidence_out), "n_claims": len(claims_out),
        "n_collapsed": collapsed_n, "n_details": len(details),
        "web_validation": []}
    issues = validate_chain_web(payload, strict=False)
    payload["meta"]["web"]["web_validation"] = issues
    if issues:
        raise ChainWebError("chain_web.v1 校验失败(失败不伪装,不出件):\n"
                            + "\n".join(issues))
    return payload


def counts_of(items: list[dict], ruling: str) -> int:
    return sum(1 for x in items if x["ruling"] == ruling)


def _evidence_state_reason(metrics: dict) -> str:
    """直接证据徽章的人话解释(数值只来自已计算机械口径)。"""
    state = metrics.get("evidence_state")
    if state == "contested":
        return (f"存在 {metrics.get('n_counter', 0)} 条已承认反证;"
                f"正向/时效边 {metrics.get('n_direct_support', 0)} 条")
    if state == "supported":
        return f"有 {metrics.get('n_direct_support', 0)} 条已承认直接支持/时效边"
    if state == "unadjudicated":
        return f"有 {metrics.get('n_unadjudicated', 0)} 件关联材料尚未裁定"
    tried = metrics.get("n_irrelevant_tried", 0)
    return (f"没有被承认的直接证据;"
            + (f"已查过 {tried} 件但判为无关" if tried else "尚待补证"))


_CATEGORY_CACHE: dict[int, dict[str, str]] = {}


def _category_of(led, cid: str) -> str:
    """claims 表 category 列现成值(事实/实体/关系/时效/矛盾;核实偏差 3)。
    按 led 实例缓存全表映射(一次查询,进程内复用);缺行=空串如实。"""
    key = id(led)
    cache = _CATEGORY_CACHE.get(key)
    if cache is None:
        try:
            cache = {r["claim_id"]: r["category"] or ""
                     for r in led.conn.execute(
                         "SELECT claim_id, category FROM claims")}
        except Exception:   # 旧库缺列:全空如实降级(失败不伪装)
            cache = {}
        _CATEGORY_CACHE[key] = cache
    return cache.get(cid, "")


# ---------------------------------------------------------------- 校验

def validate_chain_web(payload: dict, strict: bool = True) -> list[str]:
    """契约校验器(§7 验收;strict=True 任一问题抛 ChainWebError=CLI 不出
    件;strict=False 返回问题清单供测试逐条断言)。"""
    issues: list[str] = []

    def bad(msg: str) -> None:
        issues.append(msg)

    if payload.get("schema_version") != SCHEMA_VERSION:
        bad(f"schema_version={payload.get('schema_version')!r} 不符")
    lanes = [l.get("id") for l in payload.get("layers") or []]
    if lanes != [l["id"] for l in LANES]:
        bad(f"layers 五泳道不符: {lanes}")

    nodes = payload.get("nodes") or []
    node_ids = [n.get("id") for n in nodes]
    if len(node_ids) != len(set(node_ids)):
        bad("节点 id 不唯一")
    id_set = set(node_ids)
    details = payload.get("details") or {}
    evidence_ids = {e["evidence_id"] for e in payload.get("evidence") or []}
    claim_ids = {c["claim_id"] for c in payload.get("claims") or []}
    claim_by = {c["claim_id"]: c for c in payload.get("claims") or []}
    internal = {e["evidence_id"] for e in payload.get("evidence") or []
                if e.get("sensitivity") == "internal"}

    # gap 新件为结构化对象，旧件字符串兼容。已知字段严格验型，
    # 未知字段可保留但前端绝不直接当 React child 渲染。
    gap_text_keys = ("type", "label", "next", "channel", "next_channel",
                     "channel_rationale")
    gap_count_keys = ("tries", "unadjudicated")
    for c in payload.get("claims") or []:
        gap = c.get("gap")
        cid = c.get("claim_id") or "?"
        if gap is not None and not isinstance(gap, (str, dict)):
            bad(f"claim {cid} gap 必须为 null/string/object")
            continue
        if isinstance(gap, dict):
            for key in gap_text_keys:
                if key in gap and not isinstance(gap[key], str):
                    bad(f"claim {cid} gap.{key} 必须为 string")
            for key in gap_count_keys:
                value = gap.get(key)
                if key in gap and (not isinstance(value, (int, float))
                                   or isinstance(value, bool)):
                    bad(f"claim {cid} gap.{key} 必须为 number")

    for h in payload.get("conclusions") or []:
        hid = h.get("hypothesis_id") or "?"
        for key in ("description", "summary", "source_ref",
                    "source_snapshot_version"):
            if key in h and not isinstance(h[key], str):
                bad(f"conclusion {hid} {key} 必须为 string")
        for key in ("supporting_claim_ids", "pending_claim_ids",
                    "contradicted_claim_ids"):
            if key in h:
                value = h[key]
                if (not isinstance(value, list)
                        or any(not isinstance(cid, str) for cid in value)):
                    bad(f"conclusion {hid} {key} 必须为 string[]")
                elif any(cid not in claim_ids for cid in value):
                    bad(f"conclusion {hid} {key} 存在 claim 悬空")

    for n in nodes:
        ref = n.get("detail_ref")
        if ref and ref not in details:
            bad(f"节点 {n['id']} detail_ref 悬空: {ref}")
        if (n.get("type") in ("fact", "verification")
                and n.get("sub_type") not in ("fact_cluster", "gap_hint",
                                               "meta_info")):
            if n.get("status") not in ("已证实", "矛盾", "待证实"):
                bad(f"节点 {n['id']} status 非三态: {n.get('status')!r}")
        if n.get("type") == "fact" and n.get("sub_type") != "fact_cluster":
            if n.get("evidence_state") not in (
                    "supported", "contested", "unadjudicated", "no_direct"):
                bad(f"事实节点 {n['id']} evidence_state 越界")

    relations = set(_RELATION_WORDS)
    for e in payload.get("edges") or []:
        if e.get("source") not in id_set or e.get("target") not in id_set:
            bad(f"边 {e.get('id')} 端点悬空")
        if e.get("relation_type") not in relations:
            bad(f"边 {e.get('id')} 关系词越界: {e.get('relation_type')!r}")
        for eid in e.get("evidence_ids") or []:
            if eid not in evidence_ids:
                bad(f"边 {e.get('id')} evidence 悬空: {eid}")
        for cid in e.get("claim_ids") or []:
            if cid not in claim_ids:
                bad(f"边 {e.get('id')} claim 悬空: {cid}")
        for rid in e.get("ruling_ids") or []:
            if rid not in details:
                bad(f"边 {e.get('id')} ruling 反查悬空: {rid}")
        if (e.get("relation_type") in ("直接证明", "印证", "佐证", "反证",
                                       "时效更新") and not e.get("ruling_ids")
                and not e.get("sidecar")):   # 融合卡边=旁车聚合,非裁定
            bad(f"边 {e.get('id')} 支持族边缺 ruling 反查")

    # raw_edges 全量口径允许指向已语义分流、不在 nodes 的 claim;
    # 但 claim/evidence/ruling 三向反查仍必须完整。
    raw_edges = payload.get("raw_edges") or []
    for e in raw_edges:
        for eid in e.get("evidence_ids") or []:
            if eid not in evidence_ids:
                bad(f"原始边 {e.get('id')} evidence 悬空: {eid}")
        for cid in e.get("claim_ids") or []:
            if cid not in claim_ids:
                bad(f"原始边 {e.get('id')} claim 悬空: {cid}")
        for rid in e.get("ruling_ids") or []:
            if rid not in details:
                bad(f"原始边 {e.get('id')} ruling 反查悬空: {rid}")

    raw_ids = {e.get("id") for e in raw_edges}
    group_ids: set[str] = set()
    for g in payload.get("edge_groups") or []:
        gid = g.get("id")
        if not gid or gid in group_ids:
            bad(f"edge_group id 不唯一: {gid!r}")
        group_ids.add(gid)
        if g.get("source") not in id_set or g.get("node_id") not in id_set:
            bad(f"edge_group {gid} 端点悬空")
        if g.get("n") != len(g.get("raw_edge_ids") or []):
            bad(f"edge_group {gid} n 与 raw_edge_ids 不一致")
        if not set(g.get("raw_edge_ids") or []) <= raw_ids:
            bad(f"edge_group {gid} 原始边反查悬空")
        if not set(g.get("members") or []) <= claim_ids:
            bad(f"edge_group {gid} 成员 claim 悬空")

    for n in nodes:
        if n.get("type") in ("fact", "verification", "reasoning",
                             "conclusion"):
            if not n.get("provenance_ids") and not n.get("no_direct_evidence"):
                bad(f"节点 {n['id']} 无 provenance 且未标 no_direct_evidence")
            for eid in n.get("provenance_ids") or []:
                if eid not in evidence_ids:
                    bad(f"节点 {n['id']} provenance 悬空: {eid}")

    # 敏感压净(纪律,不是注记)
    for e in payload.get("evidence") or []:
        if e.get("sensitivity") != "internal":
            continue
        eid = e["evidence_id"]
        for k in ("excerpt", "source_ref", "source_url", "platform",
                  "published_at", "collected_at", "attachment_ref"):
            if e.get(k) is not None:
                bad(f"internal 证据 {eid} 字段未压净: {k}")
        if e.get("title") != _INTERNAL_TITLE:
            bad(f"internal 证据 {eid} title 未压为定位符")
        if f"evidence:{eid}" in details or f"assets:{eid}" in details:
            bad(f"internal 证据 {eid} details 内容键未撤")
    for key, d in details.items():
        if key.startswith("ruling:") and d.get("masked"):
            if d.get("quote") is not None or d.get("reason") is not None:
                bad(f"敏感裁定 {key} quote/reason 未压空")
    for n in nodes:
        if n.get("sub_type") == "融合":
            if internal & set(n.get("provenance_ids") or []):
                bad(f"融合节点 {n['id']} provenance 混入 internal 件")

    # 折叠口径(family_rep 非代表必折叠;小区必折叠)
    for n in nodes:
        if n["type"] != "fact":
            continue
        cid = n["id"].split(":", 1)[-1]
        c = claim_by.get(cid)
        if c is None:
            continue
        if c.get("family_rep") and not n.get("collapsed"):
            bad(f"家族非代表 {n['id']} 未折叠")

    # routed claim 唯一映射:不得同时冒出 fact 和逐 claim verification。
    routed_ids = {c["claim_id"] for c in payload.get("claims") or []
                  if c.get("interpretive")}
    for cid in routed_ids:
        if f"cl:{cid}" in id_set or f"vf:{cid}" in id_set:
            bad(f"分流 claim {cid} 重复生成 fact/verification 卡")

    # 可见 reasoning 必须有入边和出边，避免「为了好看」的孤立推理。
    incoming = {e.get("target") for e in payload.get("edges") or []}
    outgoing = {e.get("source") for e in payload.get("edges") or []}
    for n in nodes:
        if (n.get("type") == "verification" and n["id"] not in incoming
                and n["id"] not in outgoing
                and not n.get("intentional_orphan")):
            bad(f"验证节点 {n['id']} 孤立且未标 intentional_orphan")
        if n.get("type") == "reasoning":
            if n["id"] not in incoming or n["id"] not in outgoing:
                bad(f"推理节点 {n['id']} 缺入边或出边")

    # 计数一致
    st = payload.get("stats") or {}
    lane_counts: dict[str, int] = {}
    for n in nodes:
        lane_counts[n["type"]] = lane_counts.get(n["type"], 0) + 1
    if st.get("lanes") != {l["id"]: lane_counts.get(l["id"], 0)
                           for l in LANES}:
        bad("stats.lanes 与节点实计不符")
    web = (payload.get("meta") or {}).get("web") or {}
    if web and web.get("n_collapsed") != sum(1 for n in nodes
                                             if n.get("collapsed")):
        bad("meta.web.n_collapsed 与节点实计不符")
    meta = payload.get("meta") or {}
    for o in payload.get("omissions") or []:
        mk = meta.get(o["key"])
        if isinstance(mk, int) and mk != o["n"]:
            bad(f"omissions {o['key']}={o['n']} 与 meta {mk} 不符")

    if strict and issues:
        raise ChainWebError("chain_web.v1 校验失败(失败不伪装,不出件):\n"
                            + "\n".join(issues))
    return issues
