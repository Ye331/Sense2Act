# -*- coding: utf-8 -*-
"""K3v1 内部上传通道测试(2026-10-27)。

覆盖(蓝图 §九 K3 验收):
- 台账→卡 roundtrip(CSV→上传件→合并→对象卡,确定性零 LLM);
- md 文档档1:文档卡+原件指纹入 evidence_index(internal 默认);
- 重派生存活+幂等(上传件=源数据,sha256 逐字节);
- 敏感红线消费侧:internal 上传件 K2 检索零服务;
- 同文文档与既有证据同指纹并轨(origin_refs 并集=十家转载一根同款);
- CLI 端到端(kb-import/kb-derive)+校验错误(缺 canonical rc=2/坏件显式报错)。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ROOT  # noqa: E402
from sense2act.kb_derive import (  # noqa: E402
    derive_event, load_rules, open_main_ro, write_kb)
from sense2act.kb_import import (  # noqa: E402
    KbImportError, import_doc, import_ledger, load_uploads, merge_uploads)
from sense2act.kb_retrieve import KbRetriever  # noqa: E402
from sense2act.kb_store import KbStore  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "data" / "config"

_LEDGER_CSV = (
    "canonical,aliases,credit_code,focus\n"
    "测试供应商甲,甲公司;SUP-A,91330100MA0000001A,机房装修\n")
_DOC_MD = "# 合同 DB-2024-017\n\n账期三十天,违约金千分之五。\n"


def _derive_with(led, upload_dir, event_id="evt_t"):
    rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
    src = led.conn.execute("PRAGMA database_list").fetchone()[2]
    conn = open_main_ro(src)
    try:
        res = derive_event(conn, event_id, rules)
    finally:
        conn.close()
    kb = os.path.join(tempfile.mkdtemp(), "kb.db")
    write_kb(kb, [res], [], rules["version"],
             uploads=load_uploads(upload_dir))
    return kb


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class TestLedgerImport(unittest.TestCase):
    def test_台账roundtrip_CSV到对象卡(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "suppliers.csv"
        csv.write_text(_LEDGER_CSV, encoding="utf-8")
        updir = tmp / "uploads"
        out = import_ledger(csv, updir)
        self.assertTrue(out.exists())
        kb = _derive_with(_ledger_source(), upload_dir=updir)
        store = KbStore.open(kb)
        cards = {e["names"]["canonical"]: e for e in store.entities()
                 if e["kind"] == "实体"}
        self.assertIn("测试供应商甲", cards)
        card = cards["测试供应商甲"]
        self.assertEqual(card["names"]["aliases"], ["SUP-A", "甲公司"])
        self.assertEqual(card["attributes"]["credit_code"],
                         "91330100MA0000001A")
        self.assertEqual(card["attributes"]["focus"], "机房装修")

    def test_缺canonical拒绝(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "bad.csv"
        csv.write_text("name,credit_code\n测试供应商甲,91330100\n",
                       encoding="utf-8")
        with self.assertRaises(KbImportError):
            import_ledger(csv, tmp / "uploads")

    def test_上传件幂等_重派生存活(self):
        """上传件=源数据(非派生视图):重派生后卡仍在,且两遍逐字节一致。"""
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "suppliers.csv"
        csv.write_text(_LEDGER_CSV, encoding="utf-8")
        updir = tmp / "uploads"
        import_ledger(csv, updir)
        led = _ledger_source()
        kb1 = _derive_with(led, upload_dir=updir)
        kb2 = _derive_with(led, upload_dir=updir)
        self.assertEqual(_sha(kb1), _sha(kb2))          # 幂等逐字节
        store = KbStore.open(kb1)
        self.assertIn("测试供应商甲",
                      {e["names"]["canonical"] for e in store.entities()})


def _ledger_source():
    """最小主库(与 mini fixture 同族,给派生一个事件底座)。"""
    from test_kb_retrieve import _envelope, add_ev, _mini_ledger
    return _mini_ledger(pending=True)


class TestDocImport(unittest.TestCase):
    def test_md文档档1_文档卡与internal指纹(self):
        tmp = Path(tempfile.mkdtemp())
        md = tmp / "contract.md"
        md.write_text(_DOC_MD, encoding="utf-8")
        updir = tmp / "uploads"
        import_doc(md, updir)                            # 默认 internal
        kb = _derive_with(_ledger_source(), upload_dir=updir)
        import sqlite3
        conn = sqlite3.connect(kb)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM evidence_index WHERE source_type='internal'")]
        conn.close()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["sensitivity"], "internal")
        self.assertIn("账期三十天", row["content"])       # 内容快照
        store = KbStore.open(kb)
        docs = [e for e in store.entities() if e["kind"] == "文档"]
        self.assertEqual(len(docs), 1)
        links = store.links(docs[0]["entity_id"], "evidence")
        self.assertEqual([l["ref_id"] for l in links], [row["fingerprint"]])

    def test_internal上传件_K2检索零服务(self):
        """§6.4 红线消费侧:internal 上传件永不进 prompt(K3 验收断言)。"""
        tmp = Path(tempfile.mkdtemp())
        md = tmp / "contract.md"
        md.write_text(_DOC_MD, encoding="utf-8")
        updir = tmp / "uploads"
        import_doc(md, updir)
        kb = _derive_with(_ledger_source(), upload_dir=updir)
        ret = KbRetriever(kb, _CONFIG)
        try:
            items, _ = ret.retrieve("合同 DB-2024-017 账期三十天")
            self.assertFalse(any("账期" in i.content for i in items))
        finally:
            ret.close()

    def test_同文文档与既有证据并轨(self):
        """同文→同指纹→同一条 evidence_index 行(origin_refs 并集)=十家
        转载一根的同款聚合语义。"""
        tmp = Path(tempfile.mkdtemp())
        md = tmp / "note.md"
        body = "公告:测试采购中心机房改造项目预算一百万元"
        md.write_text(body, encoding="utf-8")
        updir = tmp / "uploads"
        import_doc(md, updir, sensitivity="public", title="公告转载")
        from test_kb_retrieve import _mini_ledger
        led = _mini_ledger(pending=True)
        kb = _derive_with(led, upload_dir=updir)
        import sqlite3
        conn = sqlite3.connect(kb)
        n = conn.execute(
            "SELECT COUNT(*) FROM evidence_index WHERE content LIKE '%预算一百万元%'"
        ).fetchone()[0]
        refs = conn.execute(
            "SELECT origin_refs_json FROM evidence_index"
            " WHERE content LIKE '%预算一百万元%'").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)                           # 并轨成一行
        self.assertIn("upload:doc_note", refs)           # 上游 origin 并入

    def test_非法sensitivity拒绝(self):
        tmp = Path(tempfile.mkdtemp())
        md = tmp / "x.md"
        md.write_text("内容", encoding="utf-8")
        updir = tmp / "uploads"
        import_doc(md, updir, sensitivity="secret")
        with self.assertRaises(KbImportError):
            merge_uploads({}, [], [], load_uploads(updir))


class TestCli(unittest.TestCase):
    def test_cli_端到端_导入到派生(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "suppliers.csv"
        csv.write_text(_LEDGER_CSV, encoding="utf-8")
        updir = tmp / "uploads"
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-import",
             "--ledger", str(csv), "--upload-dir", str(updir)],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertTrue((updir / "suppliers.json").exists())
        kb = os.path.join(tmp, "kb.db")
        from test_kb_retrieve import _mini_ledger
        led = _mini_ledger(pending=True)
        src = led.conn.execute("PRAGMA database_list").fetchone()[2]
        proc2 = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-derive",
             "--source-db", src, "--event-id", "evt_t", "--kb", kb,
             "--uploads-dir", str(updir)],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc2.returncode, 0, proc2.stderr[-400:])
        store = KbStore.open(kb)
        self.assertIn("测试供应商甲",
                      {e["names"]["canonical"] for e in store.entities()})

    def test_cli_缺canonical_rc2(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "bad.csv"
        csv.write_text("name,x\n甲,1\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-import",
             "--ledger", str(csv),
             "--upload-dir", os.path.join(tmp, "u")],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("canonical", proc.stderr)


if __name__ == "__main__":
    unittest.main()
