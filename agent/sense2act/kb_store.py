# -*- coding: utf-8 -*-
"""kb.db 存储层(K1,2026-10-26;K5 规则卡表 2026-10-27)——知识库表 + 全量重建式写入。

蓝图=《实施方案/企业知识库-实施方案-20261025.md》§三(kb.db 草案):
独立 SQLite 库,与主库物理隔离,**对主库纯只读**;claim/evidence 正本仍在
主库,kb.db 只存身份+归一+引用。

铁律(蓝图 §二):卡和链是派生视图,只存引用不拷贝状态——claim 三态现场
读主库;entity_link.zone 仅为派生时入卡门槛判定快照(kb_version 随行,
重派生可改),不是状态拷贝。

确定性纪律:本模块只做"给定数据 → 逐字节一致的库文件"——全部 JSON 列
sort_keys 序列化,写入按主键排序单事务完成,**时间戳一律取自源产物**
(调用方传什么写什么,本模块禁止取 now());同源数据两遍写入 sha256 一致
(幂等验收=tests/test_kb_derive.py)。

deleted_at 列为删除权墓碑预留(蓝图 §十四-④ 待表态):零成本可逆,
机制本版不实现。

rule_card 表(K5,2026-10-27):蓝图 §三要点6"规则卡=独立规则表,非对象卡
(尺子≠证据)"——**不进 entity/card_kinds**(范畴分开):对象卡=证据的视图,
规则卡=判据的物化(源=kb-import 上传件,重派生后存活,幂等同款)。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CARD_KINDS_DEFAULT = ("实体", "项目", "话题", "案卷", "文档")

_KB_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
  entity_id      TEXT PRIMARY KEY,   -- ent_/prj_/dos_ 前缀 + 确定性哈希(见 kb_derive)
  kind           TEXT NOT NULL,      -- 卡种开放注册:kb_rules.card_kinds, schema 不 CHECK
  names_json     TEXT NOT NULL,      -- {"canonical": …, "aliases": […]}(sort_keys)
  attributes_json TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL DEFAULT '',   -- 源侧时间(events.created_at 派生),禁 now()
  deleted_at     TEXT DEFAULT ''     -- 删除权墓碑预留列(§十四-④ 待表态)
);
CREATE TABLE IF NOT EXISTS entity_link (
  entity_id   TEXT NOT NULL REFERENCES entity(entity_id),
  ref_kind    TEXT NOT NULL,        -- claim|evidence|event|oq|signal|document
  ref_id      TEXT NOT NULL,
  zone        TEXT NOT NULL DEFAULT '',  -- 正卡|未决|待复核;仅对象卡 claim 链接有值
  kb_version  TEXT NOT NULL DEFAULT '',
  note        TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (entity_id, ref_kind, ref_id)
);
CREATE TABLE IF NOT EXISTS evidence_index (
  fingerprint      TEXT PRIMARY KEY,  -- text_key(title|content)(主库 dedup_key 文本半)
  lineage_roots_json TEXT NOT NULL DEFAULT '[]',
  origin_refs_json TEXT NOT NULL DEFAULT '[]',  -- ["{event}:E{n}",…] 排序去重
  source_type      TEXT NOT NULL DEFAULT '',    -- crawler|internal|human|signal_seed
  sensitivity      TEXT NOT NULL DEFAULT 'public',  -- public|internal(消费主库 sensitive)
  observed_at      TEXT NOT NULL DEFAULT '',    -- 证据发布时间(published_at)
  url              TEXT NOT NULL DEFAULT '',
  simhash          TEXT NOT NULL DEFAULT '',
  title            TEXT NOT NULL DEFAULT '',    -- 内容快照(K2,2026-10-27):证据行
  content          TEXT NOT NULL DEFAULT '',    -- append-only 不可变,快照≠状态拷贝
  source           TEXT NOT NULL DEFAULT '',    -- (铁律只禁拷贝状态;fingerprint
  author_type      TEXT NOT NULL DEFAULT '',    -- 等值即蓝图 §十 backref 反查)
  kb_version       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kb_anchor (
  event_id       TEXT PRIMARY KEY,
  kb_version     TEXT NOT NULL,
  snapshotted_at TEXT NOT NULL DEFAULT '',  -- meta.recorded_at;无 meta 用 events.updated_at
  flags_json     TEXT NOT NULL DEFAULT '{}',  -- 管道版本标注(META_KEYS∩meta,sort_keys)
  pipeline_note  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS claim_evidence_snap (
  claim_id   TEXT PRIMARY KEY,    -- {event}:C{n}
  fps_json   TEXT NOT NULL DEFAULT '[]',  -- 关联证据指纹(排序去重;K2,2026-10-27)
  kb_version TEXT NOT NULL DEFAULT ''     -- 派生时点快照:关联 append-only 非三态,
);                                          -- zone 同款"快照非状态拷贝"逻辑(检索器
                                            -- 自包含的前提,正本关联仍现场读主库)
CREATE TABLE IF NOT EXISTS rule_card (
  rule_id     TEXT PRIMARY KEY,  -- rul_ 前缀 + 确定性哈希(kb_import._rule_id)
  name        TEXT NOT NULL,     -- 规则卡名("××供应商准入规则")
  items_json  TEXT NOT NULL,     -- 条目数组(谓词/阈值全在表=数值进表纪律;sort_keys)
  source_file TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL DEFAULT '',  -- 源侧时间,禁 now()
  kb_version  TEXT NOT NULL DEFAULT ''
);                                 -- K5(2026-10-27):独立规则表非对象卡(§三要点6
                                   -- "尺子≠证据");本身不参与三态,比对表每格走三态
"""


