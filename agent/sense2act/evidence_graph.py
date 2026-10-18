"""证据图谱(档1 mermaid,2026-10-11/12 用户逐轮定稿版):台账 → 图的确定性投影。

三张图,各答一个问题:
  图 A 实体关系图        实体之间有什么关系(边带三态:实线=已证实/虚线=待证实/红=矛盾)
  图 B1 复合事实 ⇒ 竞争假设  研判的证据面(粗箭头"支撑";实线=结论,虚线=候选假设)
  图 B2 证据 → 复合事实   查实的证据面("证实"箭头:证据拼入了哪条复合事实)

四条纪律(用户逐轮打磨定稿):
1. 确定性投影:图=台账只读渲染,纯代码零 LLM(分权纪律延伸:代码画图,模型不画);
2. 边带三态:待证实关系绝不画成实线(可视化不放大过度断言);
3. 事实全文零截断:清洗(元话语/URL/金额万元化)+标点折行(英文词保护),信息零损失;
4. 判别语义归节点:实线框=结论(唯一最高分),灰虚线框=候选(尚无专属事实),
   共享事实同升不判别——不给人"共享事实=已证明"的误导。

复合事实=已证实 claim 按主语实体聚合的超节点(图摘要手法);节点内列全部明细
(>12 项折叠且指明去处)。解读类过程事实(定性/基础缺口/假设种子)整类不进图。
全部迭代显式排序,无时间戳——report.md 字节级可复现(test_deterministic_rerun 兜底)。
"""
from __future__ import annotations

import json
import re

MAX_CLAIMS = 40          # 图 B 复合事实组预算(超限按 priority 降序保留并注明)
MAX_DETAIL_LINES = 12    # 复合节点内明细行上限(超限折叠且指明去处)
WRAP_FACT = 18           # 事实折行宽度(mermaid 超长 label 会裁字,折到安全宽度)
WRAP_TITLE = 22          # 证据标题折行宽度

DOMAINS = (("金额", "金额变化得分", "金额依据"),
           ("新颖性", "新颖性得分", "新颖性依据"),
           ("频率", "频率变化得分", "频率依据"))

_LEGEND = (
    "> 图例:实线=已证实 | 虚线=待证实 | 红=矛盾(图 A);"
    "实线箭头=复合事实支撑该假设(图 B1);'证实'箭头=证据拼入复合事实(图 B2);"
    "蓝实框=结论(唯一最高分),灰虚线框=候选假设(尚无专属事实),🏆=主导,👤=委托方裁决;"
    "证据〔〕内=谱系根(同根=非独立,独立根≥2 才已证实)。\n"
    "> 本图由台账确定性投影生成(零 LLM);在线环境自动渲染,"
    "离线可看本文件 mermaid 源码(VSCode/Typora 原生渲染)。")


def _esc(text: str, limit: int = 40) -> str:
    """mermaid 标签转义+定长裁剪(仅用于图 A 实体名等短字段)。"""
    out = (text or "").replace('"', "'").replace("\n", " ").strip()
    return out[:limit] + ("…" if len(out) > limit else "")


def _mq(text: str) -> str:
    """mermaid 引号安全(2026-10-16,中山大学跑实战):长文本入 "..." 标签前
    必须折掉直引号——否则提前终止字符串,mermaid 11.4.1 报 Syntax error
    (上游项目编号自带引号如 "中大招（货）[2026]209号" 为实弹触发形态)。
    与 _esc 同约定折成单引号;不截断(定稿:事实全文零截断)。"""
    return (text or "").replace('"', "'")


