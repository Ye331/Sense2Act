# -*- coding: utf-8 -*-
"""K5 规则卡比对测试(2026-10-27)。

覆盖(蓝图 §九 K5 验收:比对纯函数单测+资质核对表三态与金标口径一致):
- 规则上传(K3 路径③):JSON/CSV→上传件→merge→rule_card 表;必填校验
  /谓词白名单(改表即生效疫苗)/rid 唯一/同名异文拒绝;
- 自我卡(§三要点5):self:true→ent_self 固定 id;未配置显式报错;
- 比对三态=金标口径:已证实=载明∧真/矛盾=载明∧假/待证实=无载明
  (不伪装满足);holds 分层语义(attributes 显式记录可判异,claim 文本
  沉默≠否认);未决区不作依据(§七消费通道纪律同源);
- 幂等重派生存活+排序确定性逐字节;render md/json 双产出;
- CLI 端到端(kb-import --rules/kb-check)+门禁(缺 --db rc=2)。
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

from sense2act.kb_check import (  # noqa: E402
    KbCheckError, _money_yuan, assemble_subject, compare_rules, render_check)
from sense2act.kb_derive import (  # noqa: E402
    derive_event, load_rules, open_main_ro, write_kb)
from sense2act.kb_import import (  # noqa: E402
    KbImportError, import_ledger, import_rules, load_uploads, merge_uploads)
from sense2act.kb_store import KbStore  # noqa: E402
from test_kb_retrieve import _BUYER, _envelope, _mini_ledger  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "data" / "config"

_RULES_JSON = {
    "name": "测试供应商准入规则",
    "items": [
        {"rid": "r001", "kind": "准入", "field": "注册资本",
         "predicate": "gte", "value": "500万元",
         "text": "注册资本不低于人民币500万元"},
        {"rid": "r002", "kind": "准入", "field": "注册资本",
         "predicate": "gte", "value": "1000万元", "text": ""},
        {"rid": "r003", "kind": "准入", "field": "资质",
         "predicate": "holds", "value": "三类医疗器械许可证", "text": ""},
        {"rid": "r004", "kind": "红线", "field": "资质",
         "predicate": "holds", "value": "二类医疗器械许可证", "text": ""},
        {"rid": "r005", "kind": "加分", "field": "类似项目经验",
         "predicate": "exists", "value": "有", "text": ""},
    ],
}

_SELF_ENTITY = {"canonical": "我方公司", "self": True,
                "aliases": ["我司"],
                "attributes": {"注册资本": "800万元",
                               "资质": "三类医疗器械许可证(2024)"}}


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _db_path(led) -> str:
    return led.conn.execute("PRAGMA database_list").fetchone()[2]


def _write_rules_json(tmp: Path) -> Path:
    p = tmp / "rules.json"
    p.write_text(json.dumps(_RULES_JSON, ensure_ascii=False),
                 encoding="utf-8")
    return p


class TestRuleImport(unittest.TestCase):
    """路径③:规则表→上传件→聚合(确定性零 LLM)。"""

    def test_规则JSON上传roundtrip_到rule_card表(self):
        tmp = Path(tempfile.mkdtemp())
        src = _write_rules_json(tmp)
        updir = tmp / "uploads"
        out = import_rules(src, updir, config_dir=_CONFIG)
        self.assertTrue(out.exists())
        rules = merge_uploads({}, [], [], load_uploads(updir),
                              rule_params={"predicates": ["gte", "holds",
                                                          "exists"],
                                           "item_kinds": ["准入", "红线",
                                                          "加分"],
                                           "default_item_kind": "准入",
                                           "self_entity_id": "ent_self"})
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["name"], "测试供应商准入规则")
        self.assertTrue(rules[0]["rule_id"].startswith("rul_"))
        self.assertEqual([i["rid"] for i in rules[0]["items"]],
                         ["r001", "r002", "r003", "r004", "r005"])
        self.assertEqual(rules[0]["items"][1]["text"],
                         "注册资本 gte 1000万元")       # 空 text 自动拼

    def test_规则CSV上传_rid按序派生(self):
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "rules.csv"
        csv.write_text(
            "kind,field,predicate,value\n"
            "准入,注册资本,gte,500万元\n"
            "红线,行贿记录,holds,有\n", encoding="utf-8")
        updir = tmp / "uploads"
        import_rules(csv, updir, name="CSV准入规则", config_dir=_CONFIG)
        rules = merge_uploads({}, [], [], load_uploads(updir),
                              rule_params={"predicates": ["gte", "holds"],
                                           "item_kinds": ["准入", "红线"],
                                           "default_item_kind": "准入",
                                           "self_entity_id": "ent_self"})
        self.assertEqual(rules[0]["name"], "CSV准入规则")
        self.assertEqual([i["rid"] for i in rules[0]["items"]],
                         ["r001", "r002"])             # 缺省按序派生

    def test_缺必填列拒绝(self):
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "bad.json"
        src.write_text(json.dumps(
            {"name": "坏规则", "items": [
                {"field": "注册资本", "predicate": "gte"}]},
            ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(KbImportError) as ctx:
            import_rules(src, tmp / "u", config_dir=_CONFIG)
        self.assertIn("value", str(ctx.exception))

    def test_非法谓词拒绝_白名单改表即生效疫苗(self):
        """白名单查 kb_rules.json rule_check(开放注册):清单外拒绝;
        临时 config 目录真加谓词→同一上传件通过(改表即生效,照数值参数
        从规则表消费纪律)。"""
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "rules.json"
        src.write_text(json.dumps(
            {"name": "新谓词规则", "items": [
                {"field": "成立年限", "predicate": "older_than",
                 "value": "5年"}]}, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(KbImportError) as ctx:
            import_rules(src, tmp / "u", config_dir=_CONFIG)
        self.assertIn("older_than", str(ctx.exception))
        cfg = tmp / "cfg"
        cfg.mkdir()
        base = json.loads((_CONFIG / "kb_rules.json").read_text("utf-8"))
        base["rule_check"]["predicates"] = base["rule_check"]["predicates"] \
            + ["older_than"]
        (cfg / "kb_rules.json").write_text(
            json.dumps(base, ensure_ascii=False), encoding="utf-8")
        for f in ("aliases.json", "watchlist.json"):
            (cfg / f).write_text(
                (_CONFIG / f).read_text("utf-8"), encoding="utf-8")
        out = import_rules(src, tmp / "u2", config_dir=cfg)   # 改表→通过
        self.assertTrue(out.exists())

    def test_同名规则卡异文拒绝(self):
        tmp = Path(tempfile.mkdtemp())
        updir = tmp / "uploads"
        body_a = {"name": "同卡", "items": [
            {"field": "注册资本", "predicate": "gte", "value": "500万元"}]}
        body_b = {"name": "同卡", "items": [
            {"field": "注册资本", "predicate": "gte", "value": "800万元"}]}
        for tag, body in (("a", body_a), ("b", body_b)):
            p = tmp / f"r_{tag}.json"
            p.write_text(json.dumps(body, ensure_ascii=False),
                         encoding="utf-8")
            import_rules(p, updir, config_dir=_CONFIG)
        rc = {"predicates": ["gte"], "item_kinds": ["准入"],
              "default_item_kind": "准入", "self_entity_id": "ent_self"}
        with self.assertRaises(KbImportError) as ctx:
            merge_uploads({}, [], [], load_uploads(updir), rule_params=rc)
        self.assertIn("同名异文", str(ctx.exception))


class TestSelfCard(unittest.TestCase):
    """自我卡(蓝图 §三要点5:资质核对表挂靠点)。"""

    def test_self声明归固定id_未配置显式报错(self):
        ents: dict = {}
        up = [{"_name": "x.json", "entities": [dict(_SELF_ENTITY)]}]
        rules = merge_uploads(ents, [], [], up,
                              rule_params={"self_entity_id": "ent_self"})
        self.assertEqual(list(ents), ["ent_self"])
        self.assertEqual(ents["ent_self"]["names"]["canonical"], "我方公司")
        with self.assertRaises(KbImportError) as ctx:
            merge_uploads({}, [], [], [dict(u) for u in up])
        self.assertIn("self_entity_id", str(ctx.exception))


class _Derived:
    """mini 台账+规则/自我卡上传→kb.db(测试底座)。"""

    def __init__(self, pending=False):
        self.updir = Path(tempfile.mkdtemp()) / "uploads"
        led_csv = Path(tempfile.mkdtemp()) / "self.csv"
        led_csv.write_text(
            "canonical,aliases,注册资本,资质\n"
            "我方公司,我司,800万元,三类医疗器械许可证(2024)\n",
            encoding="utf-8")
        import_ledger(led_csv, self.updir)
        # 台账转出的对象卡无 self 标——上传件 JSON 直改注入 self 声明
        upfile = self.updir / "self.json"
        doc = json.loads(upfile.read_text("utf-8"))
        doc["entities"][0]["self"] = True
        upfile.write_text(json.dumps(doc, ensure_ascii=False),
                          encoding="utf-8")
        import_rules(_write_rules_json(Path(tempfile.mkdtemp())),
                     self.updir, config_dir=_CONFIG)
        self.rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
        self.led = _mini_ledger(pending=pending)
        conn = open_main_ro(_db_path(self.led))
        try:
            self.res = derive_event(conn, "evt_t", self.rules)
        finally:
            conn.close()

    def derive(self) -> str:
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        write_kb(kb, [self.res], [], self.rules["version"],
                 uploads=load_uploads(self.updir),
                 rule_params=self.rules.get("rule_check"))
        return kb

    def mains(self) -> dict:
        from sense2act.kb_chain import open_mains
        return open_mains([_db_path(self.led)], _CONFIG)


class TestDeriveWiring(unittest.TestCase):
    def test_规则卡落库_自我卡注册_幂等存活(self):
        d = _Derived()
        kb1, kb2 = d.derive(), d.derive()
        self.assertEqual(_sha(kb1), _sha(kb2))           # 幂等逐字节
        store = KbStore.open(kb1)
        try:
            rs = store.rules()
            self.assertEqual(len(rs), 1)
            self.assertEqual(rs[0]["name"], "测试供应商准入规则")
            self.assertEqual(len(rs[0]["items"]), 5)
            ents = {e["entity_id"]: e for e in store.entities()}
            self.assertIn("ent_self", ents)              # 自我卡注册
            self.assertEqual(
                ents["ent_self"]["attributes"]["注册资本"], "800万元")
        finally:
            store.conn.close()

    def test_三态与金标口径一致_逐格断言(self):
        """K5 验收:比对表每格三态=金标口径(models 枚举同源三值)。"""
        from sense2act.models.base import ClaimStatus
        d = _Derived()
        kb = d.derive()
        store = KbStore.open(kb)
        mains = d.mains()
        try:
            sub = assemble_subject(store, mains, "ent_self",
                                   d.rules["zones"])
            self.assertEqual(sub["attributes"]["注册资本"], "800万元")
            rules = store.rules()
            rows = compare_rules(rules, [sub], d.rules["zones"])
            valid = {s.value for s in ClaimStatus}
            for r in rows:
                self.assertIn(r["status"], valid,
                              f"三态与金标口径不一致: {r}")
            by = {r["rid"]: r for r in rows}
            self.assertEqual(by["r001"]["status"], "已证实")   # 800万≥500万
            self.assertIn("attributes:", by["r001"]["basis"])
            self.assertEqual(by["r002"]["status"], "矛盾")     # 800万<1000万
            self.assertEqual(by["r003"]["status"], "已证实")   # 显式记录含证书
            self.assertEqual(by["r004"]["status"], "矛盾")     # 显式记录判异
            self.assertEqual(by["r005"]["status"], "待证实")   # 无载明不伪装
            self.assertEqual(by["r005"]["basis"], "")
        finally:
            from sense2act.kb_chain import close_mains
            close_mains(mains)
            store.conn.close()

    def test_holds分层语义_claim沉默不判矛盾_未决区不作依据(self):
        """attributes 显式记录可判异(矛盾);claim 文本沉默≠否认(待证实);
        未决区 claim 不作合规依据(§七消费通道纪律同源)。"""
        d = _Derived()
        # 给自我卡挂一条正卡 claim(文本含"类似项目经验")与一条未决 claim
        ent_id = "ent_self"
        d.res  # noqa — 已派生;直接在 kb 聚合前注入 links 不可行,走主库:
        led = d.led
        led.conn.execute(
            "UPDATE claims SET text='我方公司有类似项目经验' "
            "WHERE claim_id='evt_t:C2'")
        led.conn.execute(
            "UPDATE claims SET status='待证实' WHERE claim_id='evt_t:C1'")
        led.conn.commit()        # assemble 走独立 ro 连接,未提交不可见
        # C2 已证实且实体归一命中测试采购中心——为挂到自我卡,直接在派生
        # 结果 links 上补一条(确定性注入,照 kb_derive 产出形态)
        d.res["links"] = list(d.res.get("links") or []) + [
            {"entity_id": ent_id, "ref_kind": "claim", "ref_id": "evt_t:C2",
             "zone": "正卡", "note": "测试注入"}]
        kb = d.derive()
        store = KbStore.open(kb)
        mains = d.mains()
        try:
            sub = assemble_subject(store, mains, ent_id, d.rules["zones"])
            self.assertEqual(len(sub["claims"]), 1)      # 只挂了 C2
            rows = compare_rules(store.rules(), [sub], d.rules["zones"])
            by = {r["rid"]: r for r in rows}
            # r005 exists 类似项目经验:正卡 claim 文本含 field→已证实
            self.assertEqual(by["r005"]["status"], "已证实")
            self.assertEqual(by["r005"]["basis"], "claim:evt_t:C2")
            # 未决区 claim 不作依据:去掉正卡资格再比对→r005 待证实
            sub2 = dict(sub)
            sub2["claims"] = [dict(c, in_positive=False) for c in sub["claims"]]
            rows2 = compare_rules(store.rules(), [sub2], d.rules["zones"])
            by2 = {r["rid"]: r for r in rows2}
            self.assertEqual(by2["r005"]["status"], "待证实")
        finally:
            from sense2act.kb_chain import close_mains
            close_mains(mains)
            store.conn.close()


class TestCompareKernel(unittest.TestCase):
    """比对内核纯函数(零库):分层语义/数值归一/排序确定性。"""

    _RULES = [{"rule_id": "rul_x", "name": "R", "items": [
        {"rid": "r001", "kind": "准入", "field": "注册资本",
         "predicate": "gte", "value": "500万元", "text": ""},
        {"rid": "r002", "kind": "准入", "field": "预算",
         "predicate": "lte", "value": "200万元", "text": ""},
        {"rid": "r003", "kind": "准入", "field": "资质",
         "predicate": "holds", "value": "三类医疗器械许可证", "text": ""},
        {"rid": "r004", "kind": "加分", "field": "类似项目经验",
         "predicate": "exists", "value": "有", "text": ""},
    ]}]
    _ZONES = {"正卡": ["已证实", "矛盾"], "未决": ["待证实"]}

    def test_数值归一与不可比待证实(self):
        self.assertEqual(_money_yuan("800万元"), 8000000.0)
        self.assertEqual(_money_yuan("0.02亿元"), 2000000.0)
        self.assertIsNone(_money_yuan("未知"))
        sub = {"entity_id": "s1", "canonical": "甲",
               "attributes": {"注册资本": "伍佰万元整", "预算": "100万元"},
               "claims": []}
        rows = compare_rules(self._RULES, [sub], self._ZONES)
        by = {r["rid"]: r for r in rows}
        self.assertEqual(by["r001"]["status"], "待证实")   # 中文数字不可比
        self.assertIn("不可比", by["r001"]["reason"])
        self.assertEqual(by["r002"]["status"], "已证实")   # 100万≤200万

    def test_排序确定性与双产出零分叉(self):
        subs = [
            {"entity_id": "s2", "canonical": "乙", "attributes": {},
             "claims": []},
            {"entity_id": "s1", "canonical": "甲",
             "attributes": {"注册资本": "800万元", "预算": "100万元",
                            "资质": "三类医疗器械许可证"},
             "claims": []},
        ]
        rows1 = compare_rules(self._RULES, subs, self._ZONES)
        rows2 = compare_rules(self._RULES, list(reversed(subs)),
                              self._ZONES)
        self.assertEqual(json.dumps(rows1, sort_keys=True),
                         json.dumps(rows2, sort_keys=True))  # 入参序不敏感
        keys = [(r["rule_id"], r["rid"], r["subject_id"]) for r in rows1]
        self.assertEqual(keys, sorted(keys))                 # 排序确定性
        md = render_check(rows1, "md")
        self.assertIn("资质核对表", md)
        self.assertIn("✓ 已证实", md)
        data = json.loads(render_check(rows1, "json"))
        self.assertEqual(len(data), len(rows1))              # 双产出同数据

    def test_zones缺正卡映射拒绝(self):
        with self.assertRaises(KbCheckError):
            compare_rules(self._RULES, [], {})


class TestCli(unittest.TestCase):
    def test_cli_规则上传到比对端到端(self):
        tmp = Path(tempfile.mkdtemp())
        src = _write_rules_json(tmp)
        updir = tmp / "uploads"
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-import",
             "--rules", str(src), "--upload-dir", str(updir)],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertTrue((updir / "rules_rules.json").exists())
        d = _Derived()
        kb = d.derive()
        dbp = _db_path(d.led)
        proc2 = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-check",
             "--kb", kb, "--db", dbp, "--subject", "ent_self"],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc2.returncode, 0, proc2.stderr[-400:])
        self.assertIn("资质核对表", proc2.stdout)
        self.assertIn("✓ 已证实", proc2.stdout)
        self.assertIn("✗ 矛盾", proc2.stdout)
        self.assertIn("? 待证实", proc2.stdout)
        from sense2act.kb_chain import close_mains
        close_mains(d.mains())

    def test_cli_缺主库rc2(self):
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-check", "--kb", "x.db"],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--db", proc.stderr)


if __name__ == "__main__":
    unittest.main()