class KbStoreError(Exception):
    """kb.db 存储层错误(路径不可写/数据不合法)。"""


def _jd(obj) -> str:
    """JSON 列统一序列化(排序键、保中文);确定性的一半在写入侧。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


class KbStore:
    """kb.db 写入/读取。写入=全量重建语义(write_all 清库重插)。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ---------------------------------------------------------------- 建/写

    @classmethod
    def create(cls, path: str | Path) -> "KbStore":
        """新建(全量重建)kb.db:先删后建,调用方随后 write_all 一次成型。"""
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        conn = sqlite3.connect(str(p))
        conn.executescript(_KB_SCHEMA)
        conn.commit()
        return cls(conn)

    @classmethod
    def open(cls, path: str | Path) -> "KbStore":
        p = Path(path)
        if not p.exists():
            raise KbStoreError(f"kb.db 不存在: {p}")
        conn = sqlite3.connect(str(p))
        conn.row_factory = sqlite3.Row
        return cls(conn)

    def write_all(self, entities: list[dict], links: list[dict],
                  evidences: list[dict], anchors: list[dict],
                  claim_assocs: list[dict], kb_version: str,
                  rules: list[dict] | None = None) -> None:
        """单事务全量写入;每组按主键排序插入——同源数据两遍=逐字节一致。

        入参为 kb_derive 产出的平铺 dict(键名=列名);JSON 类列在此处统一
        序列化,派生层只管纯数据。**一次性写入器语义:commit 后即关连接**
        (全量重建先 unlink 旧文件,Windows 上不关锁会撞 PermissionError,
        冒烟实证)。rules=K5 规则卡(源=kb-import 上传件,重派生后存活;
        缺省=空,零差异)。
        """
        cur = self.conn.cursor()
        # 全量重建:清六表重插(python sqlite3 隐式事务,commit 一次成型;
        # 不用显式 BEGIN——默认 isolation_level 下会撞"事务已开启")
        for t in ("entity_link", "evidence_index", "kb_anchor",
                  "claim_evidence_snap", "rule_card", "entity"):
            cur.execute(f"DELETE FROM {t}")
        for e in sorted(entities, key=lambda x: x["entity_id"]):
            cur.execute(
                "INSERT INTO entity(entity_id,kind,names_json,attributes_json,"
                "created_at,deleted_at) VALUES(?,?,?,?,?,?)",
                (e["entity_id"], e["kind"], _jd(e["names"]),
                 _jd(e.get("attributes") or {}), e.get("created_at", ""),
                 e.get("deleted_at", "")))
        for l in sorted(links, key=lambda x: (x["entity_id"], x["ref_kind"],
                                              x["ref_id"])):
            cur.execute(
                "INSERT INTO entity_link(entity_id,ref_kind,ref_id,zone,"
                "kb_version,note) VALUES(?,?,?,?,?,?)",
                (l["entity_id"], l["ref_kind"], l["ref_id"],
                 l.get("zone", ""), l.get("kb_version", kb_version),
                 l.get("note", "")))
        for v in sorted(evidences, key=lambda x: x["fingerprint"]):
            cur.execute(
                "INSERT INTO evidence_index(fingerprint,lineage_roots_json,"
                "origin_refs_json,source_type,sensitivity,observed_at,url,"
                "simhash,title,content,source,author_type,kb_version)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (v["fingerprint"], _jd(v.get("lineage_roots") or []),
                 _jd(v.get("origin_refs") or []), v.get("source_type", ""),
                 v.get("sensitivity", "public"), v.get("observed_at", ""),
                 v.get("url", ""), v.get("simhash", ""),
                 v.get("title", ""), v.get("content", ""),
                 v.get("source", ""), v.get("author_type", ""),
                 v.get("kb_version", kb_version)))
        for a in sorted(anchors, key=lambda x: x["event_id"]):
            cur.execute(
                "INSERT INTO kb_anchor(event_id,kb_version,snapshotted_at,"
                "flags_json,pipeline_note) VALUES(?,?,?,?,?)",
                (a["event_id"], a.get("kb_version", kb_version),
                 a.get("snapshotted_at", ""), _jd(a.get("flags") or {}),
                 a.get("pipeline_note", "")))
        for c in sorted(claim_assocs, key=lambda x: x["claim_id"]):
            cur.execute(
                "INSERT INTO claim_evidence_snap(claim_id,fps_json,kb_version)"
                " VALUES(?,?,?)",
                (c["claim_id"], _jd(c.get("fps") or []),
                 c.get("kb_version", kb_version)))
        for r in sorted(rules or [], key=lambda x: x["rule_id"]):
            cur.execute(
                "INSERT INTO rule_card(rule_id,name,items_json,source_file,"
                "created_at,kb_version) VALUES(?,?,?,?,?,?)",
                (r["rule_id"], r["name"], _jd(r.get("items") or []),
                 r.get("source_file", ""), r.get("created_at", ""),
                 r.get("kb_version", kb_version)))
        self.conn.commit()
        self.conn.close()
        self.conn = None

    # ---------------------------------------------------------------- 读

    def entities(self, kind: str = "") -> list[dict]:
        sql = "SELECT * FROM entity"
        rows = (self.conn.execute(sql).fetchall() if not kind else
                self.conn.execute(sql + " WHERE kind=?", (kind,)).fetchall())
        out = []
        for r in rows:
            out.append({"entity_id": r["entity_id"], "kind": r["kind"],
                        "names": json.loads(r["names_json"]),
                        "attributes": json.loads(r["attributes_json"]),
                        "created_at": r["created_at"]})
        return out

    def links(self, entity_id: str = "", ref_kind: str = "") -> list[dict]:
        sql, args = "SELECT * FROM entity_link WHERE 1=1", []
        if entity_id:
            sql += " AND entity_id=?"
            args.append(entity_id)
        if ref_kind:
            sql += " AND ref_kind=?"
            args.append(ref_kind)
        rows = self.conn.execute(sql, args).fetchall()
        return [{"entity_id": r["entity_id"], "ref_kind": r["ref_kind"],
                 "ref_id": r["ref_id"], "zone": r["zone"],
                 "kb_version": r["kb_version"], "note": r["note"]}
                for r in rows]

    def rules(self, name: str = "") -> list[dict]:
        """规则卡读取(K5);name 传规则卡名精确匹配(空=全部)。"""
        sql, args = "SELECT * FROM rule_card", []
        if name:
            sql += " WHERE name=?"
            args.append(name)
        rows = self.conn.execute(sql, args).fetchall()
        return [{"rule_id": r["rule_id"], "name": r["name"],
                 "items": json.loads(r["items_json"]),
                 "source_file": r["source_file"],
                 "created_at": r["created_at"]}
                for r in rows]

    def anchor(self, event_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM kb_anchor WHERE event_id=?", (event_id,)).fetchone()
        if not r:
            return None
        return {"event_id": r["event_id"], "kb_version": r["kb_version"],
                "snapshotted_at": r["snapshotted_at"],
                "flags": json.loads(r["flags_json"]),
                "pipeline_note": r["pipeline_note"]}