def _clean_fact(text: str) -> str:
    """展示层清洗(台账原文不动):去元话语与噪音,只留事实本体。

    - '信封载明X'/'信封 relevance 金额依据载明X' → 'X'(信封怎么说不进图);
    - '解读将本信号定性为「商机」,理由为…' → '本信号定性为商机'(论证尾巴不进图);
    - 检测器内容的项目名样板去重(主语已是复合事实标题);
    - URL → '链接见附录 A'(mermaid 会当 markdown 链接报错,且超长串撑爆节点);
    - ≥1 万的'X 元' → 'Y 万元'(金额友好化)。
    """
    t = (text or "").strip()
    t = re.sub(r"^信封.*?载明[:：]?\s*", "", t)
    t = re.sub(r"^信封自述[:：]\s*", "", t)
    t = re.sub(r"^解读将本信号", "本信号", t)
    t = re.sub(r"^解读登记的", "", t)
    t = re.sub(r"^解读选定的", "", t)
    t = re.sub(r"[,，]?理由为.*$", "", t)

    def _yuan(m):
        v = float(m.group(1))
        return f"{v/10000:g} 万元" if v >= 10000 else m.group(0)

    t = re.sub(r"(\d+(?:\.\d+)?) 元", _yuan, t)
    t = re.sub(r" 的采购项目『[^』]*』触发", " 触发", t)
    t = re.sub(r"链接\s*https?://\S+", "链接见附录 A", t)
    t = t.replace("(", "（").replace(")", "）")   # 半角括号→全角(标签安全)
    t = re.sub(r"https?://\S+", "链接见附录 A", t)
    t = t.rstrip("。")
    # 仅剥配对的外层引号(「A」「B」列表型保留;不剥内容自己的闭引号)
    if t.startswith("「") and t.endswith("」") and "」「" not in t:
        t = t[1:-1]
    return t.strip() or (text or "").strip()


def _wrap(text: str, width: int) -> str:
    """全文折行(零截断):超宽按标点断行,切点避开英文单词。"""
    segs = []
    while len(text) > width:
        cut = width
        m = re.search(r"[A-Za-z0-9]+$", text[:cut])
        if m and m.start() > 0 and not re.match(r"\s", text[cut:cut + 1]):
            cut = m.start()                      # 回退到英文词首
        if cut < width // 2:
            cut = width
        for p in (",", "，", "、", ";", "：", ":", ")", "」"):
            k = text.rfind(p, 0, cut + 1)
            if k >= cut // 2:
                cut = k + 1
                break
        segs.append(text[:cut])
        text = text[cut:]
    segs.append(text)
    return "<br/>".join(segs)


def _pretty(line: str) -> str:
    """排版后处理:域前缀加粗,异常得分橙色高亮(图谱里唯一暖色数字)。"""
    line = re.sub(r"^(金额|新颖性|频率):",
                  lambda m: "<b>" + m.group(1) + ":</b>", line)
    line = re.sub(r"\((金额|新颖性|频率)异常得分 ([\d.]+)",
                  lambda m: ("(<span style='color:#b3502c'>" + m.group(1)
                             + "异常得分 <b>" + m.group(2) + "</b></span>"),
                  line)
    return line


# ---------------------------------------------------------------- 数据装配

