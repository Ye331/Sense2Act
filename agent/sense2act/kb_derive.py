# -*- coding: utf-8 -*-
"""知识库派生写入器(K1,2026-10-26)——台账 → 对象卡/案卷卡,确定性纯函数。

蓝图=《实施方案/企业知识库-实施方案-20261025.md》§3.1:发现→归一→挂载;
§二 四则入卡门槛 + 种子质量闸。纪律:

- **幂等纯函数**:同一批源产物派生两遍 kb.db 逐字节一致——所有时间戳取自
  源(events/evidence/meta),本模块禁止取 now();套件按 event_id 排序派生;
- **零 LLM**:发现 v1 只走三个确定性来源(信封结构化字段/台账 claim_entities
  join/watchlist·别名表规范化子串命中,§3.1),LLM 辅助四环节全部押后(§3.2);
- **对主库纯只读**(mode=ro URI 打开,open_main_ro);
- **分权**:入卡门槛全机械判据(source_gate 查 kb_rules.json,零硬编码);
- 泄漏墙(§四红线):本模块只是派生器;bench 主读数永远 KB-off(K2 才有
  KB-on 通道)。

链(compute_chain)=查询时现算,在 kb_chain.py(C1,2026-10-26);本模块
C1 起兼产 OQ 挂载(ref_kind=oq,方案 §六-8——派生期唯一新增,链侧消费)。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .corpus_bound import _strip_heads
from .kb_store import KbStore
from .models.envelope import InvestigationEnvelope
from .run_meta import META_KEYS, read_run_meta
from .store.ledger import alias_key, text_key

# 合格 source_type(来源则"非信封/非种子"的机械语义);signal_seed=复述族
_ELIGIBLE_TYPES = ("crawler", "internal", "human")
_TYPE_PRIORITY = ("crawler", "internal", "human", "signal_seed")


class KbDeriveError(Exception):
    """派生失败(源缺失/规则不合法/案例运行失败)。"""


# ---------------------------------------------------------------- 规则装载

def load_rules(path: str | Path, config_dir: str | Path = "data/config") -> dict:
    """加载并校验 kb_rules.json;病根族清单同源解析(trigger_patterns_source)。

    校验即契约(照 credibility.load_rules 先例):version 非空、卡种注册
    非空且 derived⊆card、source_gate 四参数齐、状态分区映射齐;病根族清单
    不复制进本表,按 <文件>:<键> 指针从 credibility_rules.json 现读
    (同源纪律:清单只有一份)。
    """
    p = Path(path)
    if not p.exists():
        raise KbDeriveError(f"kb_rules 不存在: {p}")
    try:
        rules = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise KbDeriveError(f"kb_rules 不可读: {exc}") from exc
    if not isinstance(rules, dict):
        raise KbDeriveError("kb_rules 顶层必须是对象")
    if not isinstance(rules.get("version"), str) or not rules["version"]:
        raise KbDeriveError("kb_rules 缺 version")
    kinds = rules.get("card_kinds")
    if (not isinstance(kinds, list) or not kinds
            or not all(isinstance(k, str) for k in kinds)):
        raise KbDeriveError("card_kinds 必须是非空字符串列表(卡种开放注册)")
    dk = rules.get("derived_kinds")
    if (not isinstance(dk, list) or not dk
            or any(k not in kinds for k in dk)):
        raise KbDeriveError("derived_kinds 必须非空且 ⊆ card_kinds")
    gate = rules.get("source_gate")
    if not isinstance(gate, dict):
        raise KbDeriveError("缺 source_gate")
    if not isinstance(gate.get("eligible_round_min"), int) \
            or gate["eligible_round_min"] < 0:
        raise KbDeriveError("source_gate.eligible_round_min 必须是非负整数")
    for key in ("eligible_lineage_roots", "eligible_platforms"):
        v = gate.get(key)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise KbDeriveError(f"source_gate.{key} 必须是字符串列表")
    if not isinstance(gate.get("browser_marker"), str):
        raise KbDeriveError("source_gate.browser_marker 必须是字符串")
    sg = rules.get("substance_gate")
    if sg is not None:
        if not isinstance(sg, dict) or \
                not isinstance(sg.get("exclude_self_contained"), bool):
            raise KbDeriveError(
                "substance_gate.exclude_self_contained 必须是布尔"
                "(自证复述族出局开关,2026-10-26 拍板)")
    zones = rules.get("zones")
    if not isinstance(zones, dict):
        raise KbDeriveError("缺 zones")
    for key in ("正卡", "未决"):
        v = zones.get(key)
        if not isinstance(v, list) or not v:
            raise KbDeriveError(f"zones.{key} 必须是非空列表(状态→分区映射)")
    if not isinstance(zones.get("待复核"), str) or not zones["待复核"]:
        raise KbDeriveError("zones.待复核 必须是非空字符串(降权分区名)")
    ret = rules.get("retrieval")
    if not isinstance(ret, dict) or not isinstance(ret.get("hits_max"), int) \
            or ret["hits_max"] < 1 or not isinstance(ret.get("simhash_dist"), int) \
            or ret["simhash_dist"] < 0:
        raise KbDeriveError("retrieval.hits_max≥1/simhash_dist≥0 必须为整数"
                            "(K2 检索参数,照数值参数从规则表消费纪律)")
    rc = rules.get("rule_check")
    if not isinstance(rc, dict) \
            or not isinstance(rc.get("predicates"), list) \
            or not rc["predicates"] \
            or not isinstance(rc.get("item_kinds"), list) \
            or not rc["item_kinds"] \
            or not isinstance(rc.get("self_entity_id"), str) \
            or not rc["self_entity_id"]:
        raise KbDeriveError("rule_check.predicates/item_kinds/self_entity_id"
                            " 必须非空(K5 规则卡比对参数,开放注册改表即生效)")
    ref = rules.get("trigger_patterns_source")
    if not isinstance(ref, str) or ":" not in ref:
        raise KbDeriveError("trigger_patterns_source 必须是 <文件>:<键> 指针")
    fname, key = ref.split(":", 1)
    cpath = Path(config_dir) / fname
    try:
        conf = json.loads(cpath.read_text(encoding="utf-8-sig"))
        patterns = conf[key]
    except (OSError, ValueError, KeyError) as exc:
        raise KbDeriveError(f"病根族清单解析失败({ref}): {exc}") from exc
    if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
        raise KbDeriveError(f"病根族清单必须是字符串列表: {ref}")
    rules["_trigger_patterns"] = patterns
    # 归一口径配置(aliases/watchlist 同源载入;名字命中消费)
    apath = Path(config_dir) / "aliases.json"
    wpath = Path(config_dir) / "watchlist.json"
    try:
        rules["_aliases"] = json.loads(apath.read_text(encoding="utf-8-sig"))
        rules["_watchlist"] = json.loads(wpath.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise KbDeriveError(f"别名/watchlist 配置不可读: {exc}") from exc
    return rules


# ---------------------------------------------------------------- 机械判据

def _ent_id(canonical: str) -> str:
    """实体卡 id=主库 ensure_entity 同款 scheme(跨库可对照)。"""
    return "ent_" + hashlib.sha256(
        alias_key(canonical).encode("utf-8")).hexdigest()[:10]


def _prj_id(project_id: str) -> str:
    return "prj_" + hashlib.sha256(
        alias_key(project_id).encode("utf-8")).hexdigest()[:10]


def _dos_id(event_id: str) -> str:
    return "dos_" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:10]


def source_type_of(ev: dict, gate: dict) -> str:
    """证据来源四分类(全机械,kb_rules.source_gate 查表;零 LLM)。

    采集=round≥门槛 ∨ 浏览器捕获(author_type 含标记;capture_cli 也插
    round=0,round 单独不作判据);人答=lineage_root=human;
    内部历史=platform 白名单;其余(信封种子 signal_layer/线索卡 round=0
    复述族)=signal_seed。
    """
    if (ev.get("lineage_root") or "") == "human":
        return "human"
    if ev.get("platform") in gate["eligible_platforms"]:
        return "internal"
    if ev.get("round", 0) >= gate["eligible_round_min"]:
        return "crawler"
    marker = gate.get("browser_marker") or ""
    if marker and marker in (ev.get("author_type") or ""):
        return "crawler"
    return "signal_seed"


def is_eligible(ev: dict, gate: dict) -> bool:
    """来源则合格根谓词:采集∨内部∨人答(复述族出局,案卷卡兜底)。"""
    return source_type_of(ev, gate) in _ELIGIBLE_TYPES


def _envelope_entity_names(envelope: InvestigationEnvelope) -> list[str]:
    """发现来源①:信封结构化字段(tender.purchaser / rumor.subject_name)。"""
    names: list[str] = []
    if envelope.kind == "tender" and envelope.tender and envelope.tender.purchaser:
        names.append(envelope.tender.purchaser)
    if envelope.kind == "rumor" and envelope.rumor and envelope.rumor.subject_name:
        names.append(envelope.rumor.subject_name)
    return names


def _known_names(rules: dict,
                 envelope: InvestigationEnvelope) -> dict[str, str]:
    """归一名字全集(发现来源③):别名表+watchlist+信封实体名。

    键=alias_key(名字)(NFKC+casefold,主库锚定同口径);信封/watchlist
    名字**先过别名表归一**再注册(否则信封俗称与台账 canonical 出同实体
    双卡——B公司 vs B新能源科技有限公司 实证);<2 字符键丢弃防子串误命中。
    归一 v1 只走精确键命中,近似/仲裁押后(§3.2)。
    """
    aliases = rules.get("_aliases") or {}

    def _canon(name: str) -> str:
        return aliases.get(alias_key(name), name)

    out: dict[str, str] = {}
    for a, canon in aliases.items():
        out[alias_key(a)] = canon
    for w in (rules.get("_watchlist") or []):
        if isinstance(w, dict) and w.get("canonical_name"):
            canon = _canon(w["canonical_name"])
            out[alias_key(w["canonical_name"])] = canon
            out[alias_key(canon)] = canon
    for n in _envelope_entity_names(envelope):
        canon = _canon(n)
        out[alias_key(n)] = canon      # 俗称键也指到归一 canonical
        out[alias_key(canon)] = canon
    return {k: v for k, v in out.items() if len(k) >= 2}


# ---------------------------------------------------------------- 派生(纯读)

_CLAIM_SQL = (
    "SELECT claim_id, draft_id, text, category, status, priority,"
    " corpus_bound, unreachable, created_round FROM claims"
    " WHERE event_id=? ORDER BY created_round, draft_id")
_FA_SQL = (
    "SELECT e.evidence_id, e.lineage_root, e.platform, e.round,"
    " e.author_type, e.sensitive, e.published_at, e.url, e.title,"
    " e.content, e.simhash, ce.ruling"
    " FROM evidence e JOIN claim_evidence ce ON ce.evidence_id=e.evidence_id"
    " WHERE ce.claim_id=?")
_ENT_SQL = (
    "SELECT ce.entity_id, e.canonical_name, e.anchor_status, e.credit_code"
    " FROM claim_entities ce JOIN entities e ON e.entity_id=ce.entity_id"
    " WHERE ce.claim_id=?")
_EVIDENCE_SQL = (
    "SELECT evidence_id, lineage_root, platform, round, author_type,"
    " sensitive, published_at, url, title, content, simhash"
    " FROM evidence WHERE event_id=? ORDER BY evidence_id")
_CEV_SQL = (
    "SELECT ce.claim_id AS claim_id, e.title AS title, e.content AS content"
    " FROM claim_evidence ce JOIN evidence e ON e.evidence_id=ce.evidence_id"
    " WHERE e.event_id=?")


def open_main_ro(path: str | Path) -> sqlite3.Connection:
    """主库只读打开(mode=ro URI;_ess_v2v3.load_ro 先例)。"""
    p = Path(path)
    if not p.exists():
        raise KbDeriveError(f"主库不存在: {p}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def derive_event(main_conn: sqlite3.Connection, event_id: str, rules: dict,
                 meta: dict | None = None,
                 pipeline_note: str = "") -> dict:
    """单事件派生(纯读主库 → 返回平铺数据;不写任何库)。

    返回 {"entities", "links", "evidence_contribs", "anchor", "stats"}。
    四则门槛:关联则(实体命中)∧来源则(合格根)∧状态则(查 zones 表);
    兜底则=案卷卡全量(每条 claim 无条件挂案卷卡,一条不丢)。
    种子质量闸:正卡区∧run meta predicate_check≠on∧文本命中病根族→待复核。
    """
    gate = rules["source_gate"]
    zones = rules["zones"]
    review_zone = zones["待复核"]
    kb_version = rules["version"]
    patterns = rules["_trigger_patterns"]
    pc_on = bool((meta or {}).get("predicate_check"))
    # 自证复述族出局(用户 10-26 拍板"彻底出局"):对象卡只收世界断言。
    # 判据全机械:corpus_bound 代码判定列 ∨ 框架词前缀(_strip_heads 同源
    # ——文本被剥头=曾带"信封载明/线索卡称/公告载明…"复述前缀)。
    sub_gate = rules.get("substance_gate") or {}
    excl_self = bool(sub_gate.get("exclude_self_contained", True))

    ev_row = main_conn.execute(
        "SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
    if ev_row is None:
        raise KbDeriveError(f"事件不存在: {event_id}")
    envelope = InvestigationEnvelope.model_validate(
        json.loads(ev_row["envelope_json"]))
    known = _known_names(rules, envelope)

    # 案卷卡(兜底则:全量 claims,一条不丢;zone 留空=不分区)
    dossier_id = _dos_id(event_id)
    entities: dict[str, dict] = {
        dossier_id: {
            "entity_id": dossier_id, "kind": "案卷",
            "names": {"canonical": event_id, "aliases": []},
            "attributes": {"event_kind": ev_row["kind"],
                           "signal_type": ev_row["signal_type"],
                           "status": ev_row["status"]},
            "created_at": ev_row["created_at"],
        }}
    links: list[dict] = [{
        "entity_id": dossier_id, "ref_kind": "event", "ref_id": event_id,
        "zone": "", "note": "案卷卡主链接"}]
    touched: set[str] = set()
    object_linked: set[str] = set()
    n_object = 0
    n_dossier_claims = 0

    def _register(canonical: str, **attrs) -> str:
        eid = _ent_id(canonical)
        if eid not in entities:
            entities[eid] = {
                "entity_id": eid, "kind": "实体",
                "names": {"canonical": canonical, "aliases": []},
                "attributes": dict(attrs),
                "created_at": ev_row["created_at"]}
        elif attrs:
            merged = dict(entities[eid]["attributes"])
            for k, v in attrs.items():
                merged.setdefault(k, v)
            entities[eid]["attributes"] = merged
        return eid

    for c in (dict(r) for r in main_conn.execute(_CLAIM_SQL, (event_id,))):
        # 兜底则:案卷全量,一条不丢
        links.append({"entity_id": dossier_id, "ref_kind": "claim",
                      "ref_id": c["claim_id"], "zone": "",
                      "note": f"案卷全量·{c['draft_id']}"})
        n_dossier_claims += 1
        fa = [dict(r) for r in main_conn.execute(_FA_SQL, (c["claim_id"],))]
        has_eligible = any(is_eligible(r, gate) for r in fa)
        # 状态则(查表):已证实/矛盾→正卡;待证实∧合格根→未决;其余不入对象卡
        zone = ""
        if c["status"] in zones["正卡"] and has_eligible:
            zone = "正卡"
        elif c["status"] in zones["未决"] and has_eligible:
            zone = "未决"
        # 种子质量闸(§二):正卡∧predicate_check off∧病根族→降权待复核
        if zone == "正卡" and not pc_on and any(
                pat in (c["text"] or "") for pat in patterns):
            zone = review_zone
        # 自证出局(substance_gate):"信封载明/线索卡称…"复述型与语料内
        # 自证型不是世界断言——零对象卡链接(案卷卡兜底不变)
        if zone and excl_self and (
                c["corpus_bound"] or _strip_heads(c["text"] or "") != (c["text"] or "")):
            zone = ""
        # 关联则:主语实体归一命中(来源②台账 join + 来源③封闭名表子串)。
        # 卡注册延迟到 zone 判定后——零链接噪音卡不入库(未过门槛的 claim
        # 只留案卷链接,不为它生卡)
        cands: dict[str, dict] = {}
        for r in main_conn.execute(_ENT_SQL, (c["claim_id"],)):
            cands.setdefault(r["canonical_name"], {
                "anchor_status": r["anchor_status"],
                "credit_code": r["credit_code"] or ""})
        tk = alias_key(c["text"] or "")
        for k, canon in known.items():
            if k in tk:
                cands.setdefault(canon, {})
        if zone and cands:
            ent_ids = {_register(canon, **attrs)
                       for canon, attrs in sorted(cands.items())}
            for eid in sorted(ent_ids):
                links.append({"entity_id": eid, "ref_kind": "claim",
                              "ref_id": c["claim_id"], "zone": zone,
                              "note": c["draft_id"]})
                touched.add(eid)
                n_object += 1
            object_linked.add(c["claim_id"])
    # 案卷卡↔实体卡互链(挂载判定:run 处理过该实体)
    for eid in sorted(touched):
        links.append({"entity_id": eid, "ref_kind": "event",
                      "ref_id": event_id, "zone": "", "note": "案卷互链"})

    # OQ 挂载(C1 配套,2026-10-26;方案《…链compute_chain-设计方案…》§六-8):
    # 三态纪律"查不到→Open Questions"在卡上的投影——**未过状态则**的待证实
    # claim(无合格根,典型=查过没有/unreachable;已过门槛进未决区的不重复
    # 挂)以 ref_kind=oq 挂到其主语实体卡,供话题链 OQ 段反查。判据=锚定
    # join(蓝图 §3.1"OQ 主语实体";"精确键命中"的落地口径:canonical=
    # 别名表精确键锚定产物,相对子串宽进从严——探针实证 bench 20 案 182 条
    # 待证实:全等=0/锚定 join=134/子串=75,全等是死判据)。只挂已注册实体卡
    # (零链接噪音卡不注册纪律的延伸:oq 不生新卡,案卷卡全量兜底一条不丢);
    # 不叠 substance_gate——自证复述型待证实也是"没查清的"(OQ=待查清单,
    # 非对象卡 claim 链接)。
    n_oq = 0
    for pr in main_conn.execute(
            "SELECT claim_id, draft_id FROM claims"
            " WHERE event_id=? AND status='待证实'", (event_id,)):
        if pr["claim_id"] in object_linked:
            continue
        joins = {r["canonical_name"] for r in main_conn.execute(_ENT_SQL,
                                                                (pr["claim_id"],))}
        for canon in sorted(joins):
            eid = _ent_id(canon)
            card = entities.get(eid)
            if card is not None and card["kind"] == "实体":
                links.append({"entity_id": eid, "ref_kind": "oq",
                              "ref_id": pr["claim_id"], "zone": "",
                              "note": f"未决·{pr['draft_id']}"})
                n_oq += 1

    # 项目锚(蓝图 §三要点4:外部稳定 ID=events.project_id 续查路由键;
    # 同 pno 跨信封跨 run 由同一 prj_ 卡聚合)
    if ev_row["project_id"]:
        pid = _prj_id(ev_row["project_id"])
        tender = envelope.tender if envelope.kind == "tender" else None
        pno = ""
        if tender is not None and tender.project_name:
            from .investigation import _extract_project_no
            pno = _extract_project_no(tender)
        pid_raw = ev_row["project_id"]
        aliases = sorted({a for a in (pid_raw, pno) if a and a != pid_raw})
        entities[pid] = {
            "entity_id": pid, "kind": "项目",
            "names": {"canonical": ev_row["project_id"], "aliases": aliases},
            "attributes": {
                "purchaser": tender.purchaser if tender else "",
                "published_date": tender.published_date if tender else "",
                "source_url": tender.source_url if tender else "",
            },
            "created_at": ev_row["created_at"]}
        links.append({"entity_id": pid, "ref_kind": "event",
                      "ref_id": event_id, "zone": "", "note": "项目锚"})

    # 指纹索引贡献(evidence_index 跨事件聚合在 write 阶段做;
    # title/content/source/author_type=内容快照,K2 运行时零主库依赖——
    # 证据行 append-only 不可变,快照≠状态拷贝,铁律只禁拷贝状态)
    contribs = []
    for r in (dict(r) for r in main_conn.execute(_EVIDENCE_SQL, (event_id,))):
        contribs.append({
            "fingerprint": text_key(f"{r['title'] or ''}|{r['content'] or ''}"),
            "lineage_root": r["lineage_root"] or "",
            "origin_ref": r["evidence_id"],
            "source_type": source_type_of(r, gate),
            "sensitivity": "internal" if r["sensitive"] else "public",
            "observed_at": r["published_at"] or "",
            "url": r["url"] or "",
            "simhash": r["simhash"] or "",
            "title": r["title"] or "",
            "content": r["content"] or "",
            "source": r["platform"] or "",
            "author_type": r["author_type"] or "",
        })

    # 管道版本标注(kb_anchor;flags=META_KEYS∩meta,时间戳全取自源)
    flags = {k: meta[k] for k in sorted(META_KEYS)
             if meta and k in meta and meta[k] is not None}
    anchor = {
        "event_id": event_id, "kb_version": kb_version,
        "snapshotted_at": (meta or {}).get("recorded_at")
        or ev_row["updated_at"],
        "flags": flags, "pipeline_note": pipeline_note,
    }
    # claim→证据关联快照(K2 检索器自包含前提;关联 append-only 非三态,
    # zone 同款快照逻辑——正本关联仍现场读主库)
    assoc: dict[str, set[str]] = {}
    for r in main_conn.execute(_CEV_SQL, (event_id,)):
        assoc.setdefault(r["claim_id"], set()).add(
            text_key(f"{r['title'] or ''}|{r['content'] or ''}"))
    claim_assocs = [{"claim_id": cid, "fps": sorted(fps)}
                    for cid, fps in sorted(assoc.items())]
    stats = {"event_id": event_id, "n_claims": n_dossier_claims,
             "n_object_links": n_object, "n_entities": len(entities) - 1,
             "n_oq_links": n_oq, "n_evidence": len(contribs)}
    return {"entities": list(entities.values()), "links": links,
            "evidence_contribs": contribs, "anchor": anchor,
            "claim_assocs": claim_assocs, "stats": stats}


# ---------------------------------------------------------------- 聚合写入

def _merge_entities(per_event: list[dict]) -> dict[str, dict]:
    """跨事件归并:created_at 取最早,aliases/attributes 并集(排序确定)。"""
    out: dict[str, dict] = {}
    for res in per_event:
        for e in res["entities"]:
            cur = out.get(e["entity_id"])
            if cur is None:
                out[e["entity_id"]] = {
                    "entity_id": e["entity_id"], "kind": e["kind"],
                    "names": {"canonical": e["names"]["canonical"],
                              "aliases": set(e["names"].get("aliases") or [])},
                    "attributes": dict(e.get("attributes") or {}),
                    "created_at": e.get("created_at", "")}
                continue
            cur["names"]["aliases"] |= set(e["names"].get("aliases") or [])
            for k, v in (e.get("attributes") or {}).items():
                cur["attributes"].setdefault(k, v)
            if e.get("created_at", "") < cur["created_at"]:
                cur["created_at"] = e["created_at"]
    for e in out.values():
        canon = e["names"]["canonical"]
        e["names"]["aliases"] = sorted(
            a for a in e["names"]["aliases"] if a != canon)
    return out


def _dedup_links(per_event: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for res in per_event:
        for l in res["links"]:
            key = (l["entity_id"], l["ref_kind"], l["ref_id"])
            if key not in seen:
                seen.add(key)
                out.append(l)
    return out


def _aggregate_evidence(per_event: list[dict]) -> list[dict]:
    """指纹聚合(蓝图 §三 evidence_index):同文跨 run=一行多 origin_ref。

    聚合口径全确定性:roots 并集排序;source_type 按合格度优先序取最合格;
    sensitivity 就严(任一 internal→internal);observed_at 取非空最大;
    url/simhash 取排序首个非空。"""
    groups: dict[str, list[dict]] = {}
    for res in per_event:
        for v in res["evidence_contribs"]:
            groups.setdefault(v["fingerprint"], []).append(v)
    out = []
    for fp in sorted(groups):
        rows = groups[fp]
        types = [r["source_type"] for r in rows]
        source_type = next(t for t in _TYPE_PRIORITY if t in types)
        urls = sorted(r["url"] for r in rows if r["url"])
        sims = sorted(r["simhash"] for r in rows if r["simhash"])
        times = sorted(r["observed_at"] for r in rows if r["observed_at"])
        # 内容快照:fingerprint=text_key(title|content) 等值⇒归一文本相同,
        # 排序首个=确定性选取(哈希碰撞除外,概率可忽略)
        def _first(key: str) -> str:
            vals = sorted({r[key] for r in rows if r.get(key)})
            return vals[0] if vals else ""

        out.append({
            "fingerprint": fp,
            "lineage_roots": sorted({r["lineage_root"] for r in rows
                                     if r["lineage_root"]}),
            "origin_refs": sorted(r["origin_ref"] for r in rows),
            "source_type": source_type,
            "sensitivity": ("internal" if any(
                r["sensitivity"] == "internal" for r in rows) else "public"),
            "observed_at": times[-1] if times else "",
            "url": urls[0] if urls else "",
            "simhash": sims[0] if sims else "",
            "title": _first("title"),
            "content": _first("content"),
            "source": _first("source"),
            "author_type": _first("author_type"),
        })
    return out


def write_kb(kb_path: str | Path, per_event: list[dict],
             signal_rows: list[dict], kb_version: str,
             uploads: list[dict] | None = None,
             rule_params: dict | None = None) -> dict:
    """聚合各事件派生结果 → 全量重建 kb.db(确定性:入参序=产出序)。

    rule_params=kb_rules.json rule_check 节(K5:自我卡 id+规则白名单
    二道校验;缺省=自我卡特性关闭,遇 self 声明显式报错)。"""
    entities = _merge_entities(per_event)
    links = _dedup_links(per_event)
    evidences = _aggregate_evidence(per_event)
    anchors = [res["anchor"] for res in per_event]
    # 关联快照去重(claim_id 含 event 前缀,跨事件天然唯一;同 id 冲突=
    # 派生 bug,keep-first 照 links 先例)
    seen_ca: set[str] = set()
    claim_assocs: list[dict] = []
    for res in per_event:
        for ca in res.get("claim_assocs", []):
            if ca["claim_id"] not in seen_ca:
                seen_ca.add(ca["claim_id"])
                claim_assocs.append(ca)
    # 信号挂卡(项目卡专属;manifest 行={signal_id, project_id, company_id,…})
    signal_links: list[dict] = []
    for row in sorted(signal_rows, key=lambda r: (r.get("project_id", ""),
                                                  r.get("signal_id", ""))):
        pid_raw = row.get("project_id") or ""
        sig = row.get("signal_id") or ""
        if not pid_raw or not sig:
            continue
        pid = _prj_id(pid_raw)
        if pid not in entities:
            entities[pid] = {
                "entity_id": pid, "kind": "项目",
                "names": {"canonical": pid_raw, "aliases": []},
                "attributes": {"company_id": row.get("company_id", "")},
                "created_at": ""}
        else:
            entities[pid]["attributes"].setdefault(
                "company_id", row.get("company_id", ""))
        signal_links.append({"entity_id": pid, "ref_kind": "signal",
                             "ref_id": sig, "zone": "",
                             "note": "信号到站挂卡(未调查可聚合)"})
    # 信号链接并入统一去重(同 manifest 重复传入/同信号多行→PK 冲突预防)
    seen: set[tuple] = {(l["entity_id"], l["ref_kind"], l["ref_id"])
                        for l in links}
    for l in signal_links:
        key = (l["entity_id"], l["ref_kind"], l["ref_id"])
        if key not in seen:
            seen.add(key)
            links.append(l)
    # K3v1 上传件合并(2026-10-27):上传件=源数据(非派生视图),在重派生
    # 后存活;排序入参保证确定性(照 signal_rows 先例)。K5:规则卡上传件
    # 并出规则聚合,写 rule_card 表(独立规则表,非对象卡)
    rule_rows: list[dict] = []
    if uploads:
        from .kb_import import merge_uploads
        rule_rows = merge_uploads(entities, links, evidences, uploads,
                                  rule_params=rule_params)
    store = KbStore.create(kb_path)
    store.write_all(list(entities.values()), links, evidences, anchors,
                    claim_assocs, kb_version, rules=rule_rows)
    return {"n_entities": len(entities), "n_links": len(links),
            "n_evidence": len(evidences), "n_anchors": len(anchors),
            "n_rules": len(rule_rows)}


# ---------------------------------------------------------------- 套件驱动

def _load_signal_rows(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        for r in doc.get("rows", []):
            if r.get("status") == "ok" or "signal_id" in r:
                rows.append(r)
    return rows


def derive_suite(suite_dir: str | Path, kb_path: str | Path,
                 rules_path: str | Path = "data/config/kb_rules.json",
                 config_dir: str | Path = "data/config",
                 signal_manifests: list[str] | None = None,
                 timeout: int = 180, log=print,
                 uploads_dir: str | Path = "data/kb_uploads") -> dict:
    """套件批量派生:录制库直读(真值口径),离线案零 LLM 回放后派生。

    通道照 evaluate 三通道:recorded_outputs_dir 命中→只读派生;否则
    run_case 回放进临时目录(零网络零 LLM)再派生。任一案例运行失败→
    如实计入 errors(失败不伪装),调用方决定退出码。
    uploads_dir=K3 上传件目录(排序 glob 合并;不存在=空,零差异)。
    """
    from . import evaluate as ev
    from .kb_import import load_uploads
    rules = load_rules(rules_path, config_dir)
    uploads = load_uploads(uploads_dir)
    _suite, cases = ev.load_suite(suite_dir)
    per_event: list[dict] = []
    errors: list[str] = []
    for case in sorted((c for c in cases if c.get("enabled", True)),
                       key=lambda c: c["event_id"]):
        cid = case["case_id"]
        meta = read_run_meta(ev.case_path(case, "responses")) or {}
        rec = ev.recorded_outputs_dir(case)
        work: Path | None = None
        try:
            if rec is not None:
                db_path, _runs = rec
                channel = "真值库"
            else:
                work = Path(tempfile.mkdtemp(prefix="s2a_kb_"))
                r = ev.run_case(case, work, timeout)
                if r["run_status"] not in ("ok", "budget_stop"):
                    errors.append(f"{cid}: 回放失败({r['run_status']})")
                    log(f"  - [ERR] {cid}: 回放失败({r['run_status']})")
                    continue
                db_path = Path(r["db"])
                channel = "回放"
            conn = open_main_ro(db_path)
            try:
                res = derive_event(conn, case["event_id"], rules, meta,
                                   pipeline_note=case.get("pipeline_note", ""))
            finally:
                conn.close()
            per_event.append(res)
            s = res["stats"]
            log(f"  - [{channel}] {cid}: claims={s['n_claims']} "
                f"对象链接={s['n_object_links']} 实体+项目={s['n_entities']} "
                f"证据={s['n_evidence']}")
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
    signal_rows = _load_signal_rows(list(signal_manifests or []))
    total = write_kb(kb_path, per_event, signal_rows, rules["version"],
                     uploads=uploads, rule_params=rules.get("rule_check"))
    total["n_cases"] = len(per_event)
    total["n_errors"] = len(errors)
    total["errors"] = errors
    return total


# ---------------------------------------------------------------- 单库入口

def derive_db(source_db: str | Path, event_id: str, kb_path: str | Path,
              rules_path: str | Path = "data/config/kb_rules.json",
              config_dir: str | Path = "data/config",
              record_path: str | Path | None = None,
              uploads_dir: str | Path = "data/kb_uploads") -> dict:
    """单库模式:一个主库一个事件 → kb.db(meta 从录制件旁车读,可缺)。"""
    from .kb_import import load_uploads
    rules = load_rules(rules_path, config_dir)
    meta = read_run_meta(record_path) if record_path else {}
    conn = open_main_ro(source_db)
    try:
        res = derive_event(conn, event_id, rules, meta or {})
    finally:
        conn.close()
    return write_kb(kb_path, [res], [], rules["version"],
                    uploads=load_uploads(uploads_dir),
                    rule_params=rules.get("rule_check"))
