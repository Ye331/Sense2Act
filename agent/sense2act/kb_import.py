# -*- coding: utf-8 -*-
"""K3v1 内部知识上传通道(2026-10-27;K5 规则卡=路径③,同日)——台账→对象卡
批量 + md 文档档1登记 + 规则表→规则卡。

蓝图=《实施方案/企业知识库-实施方案-20261025.md》§六(四条加工路径×
认识论四分表)。本包范围(v1):
- 路径① 结构化台账(CSV/JSON)→ 对象卡批量(**确定性转换器,零 LLM**,
  §6.2"最便宜最高价值,第一版必做"):列→attributes,行→卡;
- 路径② 凭证/制度文档(md,档1 原生文本)→ 文档卡+原件指纹入
  evidence_index(source_type=internal;**拆解层=条款 claim 的 LLM 提议+
  人确认押后**,v1 只登记原件——照取证链纪律"原件永不改写");
- 路径③ 规则/标准(K5,2026-10-27)→ 规则卡上传件:谓词/阈值全在表
  (数值进表纪律),白名单查 kb_rules.json rule_check(开放注册,改表
  即生效);**存储=独立规则表 rule_card,非对象卡**(§三要点6"尺子≠
  证据";规则本身不参与三态,比对表每格走三态=kb_check.py);
- 路径④ 专家经验=human 通道(既有 adjudicate)——不在本包。

存储形态:**上传件=源数据落 `data/kb_uploads/*.json`**(非派生视图——
可全量重建的只有视图,上传件在重派生后存活);kb-derive 按排序 glob 读入
合并进 kb.db(照 signal manifest 合并先例)。敏感级全程随行(§6.3 认识论
四分):文档默认 internal——K2 检索器只出 public=「敏感件不进 prompt」
红线的消费侧闸门。

自我卡(K5 附带,蓝图 §三要点5):上传件 entity 带 `"self": true` →
entity_id 固定为 rule_check.self_entity_id(ent_self)——资质核对表=
招标要求×自我卡 corpus 链的挂靠点;显式声明,未配置即报错(零兜底常量)。
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from .store.ledger import alias_key, simhash, text_key

UPLOAD_VERSION = "kb_upload_v1"


class KbImportError(Exception):
    """上传件解析/校验错误(缺必填列/格式不合法)。"""


def _ent_id(canonical: str) -> str:
    return "ent_" + hashlib.sha256(
        alias_key(canonical).encode("utf-8")).hexdigest()[:10]


def _doc_id(title: str) -> str:
    return "doc_" + hashlib.sha256(
        alias_key(title).encode("utf-8")).hexdigest()[:10]


def _rule_id(name: str) -> str:
    return "rul_" + hashlib.sha256(
        alias_key(name).encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------- 导入(读源→写上传件)

def import_ledger(path: str | Path, upload_dir: str | Path = "data/kb_uploads",
                  kind: str = "实体") -> Path:
    """台账(CSV/JSON)→ 对象卡上传件。canonical 必填列;aliases 列分号分隔;
    其余列全量进 attributes(值一律字符串,确定性)。幂等:同名上传件覆盖。"""
    p = Path(path)
    rows: list[dict]
    if p.suffix.lower() == ".json":
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        rows = doc if isinstance(doc, list) else doc.get("rows", [])
    else:
        with p.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    entities = []
    for i, row in enumerate(rows):
        canonical = (row.get("canonical") or "").strip()
        if not canonical:
            raise KbImportError(f"台账第 {i} 行缺 canonical 列: {p}")
        aliases = [a.strip() for a in (row.get("aliases") or "").split(";")
                   if a.strip()]
        attributes = {k: str(v) for k, v in row.items()
                      if k not in ("canonical", "aliases") and v not in (None, "")}
        entities.append({"canonical": canonical, "aliases": aliases,
                         "attributes": attributes, "kind": kind})
    out = Path(upload_dir) / f"{p.stem}.json"
    _write_upload(out, {"type": "ledger", "entities": entities,
                        "source_file": p.name})
    return out


def import_doc(path: str | Path, upload_dir: str | Path = "data/kb_uploads",
               sensitivity: str = "internal", title: str = "") -> Path:
    """md/文本文档 → 文档卡+原件指纹上传件(档1;原件即文本本身)。"""
    p = Path(path)
    content = p.read_text(encoding="utf-8-sig")
    if not content.strip():
        raise KbImportError(f"文档为空: {p}")
    doc_title = title.strip() or p.stem
    out = Path(upload_dir) / f"doc_{p.stem}.json"
    _write_upload(out, {
        "type": "document",
        "documents": [{
            "title": doc_title, "content": content,
            "sensitivity": sensitivity,
            "source": "internal_upload", "author_type": "内部上传",
            "origin_ref": f"upload:doc_{p.stem}",
            "url": "", "observed_at": "",
        }],
        "source_file": p.name})
    return out


def _rule_check_params(config_dir: str | Path) -> dict:
    """读 kb_rules.json rule_check 白名单(K5;开放注册=改表即生效)。"""
    p = Path(config_dir) / "kb_rules.json"
    try:
        rc = json.loads(p.read_text(encoding="utf-8-sig")).get("rule_check")
    except (OSError, ValueError) as exc:
        raise KbImportError(f"kb_rules 不可读: {p} ({exc})") from exc
    if not isinstance(rc, dict):
        raise KbImportError(f"kb_rules 缺 rule_check 节: {p}")
    return rc


def _normalize_rule_items(items: list, card_name: str, rc: dict) -> list[dict]:
    """规则条目规范化+校验(确定性:值一律字符串,rid 缺省按序派生)。

    校验即白名单消费:predicate ∈ rule_check.predicates、kind ∈
    item_kinds(缺省 default_item_kind)——清单外显式拒绝(失败不伪装),
    改 kb_rules.json 即开放新谓词。"""
    preds = rc.get("predicates") or []
    kinds = rc.get("item_kinds") or []
    dkind = rc.get("default_item_kind") or (kinds[0] if kinds else "")
    out, seen_rids = [], set()
    for i, raw in enumerate(items):
        it = dict(raw or {})
        field = str(it.get("field") or "").strip()
        pred = str(it.get("predicate") or "").strip()
        value = it.get("value")
        value = "" if value is None else str(value).strip()
        if not field or not pred or not value:
            raise KbImportError(
                f"规则卡「{card_name}」第 {i+1} 条缺 field/predicate/value")
        if pred not in preds:
            raise KbImportError(
                f"规则卡「{card_name}」第 {i+1} 条谓词不在白名单: {pred}"
                f"(清单={preds};开放注册改 kb_rules.json rule_check.predicates)")
        kind = str(it.get("kind") or "").strip() or dkind
        if kind not in kinds:
            raise KbImportError(
                f"规则卡「{card_name}」第 {i+1} 条种类不在白名单: {kind}"
                f"(清单={kinds})")
        rid = str(it.get("rid") or "").strip() or f"r{i+1:03d}"
        if rid in seen_rids:
            raise KbImportError(f"规则卡「{card_name}」rid 重复: {rid}")
        seen_rids.add(rid)
        text = str(it.get("text") or "").strip() \
            or f"{field} {pred} {value}"
        out.append({"rid": rid, "kind": kind, "field": field,
                    "predicate": pred, "value": value, "text": text})
    return out


def import_rules(path: str | Path, upload_dir: str | Path = "data/kb_uploads",
                 name: str = "", config_dir: str | Path = "data/config") -> Path:
    """规则表(JSON/CSV)→ 规则卡上传件(路径③;确定性零 LLM,§6.2)。

    JSON 三形态:{name,items} 单卡 / {rules:[{name,items},…]} 多卡 /
    裸条目列表(卡名=name 或文件名)。CSV 每行一条(列 rid/kind/field/
    predicate/value/text;卡名=name 或文件名)。"""
    rc = _rule_check_params(config_dir)
    p = Path(path)
    cards: list[dict] = []
    if p.suffix.lower() == ".json":
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
        if isinstance(doc, list):
            cards = [{"name": name.strip() or p.stem, "items": doc}]
        elif isinstance(doc, dict) and isinstance(doc.get("rules"), list):
            for c in doc["rules"]:
                cards.append({"name": str(c.get("name") or "").strip()
                              or name.strip() or p.stem,
                              "items": c.get("items") or []})
        elif isinstance(doc, dict) and isinstance(doc.get("items"), list):
            cards = [{"name": (name.strip() or str(doc.get("name") or "").strip()
                               or p.stem), "items": doc["items"]}]
        else:
            raise KbImportError(f"规则表 JSON 形态不识别(须 items/rules/列表): {p}")
    else:
        with p.open(encoding="utf-8-sig", newline="") as f:
            cards = [{"name": name.strip() or p.stem,
                      "items": list(csv.DictReader(f))}]
    rules_out = []
    for card in cards:
        if not card["name"]:
            raise KbImportError(f"规则卡缺名(--name 或 JSON name 字段): {p}")
        items = _normalize_rule_items(card["items"], card["name"], rc)
        if not items:
            raise KbImportError(f"规则卡「{card['name']}」零条目: {p}")
        rules_out.append({"name": card["name"], "items": items})
    out = Path(upload_dir) / f"rules_{p.stem}.json"
    _write_upload(out, {"type": "rulecard", "rules": rules_out,
                        "source_file": p.name})
    return out


def _write_upload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"kind": UPLOAD_VERSION, **payload}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- 合并(上传件→kb 聚合)

def load_uploads(upload_dir: str | Path) -> list[dict]:
    """读取上传件目录(排序 glob,确定性);坏件显式报错(失败不伪装)。"""
    d = Path(upload_dir)
    if not d.exists():
        return []
    ups = []
    for p in sorted(d.glob("*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise KbImportError(f"上传件不可读: {p} ({exc})") from exc
        if doc.get("kind") != UPLOAD_VERSION:
            raise KbImportError(f"上传件版本不识别: {p} ({doc.get('kind')})")
        doc["_name"] = p.name
        ups.append(doc)
    return ups


def merge_uploads(entities: dict, links: list[dict], evidences: list[dict],
                  uploads: list[dict], rule_params: dict | None = None
                  ) -> list[dict]:
    """上传件并入 kb 聚合(在 write_kb 的 per-event 归并之后调用)。

    文档指纹与既有证据同文→自然并成同一条 evidence_index 行(origin_refs
    并集,十家转载=一根的同款聚合语义)。

    K5(2026-10-27):①entity 带 `self: true` → 归 rule_check.self_entity_id
    固定 id(未配置即报错,零兜底);②type=rulecard 上传件→规则卡聚合,
    **返回值=规则卡列表**(调用方写 rule_card 表);rule_params 提供时对
    条目做第二道白名单校验(防白名单收紧后的老上传件,改表即生效)。"""
    self_eid = (rule_params or {}).get("self_entity_id") or ""
    rc = rule_params if isinstance(rule_params, dict) and rule_params else None
    rules_out: dict[str, dict] = {}
    for up in uploads:
        for e in sorted(up.get("entities") or [],
                        key=lambda x: x.get("canonical", "")):
            canonical = (e.get("canonical") or "").strip()
            if not canonical:
                raise KbImportError(f"上传件 {up.get('_name')} 有空 canonical")
            if e.get("self"):
                if not self_eid:
                    raise KbImportError(
                        f"上传件 {up.get('_name')} 声明 self=true 但未配置"
                        f" self_entity_id(kb_rules.json rule_check)")
                eid = self_eid
            else:
                eid = _ent_id(canonical)
            cur = entities.get(eid)
            if cur is None:
                entities[eid] = {
                    "entity_id": eid, "kind": e.get("kind") or "实体",
                    "names": {"canonical": canonical,
                              "aliases": sorted(set(e.get("aliases") or []))},
                    "attributes": dict(e.get("attributes") or {}),
                    "created_at": e.get("created_at", "")}
            else:
                cur["names"]["aliases"] = sorted(
                    set(cur["names"].get("aliases") or [])
                    | set(e.get("aliases") or []))
                for k, v in (e.get("attributes") or {}).items():
                    cur["attributes"].setdefault(k, v)
        for c in sorted(up.get("rules") or [],
                        key=lambda x: x.get("name", "")):
            cname = str(c.get("name") or "").strip()
            items = c.get("items") or []
            if not cname or not items:
                raise KbImportError(
                    f"上传件 {up.get('_name')} 规则卡缺 name/items")
            if rc is not None:
                items = _normalize_rule_items(items, cname, rc)
            else:
                items = [dict(x) for x in items]
            rid = _rule_id(cname)
            body = {"rule_id": rid, "name": cname, "items": items,
                    "source_file": up.get("source_file", ""),
                    "created_at": ""}
            prev = rules_out.get(rid)
            if prev is not None and prev["items"] != body["items"]:
                raise KbImportError(
                    f"规则卡同名异文(规则名={cname};同名上传件请合并或改名)")
            rules_out[rid] = body
        for d in sorted(up.get("documents") or [],
                        key=lambda x: x.get("title", "")):
            title = (d.get("title") or "").strip()
            content = d.get("content") or ""
            if not title or not content.strip():
                raise KbImportError(
                    f"上传件 {up.get('_name')} 文档缺 title/content")
            sensitivity = d.get("sensitivity") or "internal"
            if sensitivity not in ("public", "internal"):
                raise KbImportError(
                    f"上传件 {up.get('_name')} sensitivity 非法: {sensitivity}")
            fp = text_key(f"{title}|{content}")
            did = _doc_id(title)
            if did not in entities:
                entities[did] = {
                    "entity_id": did, "kind": "文档",
                    "names": {"canonical": title, "aliases": []},
                    "attributes": {"sensitivity": sensitivity,
                                   "source_file": up.get("source_file", "")},
                    "created_at": d.get("observed_at", "")}
            links.append({"entity_id": did, "ref_kind": "evidence",
                          "ref_id": fp, "zone": "",
                          "note": d.get("origin_ref") or "上传原件"})
            evidences.append({
                "fingerprint": fp,
                "lineage_roots": ["internal_upload"],
                "origin_refs": [d.get("origin_ref")
                                or f"upload:{up.get('_name', 'x')}"],
                "source_type": "internal",
                "sensitivity": sensitivity,
                "observed_at": d.get("observed_at", ""),
                "url": d.get("url", ""),
                "simhash": simhash(content),
                "title": title, "content": content,
                "source": d.get("source") or "internal_upload",
                "author_type": d.get("author_type") or "内部上传",
            })
    return [rules_out[k] for k in sorted(rules_out)]