def build_graph_payload(ledger, event_id: str) -> dict:
    """台账只读装配(全部 SQL 显式排序;直查先例=adjudicate/askgate/_deliver)。

    返回 JSON 可序列化 dict(payload_to_json 锁):档2 ECharts 的数据源。
    """
    claims_all = ledger.all_claims(event_id)
    # 解读类过程事实排除(用户定稿 2026-10-12):定性/基础缺口/假设种子不进图
    excluded = sum(1 for c in claims_all if c["text"].startswith("解读"))
    claims = [
        {"claim_id": c["claim_id"], "draft_id": c["draft_id"], "text": c["text"],
         "status": c["status"], "category": c["category"],
         "priority": c["priority"], "parent": c["parent_claim_id"]}
        for c in claims_all if c["status"] != "未定"
        and not c["text"].startswith("解读")]
    # 图 B 节点预算:超限按 priority 降序保留(渲染端只认 payload)
    omitted = 0
    if len(claims) > MAX_CLAIMS:
        ordered = sorted(claims, key=lambda c: (-c["priority"], c["draft_id"]))
        keep = {c["claim_id"] for c in ordered[:MAX_CLAIMS]}
        claims = [c for c in claims if c["claim_id"] in keep]
        omitted = len(ordered) - len(keep)

    entities_rows = ledger.conn.execute(
        "SELECT DISTINCT e.entity_id, e.canonical_name, e.type, e.anchor_status"
        " FROM entities e"
        " JOIN claim_entities ce ON ce.entity_id=e.entity_id"
        " JOIN claims c ON c.claim_id=ce.claim_id"
        " WHERE c.event_id=? ORDER BY e.canonical_name", (event_id,)).fetchall()
    entities = [{"entity_id": r["entity_id"], "name": r["canonical_name"],
                 "type": r["type"], "anchor": r["anchor_status"]}
                for r in entities_rows]

    edges: list[dict] = []
    skipped_no_endpoints = 0
    for r in ledger.relations_view(event_id):            # 已证实(视图已过滤)
        edges.append({"style": "solid", "label": r["relation_type"] or "相关",
                      "head": r["head_name"], "tail": r["tail_name"],
                      "claim_id": r["claim_id"]})
    rel_claims = ledger.conn.execute(
        "SELECT claim_id, draft_id, status, relation_type FROM claims"
        " WHERE event_id=? AND category='关系' AND status != '已证实'"
        " ORDER BY draft_id", (event_id,)).fetchall()
    for rc in rel_claims:
        ends = ledger.conn.execute(
            "SELECT ce.role, e.canonical_name FROM claim_entities ce"
            " JOIN entities e ON e.entity_id=ce.entity_id"
            " WHERE ce.claim_id=? AND ce.role IN ('头','尾')",
            (rc["claim_id"],)).fetchall()
        head = next((x["canonical_name"] for x in ends if x["role"] == "头"), None)
        tail = next((x["canonical_name"] for x in ends if x["role"] == "尾"), None)
        if not head or not tail:
            skipped_no_endpoints += 1                    # 缺头尾,不硬画
            continue
        edges.append({"style": "dashed" if rc["status"] == "待证实" else "red",
                      "label": rc["relation_type"] or "相关", "head": head,
                      "tail": tail, "claim_id": rc["claim_id"]})
    edges.sort(key=lambda e: (e["head"], e["tail"], e["label"], e["style"]))

    sup_rows = ledger.conn.execute(
        "SELECT ce.claim_id, e.evidence_id, e.lineage_root, ce.ruling,"
        " e.title, e.content FROM claim_evidence ce"
        " JOIN evidence e ON e.evidence_id=ce.evidence_id"
        " WHERE e.event_id=? ORDER BY e.evidence_id, ce.claim_id",
        (event_id,)).fetchall()
    supports = [{"claim_id": r["claim_id"], "evidence_id": r["evidence_id"],
                 "root": r["lineage_root"] or "(无谱系)", "ruling": r["ruling"],
                 "title": r["title"] or "", "content": r["content"] or ""}
                for r in sup_rows]

    ev_rows = ledger.conn.execute(
        "SELECT evidence_id, title, lineage_root, content FROM evidence"
        " WHERE event_id=? ORDER BY evidence_id", (event_id,)).fetchall()
    evidence = [{"evidence_id": r["evidence_id"], "title": r["title"] or "",
                 "root": r["lineage_root"] or "(无谱系)",
                 "content": r["content"] or ""} for r in ev_rows]

    standings = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
                 for s in ledger.hypothesis_standings(event_id)}
    h_rows = ledger.conn.execute(
        "SELECT hc.hypothesis_id, hc.claim_id FROM hypothesis_claims hc"
        " JOIN hypotheses h ON h.hypothesis_id=hc.hypothesis_id"
        " WHERE h.event_id=? ORDER BY hc.hypothesis_id, hc.claim_id",
        (event_id,)).fetchall()
    groups: dict[str, list[str]] = {}
    for r in h_rows:
        groups.setdefault(r["hypothesis_id"].rsplit(":", 1)[-1], []).append(
            r["claim_id"])
    hypothesis_groups = [
        {"draft_id": hid, "label": standings.get(hid, {}).get("label", hid),
         "dominant": bool(standings.get(hid, {}).get("dominant")),
         "adjudicated": bool(standings.get(hid, {}).get("adjudicated")),
         "score": standings.get(hid, {}).get("score", 0),
         "claim_ids": cids}
        for hid, cids in sorted(groups.items())]

    # 复合事实主语:已证实 claim 的头实体(无头取首条实体链接)
    subj_rows = ledger.conn.execute(
        "SELECT ce.claim_id, e.canonical_name, ce.role FROM claim_entities ce"
        " JOIN entities e ON e.entity_id=ce.entity_id"
        " JOIN claims c ON c.claim_id=ce.claim_id"
        " WHERE c.event_id=? AND c.status='已证实'"
        " ORDER BY ce.claim_id, ce.role", (event_id,)).fetchall()
    subjects: dict[str, str] = {}
    for r in subj_rows:
        cur = subjects.setdefault(r["claim_id"], r["canonical_name"])
        if r["role"] == "头":
            subjects[r["claim_id"]] = r["canonical_name"]

    conflicts = sum(1 for c in claims if c["status"] == "矛盾")

    return {"event_id": event_id, "entities": entities,
            "relation_edges": edges,
            "skipped_relation_no_endpoints": skipped_no_endpoints,
            "claims": claims, "supports": supports, "evidence": evidence,
            "hypothesis_groups": hypothesis_groups, "subjects": subjects,
            "conflicts": conflicts,
            "excluded_interpretation": excluded,
            "omitted": omitted}


# ---------------------------------------------------------------- 检测器配对

def merge_detector_facts(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """检测器得分行与同域依据行配对合并(两遍法,全事件级)。

    items=[(组名, 原文)];'金额变化得分 0.800'(得分行)并入'金额依据载明「…」'
    (同域依据行)→ '金额:依据本体(金额异常得分 0.800)'——得分不脱离上下文,
    依据不再重复。合并产物落在依据行所在组;已配对得分行不再单独输出。
    """
    scored = []
    basis: dict[str, list[tuple[int, str, str]]] = {}
    out_items: dict[int, tuple[str, str]] = {}
    for i, (gid, t) in enumerate(items):
        kind = None
        for disp, score_key, basis_key in DOMAINS:
            m = re.search(rf"{score_key}\s*(\d+(?:\.\d+)?)", t)
            if m:
                insuff = ("basisSufficient" in t and "false" in t)
                scored.append((i, gid, disp, m.group(1), insuff))
                kind = "score"
                break
            if basis_key in t:
                basis.setdefault(basis_key, []).append((i, gid, t))
                kind = "basis"
                break
        if kind is None:
            out_items[i] = (gid, _clean_fact(t))
    used_basis = set()
    for i, gid, disp, score, insuff in scored:
        basis_key = next(d[2] for d in DOMAINS if d[0] == disp)
        pick = None
        for j, gid2, t2 in basis.get(basis_key, []):
            if j not in used_basis:
                pick = (j, gid2, _clean_fact(t2))
                break
        if pick:
            used_basis.add(pick[0])
            out_items[pick[0]] = (pick[1], f"{disp}:{pick[2]}"
                                  f"({disp}异常得分 {score}"
                                  + (",依据不充分)" if insuff else ")"))
        else:
            out_items[i] = (gid, f"{disp}异常得分 {score}")
    return [out_items[i] for i in sorted(out_items)]


# ---------------------------------------------------------------- 渲染

_CLASSDEFS = (
    "  classDef stVerified fill:#dff0d8,stroke:#3c763d\n"
    "  classDef stPending fill:#f5f5f5,stroke:#999\n"
    "  classDef stConflict fill:#f2dede,stroke:#c9302c\n"
    "  classDef anAnchor fill:#dff0d8,stroke:#3c763d\n"
    "  classDef anBlank fill:#f5f5f5,stroke:#999\n"
    "  classDef anFork fill:#fcf8e3,stroke:#8a6d3b\n"
    "  classDef compoundNode fill:#dff0d8,stroke:#3c763d,text-align:left\n"
    "  classDef hypConc fill:#d9edf7,stroke:#31708f,stroke-width:2px\n"
    "  classDef hypCand fill:#f5f5f5,stroke:#999,stroke-dasharray: 5 3\n"
    "  classDef evNode fill:#fcf8e3,stroke:#8a6d3b\n"
    "  classDef conflictGroup fill:#f2dede,stroke:#c9302c,text-align:left")

_ANCHOR_CLASS = {"已锚": "anAnchor", "未锚": "anBlank", "分叉": "anFork"}


def render_relations(payload: dict) -> str:
    """图 A:实体关系图(边带三态)。无边时返回占位说明(空 mermaid 会渲染失败)。"""
    edges = payload["relation_edges"]
    if not edges:
        return ("图 A 省略:本事件无实体关系边"
                "(没有类目=关系的 claim,或关系断言缺实体头尾)。")
    entities = {e["name"]: e for e in payload["entities"]}
    names = sorted({e["head"] for e in edges}
                   | {e["tail"] for e in edges}
                   | set(entities))
    eid = {name: f"E{i}" for i, name in enumerate(names)}
    lines = ["flowchart TD"]
    for name in names:
        ent = entities.get(name)
        label = _esc(name, 30)
        if ent and ent.get("type"):
            label += f"（{ent['type']}）"
        lines.append(f'  {eid[name]}["{label}"]')
    red_idx: list[int] = []
    for i, e in enumerate(edges):
        h, t = eid[e["head"]], eid[e["tail"]]
        label = _esc(e["label"], 20)
        if e["style"] == "solid":
            lines.append(f'  {h} ---|"{label}"| {t}')
        elif e["style"] == "dashed":
            lines.append(f'  {h} -.->|"? {label}"| {t}')
        else:                                            # red(矛盾)
            lines.append(f'  {h} -->|"{label} ✗"| {t}')
            red_idx.append(i)
    for idx in red_idx:
        lines.append(f"  linkStyle {idx} stroke:#c9302c,stroke-width:2px")
    lines.append(_CLASSDEFS.rstrip("\n"))
    for state, cls in _ANCHOR_CLASS.items():
        ids = [eid[n] for n in names
               if entities.get(n, {}).get("anchor") == state]
        if ids:
            lines.append(f"  class {','.join(ids)} {cls}")
    return "\n".join(lines)


def _verified_items(payload: dict) -> list[tuple[str, str]]:
    """复合事实原料(2026-10-16 修复):仅【已证实】claim 进入聚合——定稿语义
    "复合事实=已证实 claim 按主语实体聚合";此前四处喂料未过滤状态,待证实
    条目经 subjects 兜底全部混进"其他事实"组冒充查实。无主语的已证实条
    (如纯窗口期/元状态条)归"其他事实"。"""
    claims = {c["claim_id"]: c for c in payload["claims"]}
    subjects = payload.get("subjects", {})
    return [(subjects.get(cid, "其他事实"), claims[cid]["text"])
            for cid in sorted(claims)
            if claims[cid]["status"] == "已证实"]


def render_b1(payload: dict) -> str:
    """图 B1:复合事实 ⇒ 竞争假设(研判的证据面)。

    节点:复合事实(同主语已证实 claim 聚合,节点内全明细)、竞争假设
    (蓝实框=结论(唯一最高分),灰虚框=候选(含并列领先——共享事实同升
    不判别,并列不算确立));边:支撑(实线指结论,虚线指候选)。
    """
    claims = {c["claim_id"]: c for c in payload["claims"]}
    subjects = payload["subjects"]
    groups = payload["hypothesis_groups"]
    if not claims:
        return "图 B1 省略:本事件无已证实事实。"
    c2hs: dict[str, list[str]] = {}
    for g in groups:
        for cid in g["claim_ids"]:
            if cid in claims:
                c2hs.setdefault(cid, []).append(g["draft_id"])
    claim_hcount = {cid: len(hs) for cid, hs in c2hs.items()}

    merged = merge_detector_facts(_verified_items(payload))
    by_group: dict[str, list[str]] = {}
    for gid, t in merged:
        by_group.setdefault(gid, []).append(t)

    scores = {g["draft_id"]: g.get("score", 0) for g in groups}
    best = max(scores.values(), default=0)
    leaders = [h for h, s in scores.items() if s == best and best > 0]
    conclusion = leaders[0] if len(leaders) == 1 else None

    # 支撑边:仅已证实 claim 计入;单归属/多归属并入同一张边表
    # (2026-10-16 修复:原 excl/share 两表可对同一(组,假设)各产一条,
    # 渲染出重复边)
    edges: dict[tuple, int] = {}
    for cid, c in claims.items():
        if c["status"] != "已证实":
            continue
        gid = subjects.get(cid, "其他事实")
        for h in c2hs.get(cid, []):
            edges[(gid, h)] = edges.get((gid, h), 0) + 1

    lines = ["flowchart TD"]
    gmap = {name: f"G{i}" for i, name in enumerate(sorted(by_group))}
    for name in sorted(by_group):
        n = len(by_group[name])
        lines.append(
            f'  {gmap[name]}["<b>📋 关于{_esc(name, 16)} · {n} 项查实</b>'
            f'<br/>{"<br/>".join("· " + _pretty(_mq(t)) for t in by_group[name])}"]'
            f":::compoundNode")
    hid = {g["draft_id"]: f"H{i}" for i, g in enumerate(groups)}
    for g in groups:
        hname = g["draft_id"]
        sc = scores.get(hname, 0)
        adjudicated = "👤" if g.get("adjudicated") else ""
        if len(leaders) == 1 and hname == leaders[0]:
            role, badge, cls = "结论", "🏆" + adjudicated, "hypConc"
        elif hname in leaders:
            role, badge, cls = "假设(并列领先)", adjudicated, "hypCand"
        else:
            role, badge, cls = "假设", adjudicated, "hypCand"
        lines.append(f'  {hid[hname]}["<b>{_esc(g["label"], 16)}</b>'
                     f'({role} · 得分 {sc}){badge}"]:::{cls}')
    hyp_ids = {g["draft_id"] for g in groups}
    for (gid, h), k in sorted(edges.items()):
        style = "-->" if h == conclusion else "-.->"
        if h in hyp_ids and gid in gmap:
            lines.append(f'  {gmap[gid]} {style}|支撑| {hid[h]}')
    lines.append(_CLASSDEFS.rstrip("\n"))
    lines.append(f"  %% 复合事实 {len(by_group)} / 结论 {len(groups)};"
                 f" 事实全文零截断;实线=结论,虚线=候选假设")
    return "\n".join(lines)


def render_b2(payload: dict) -> str:
    """图 B2:证据 → 复合事实(查实的证据面)。

    证据节点:信封自带证据(signal_layer)=内容全文(不标来源——它就是信号
    本身);外采证据=标题+〔谱系根〕(同根=非独立,独立性只对外采有意义)。
    '证实'边=该证据经支持裁定拼入该复合事实;无关/未裁证据不进图。
    """
    claims = {c["claim_id"]: c for c in payload["claims"]}
    subjects = payload["subjects"]
    evidence = {e["evidence_id"]: e for e in payload["evidence"]}
    cites = sorted({(s["evidence_id"], subjects.get(s["claim_id"], "其他事实"))
                    for s in payload["supports"]
                    if s["claim_id"] in claims
                    and claims[s["claim_id"]]["status"] == "已证实"
                    and s["ruling"] in ("支持", "时效刷新")})
    if not cites:
        return "图 B2 省略:本事件无已证实的证据-事实支撑。"
    used_eids = sorted({e for e, _ in cites})
    lines = ["flowchart TD"]
    vid = {eid: f"V{i}" for i, eid in enumerate(used_eids)}
    for eid in used_eids:
        info = evidence[eid]
        num = eid.rsplit(":", 1)[-1]
        if info["root"] == "signal_layer":
            body = _mq(_clean_fact(info["content"] or info["title"]))
            lines.append(f'  {vid[eid]}["[{num}] {body}"]:::evNode')
        else:
            body = _mq(_clean_fact(info["title"]))
            root = _esc(info["root"], 24)
            lines.append(f'  {vid[eid]}["[{num}] {body} 〔{root}〕"]:::evNode')
    by_group: dict[str, list[str]] = {}
    for gid, t in merge_detector_facts(_verified_items(payload)):
        by_group.setdefault(gid, []).append(t)
    gmap = {name: f"G{i}" for i, name in enumerate(sorted(by_group))}
    for name in sorted(by_group):
        n = len(by_group[name])
        lines.append(
            f'  {gmap[name]}["<b>📋 关于{_esc(name, 16)} · {n} 项查实</b>'
            f'<br/>{"<br/>".join("· " + _pretty(_mq(t)) for t in by_group[name])}"]'
            f":::compoundNode")
    n_conflict = payload.get("conflicts", 0)
    if n_conflict:
        lines.append(f'  GX["⚠ 矛盾事实(共 {n_conflict} 条,'
                     f'不计入复合事实)"]:::conflictGroup')
    for eid, name in cites:
        lines.append(f'  {vid[eid]} -->|"证实"| {gmap[name]}')
    lines.append(_CLASSDEFS.rstrip("\n"))
    lines.append(f"  %% 证据 {len(used_eids)} / 复合事实 {len(by_group)} / "
                 f"证实边 {len(cites)};信封证据不标来源,外采标谱系根")
    return "\n".join(lines)


def fact_groups(payload: dict) -> list[tuple[str, list[str]]]:
    """复合事实明细:返回 [(组名, [清洗后的明细文本])],供图下 HTML/markdown
    列表渲染(浏览器原生渲染中文,零测量问题、零截断)。mermaid 节点只放短标题,
    全文明细在此处——绕开 mermaid 对长中文节点标签的测量 bug。"""
    merged = merge_detector_facts(_verified_items(payload))
    by_group: dict[str, list[str]] = {}
    for gid, t in merged:
        by_group.setdefault(gid, []).append(t)
    return sorted(by_group.items())


def render_fact_details(payload: dict) -> str:
    """复合事实明细 markdown(图下列表;全文零截断)。"""
    groups = fact_groups(payload)
    if not groups:
        return ""
    lines = ["", "### E.4 复合事实明细(全文)", ""]
    for name, texts in groups:
        lines.append(f"**关于{name}** · {len(texts)} 项查实")
        for t in texts:
            lines.append(f"- {_clean_fact(t)}")
        lines.append("")
    return "\n".join(lines)


def render_section(payload: dict) -> str:
    """附录 E 整节 markdown(report.md 用;html 按图分别包裹)。"""
    parts = [
        fence_mermaid(render_relations(payload)),
        fence_mermaid(render_b1(payload)),
        fence_mermaid(render_b2(payload)),
    ]
    notes = ""
    if payload.get("omitted"):
        notes += (f"\n> ⚠ 复合事实超出节点预算,按优先级保留前 {MAX_CLAIMS} 条,"
                  f"省略 {payload['omitted']} 条(全文见附录 A 证据表)。")
    skipped = payload.get("skipped_relation_no_endpoints") or 0
    if skipped:
        notes += f"\n> 另有 {skipped} 条关系断言缺实体头尾链接,未入图。"
    excluded = payload.get("excluded_interpretation") or 0
    if excluded:
        notes += (f"\n> 解读类过程事实 {excluded} 项不进图"
                  "(定性/基础缺口/假设种子;全文见台账与附录 A)。")
    titles = ("E.1 实体关系图", "E.2 复合事实 ⇒ 竞争假设",
              "E.3 证据 → 复合事实")
    body = "\n\n".join(f"### {t}\n\n" + b for t, b in zip(titles, parts))
    return "## 附录 E:证据图谱\n\n" + _LEGEND + "\n\n" + body + "\n" + notes


def fence_mermaid(text: str) -> str:
    """mermaid 围栏;占位说明(非 flowchart)用引用块(占位进围栏=渲染报错)。"""
    if text.lstrip().startswith("flowchart"):
        return "```mermaid\n" + text + "\n```"
    return "> " + text.replace("\n", "\n> ")


def graph_diagrams(payload: dict) -> list[tuple[str, str]]:
    """三图清单(html 卡片按序嵌入;图 A 恒在,B1/B2 可能是占位说明)。"""
    return [("图 A 实体关系图(实线=已证实/虚线=待证实/红=矛盾)",
             render_relations(payload)),
            ("图 B1 复合事实 ⇒ 竞争假设(研判的证据面)",
             render_b1(payload)),
            ("图 B2 证据 → 复合事实(查实的证据面)",
             render_b2(payload))]


def payload_to_json(payload: dict) -> str:
    """档2 复用锁:payload 可 JSON 往返(ECharts option 的数据源)。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
