# -*- coding: utf-8 -*-
"""知识库 K1 派生层测试(2026-10-26)。

覆盖(蓝图 §九 K1 验收预注册):
- 四则门槛表驱动:纯种子复述族零对象卡/案卷卡全量一条不丢/状态分区;
- 种子质量闸:病根族∧predicate_check off→待复核,on→正卡;
- 发现三源:信封结构化字段/台账 claim_entities/别名归一(信封俗称过别名
  表——B公司 vs B新能源科技有限公司 双卡事故的疫苗);
- 项目锚:同 pno 跨 run 聚合同卡+信号 manifest 挂卡;
- 确定性:同源两遍派生逐字节一致(sha256)/主库 mode=ro 零写入;
- 规则表纪律:改表即生效疫苗(照数值参数从规则表消费纪律)+loader 校验;
- 卡种开放注册:schema 不 CHECK kind,新卡种注册即生效。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ALIASES, add_evidence, install_simple, make_ledger  # noqa: E402
from sense2act.kb_derive import (  # noqa: E402
    KbDeriveError, derive_db, derive_event, is_eligible, load_rules,
    open_main_ro, source_type_of, write_kb, _prj_id)
from sense2act.kb_store import KbStore, KbStoreError  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.envelope import (  # noqa: E402
    InvestigationEnvelope, TenderSignal)
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_RULES = _REPO / "data" / "config" / "kb_rules.json"
_CONFIG = _REPO / "data" / "config"


def _load_rules():
    return load_rules(_RULES, _CONFIG)


def _db_path(led) -> str:
    return led.conn.execute("PRAGMA database_list").fetchone()[2]


def _mini_ledger():
    """C1 纯种子根(signal_layer round=0)/C2 C3 采集根(round≥1)。"""
    led = make_ledger()
    install_simple(led, "evt_t")
    add_evidence(led, "evt_t", "signal_layer", "信封摘要称拖欠工资", "C1",
                 round_no=0, platform="signal_layer", source="signal_layer",
                 author_type="内部检测")
    add_evidence(led, "evt_t", "gsxt", "工商登记显示B公司劳动仲裁立案",
                 "C2", round_no=1)
    add_evidence(led, "evt_t", "company_web", "官网未发布公告", "C3",
                 round_no=1)
    return led


def _tender_ledger(project_id="PRJ-KB-T1", purchaser="测试采购中心",
                   event_id="evt_p"):
    led = make_ledger()
    env = InvestigationEnvelope(
        kind="tender", event_id=event_id,
        tender=TenderSignal(project_id=project_id,
                            project_name="机房改造项目(项目编号:SHGP-2026-K1)",
                            purchaser=purchaser,
                            published_date="2026-10-20"))
    led.register_event(env, "商机", event_id)
    bp = AnalysisBlueprint(
        claims=[ClaimDraft(id="C1", text=f"{purchaser}预算一千万元",
                           category=ClaimCategory.FACT, section_index=2,
                           priority=5, entity_names=[purchaser])],
        hypotheses=[], gaps=[])
    led.install_blueprint(event_id, bp, aliases=ALIASES)
    add_evidence(led, event_id, "ccgp", "公告:机房改造预算一千万元",
                 "C1", round_no=1)
    return led


def _derive(led, event_id="evt_t", meta=None, rules=None):
    """派生到临时 kb 并返回 (KbStore, 单事件 stats)。主库零触碰由调用方断言。"""
    rules = rules or _load_rules()
    kb = os.path.join(tempfile.mkdtemp(), "kb.db")
    conn = open_main_ro(_db_path(led))
    try:
        res = derive_event(conn, event_id, rules, meta)
    finally:
        conn.close()
    write_kb(kb, [res], [], rules["version"])
    return KbStore.open(kb), res["stats"]


class TestRulesLoader(unittest.TestCase):
    def test_缺version拒载(self):
        d = tempfile.mkdtemp()
        bad = Path(d) / "kb_rules.json"
        doc = json.loads(_RULES.read_text(encoding="utf-8-sig"))
        del doc["version"]
        bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(KbDeriveError):
            load_rules(bad, _CONFIG)

    def test_derived_kinds越界拒载(self):
        d = tempfile.mkdtemp()
        bad = Path(d) / "kb_rules.json"
        doc = json.loads(_RULES.read_text(encoding="utf-8-sig"))
        doc["derived_kinds"] = ["关系卡"]          # 不在 card_kinds 注册表内
        bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(KbDeriveError):
            load_rules(bad, _CONFIG)

    def test_病根族清单同源解析(self):
        """trigger_patterns_source 指针现读 credibility_rules.json——清单
        只有一份(同源纪律):改 credibility_rules 即生效,kb_rules 零复制。"""
        rules = _load_rules()
        src = json.loads(
            (_CONFIG / "credibility_rules.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(rules["_trigger_patterns"],
                         src["predicate_check_trigger_patterns"])
        self.assertTrue(rules["_trigger_patterns"])


class TestSourceGate(unittest.TestCase):
    def _gate(self):
        return _load_rules()["source_gate"]

    def test_四分类机械判据表驱动(self):
        gate = self._gate()
        seed = {"platform": "signal_layer", "round": 0,
                "lineage_root": "signal_layer", "author_type": "内部检测"}
        self.assertEqual(source_type_of(seed, gate), "signal_seed")
        self.assertEqual(source_type_of(
            {"platform": "ccgp", "round": 1, "lineage_root": "ccgp",
             "author_type": ""}, gate), "crawler")
        self.assertEqual(source_type_of(
            {"platform": "human", "round": 1, "lineage_root": "human",
             "author_type": "用户裁决"}, gate), "human")
        self.assertEqual(source_type_of(
            {"platform": "internal_history", "round": 0,
             "lineage_root": "internal_history", "author_type": ""},
            gate), "internal")
        # 浏览器捕获:round=0 但 author_type 带标记(capture_cli 同款)→采集
        self.assertEqual(source_type_of(
            {"platform": "toutiao", "round": 0, "lineage_root": "toutiao",
             "author_type": "官方 [browser_auto]"}, gate), "crawler")

    def test_改表即生效疫苗(self):
        """门槛数值从 kb_rules 消费,零代码兜底(照 ess_rules 纪律):
        eligible_round_min 调到 2 后 round=1 采集根不再合格。"""
        rules = _load_rules()
        gate = dict(rules["source_gate"])
        gate["eligible_round_min"] = 2
        self.assertFalse(is_eligible(
            {"platform": "ccgp", "round": 1, "lineage_root": "ccgp",
             "author_type": ""}, gate))
        self.assertTrue(is_eligible(
            {"platform": "ccgp", "round": 2, "lineage_root": "ccgp",
             "author_type": ""}, gate))


class TestDeriveGates(unittest.TestCase):
    def test_案卷卡全量一条不丢(self):
        store, stats = _derive(_mini_ledger())
        dos = [e for e in store.entities() if e["kind"] == "案卷"]
        self.assertEqual(len(dos), 1)
        claim_links = store.links(dos[0]["entity_id"], "claim")
        self.assertEqual(len(claim_links), stats["n_claims"])
        self.assertEqual({l["ref_id"] for l in claim_links},
                         {"evt_t:C1", "evt_t:C2", "evt_t:C3"})

    def test_纯种子复述族零对象卡_且不生噪音卡(self):
        """C1 支持根全为 signal_layer 种子=解读信封复述族:不入对象卡,
        也不为其注册零链接卡(归一注册延迟到 zone 判定后)。"""
        store, _ = _derive(_mini_ledger())
        links = store.links()
        c1_links = [l for l in links if l["ref_id"] == "evt_t:C1"
                    and l["ref_kind"] == "claim"]
        self.assertTrue(all(l["zone"] == "" for l in c1_links))  # 仅案卷
        # B 新能源卡(信封 subject 归一后)不因 C1 出生
        b_cards = [e for e in store.entities() if e["kind"] == "实体"
                   and "B新能源" in e["names"]["canonical"]]
        self.assertEqual(b_cards, [])

    def test_待证实合格根进未决区(self):
        store, _ = _derive(_mini_ledger())
        zones = {(l["ref_id"], l["zone"]) for l in store.links()
                 if l["ref_kind"] == "claim" and l["zone"]}
        self.assertIn(("evt_t:C2", "未决"), zones)
        self.assertIn(("evt_t:C3", "未决"), zones)

    def test_已证实合格根进正卡区(self):
        led = _mini_ledger()
        led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE claim_id='evt_t:C2'")
        led.conn.commit()
        store, _ = _derive(led)
        zones = {(l["ref_id"], l["zone"]) for l in store.links()
                 if l["ref_kind"] == "claim" and l["zone"]}
        self.assertIn(("evt_t:C2", "正卡"), zones)

    def test_别名归一_信封俗称与台账canonical同卡(self):
        """B公司(信封 subject)过别名表→B新能源科技有限公司(台账 canonical):
        归一口径统一,不出同实体双卡(冒烟实证过的事故疫苗)。"""
        led = _mini_ledger()
        # C1 加采集根使其过来源则 → B 系 claim 入对象卡
        add_evidence(led, "evt_t", "gsxt", "仲裁立案公告:B公司涉案", "C1",
                     round_no=2)
        store, _ = _derive(led)
        b_cards = [e for e in store.entities() if e["kind"] == "实体"
                   and "B新能源" in e["names"]["canonical"]]
        self.assertEqual(len(b_cards), 1)
        low_cards = [e for e in store.entities() if e["kind"] == "实体"
                     and e["names"]["canonical"] == "B公司"]
        self.assertEqual(low_cards, [])

    def test_信封结构化字段出卡_项目锚挂事件(self):
        led = _tender_ledger()
        store, _ = _derive(led, event_id="evt_p")
        cards = {e["names"]["canonical"]: e for e in store.entities()}
        self.assertIn("测试采购中心", cards)          # 来源①purchaser
        prj = [e for e in store.entities() if e["kind"] == "项目"]
        self.assertEqual(len(prj), 1)
        self.assertEqual(prj[0]["entity_id"], _prj_id("PRJ-KB-T1"))
        ev_links = store.links(prj[0]["entity_id"], "event")
        self.assertEqual([l["ref_id"] for l in ev_links], ["evt_p"])

    def test_自证复述族彻底出局_改表即生效疫苗(self):
        """用户 10-26 二次拍板:对象卡只收世界断言。corpus_bound=1 的
        "信封载明/线索卡称"复述型不进对象卡(案卷卡兜底不变);开关改表
        即生效(substance_gate.exclude_self_contained=false → 原样回来)。"""
        led = _mini_ledger()
        led.conn.execute(
            "UPDATE claims SET status='已证实', corpus_bound=1"
            " WHERE claim_id='evt_t:C2'")
        led.conn.commit()
        store, _ = _derive(led)
        c2_zoned = [l for l in store.links()
                    if l["ref_id"] == "evt_t:C2" and l["zone"]]
        self.assertEqual(c2_zoned, [])               # 自证出局
        dos = [e for e in store.entities() if e["kind"] == "案卷"][0]
        self.assertIn("evt_t:C2",                      # 案卷兜底仍在
                      {l["ref_id"] for l in store.links(dos["entity_id"], "claim")})
        rules = _load_rules()
        rules["substance_gate"] = {"exclude_self_contained": False}
        store2, _ = _derive(led, rules=rules)
        c2_zoned2 = {l["zone"] for l in store2.links()
                     if l["ref_id"] == "evt_t:C2" and l["zone"]}
        self.assertEqual(c2_zoned2, {"正卡"})          # 开关关→原样回来

    def test_框架词前缀出局_防未开代码判定的老run(self):
        """corpus_bound=0 但文本以「线索卡称」开头(_strip_heads 同源判据)
        → 同样出局:复述措辞本身就是 provenance 非世界断言。"""
        led = _mini_ledger()
        led.conn.execute(
            "UPDATE claims SET text='线索卡称A公司未发布公告'"
            " WHERE claim_id='evt_t:C3'")              # corpus_bound 保持 0
        led.conn.commit()
        store, _ = _derive(led)
        c3_zoned = [l for l in store.links()
                    if l["ref_id"] == "evt_t:C3" and l["zone"]]
        self.assertEqual(c3_zoned, [])

    def test_种子质量闸_病根族折待复核(self):
        """正卡区∧predicate_check off∧文本命中病根族→待复核;on→直入正卡
        (蓝图 §二:病根族清单同源消费,零新清单)。"""
        rules = _load_rules()
        pat = rules["_trigger_patterns"][0]
        led = _mini_ledger()
        led.conn.execute(
            "UPDATE claims SET status='已证实', text=? WHERE claim_id='evt_t:C3'",
            (f"A公司{pat}未发布公告",))
        led.conn.commit()
        store, _ = _derive(led, meta={"predicate_check": False})
        zones = {l["ref_id"]: l["zone"] for l in store.links()
                 if l["ref_kind"] == "claim" and l["zone"]}
        self.assertEqual(zones["evt_t:C3"], "待复核")
        store2, _ = _derive(led, meta={"predicate_check": True})
        zones2 = {l["ref_id"]: l["zone"] for l in store2.links()
                  if l["ref_kind"] == "claim" and l["zone"]}
        self.assertEqual(zones2["evt_t:C3"], "正卡")


class TestProjectAnchor(unittest.TestCase):
    def test_同pno跨run聚合同卡(self):
        r1 = _tender_ledger(event_id="evt_p1")
        r2 = _tender_ledger(event_id="evt_p2")
        rules = _load_rules()
        conns = [open_main_ro(_db_path(l)) for l in (r1, r2)]
        try:
            res = [derive_event(c, eid, rules)
                   for c, eid in zip(conns, ("evt_p1", "evt_p2"))]
        finally:
            for c in conns:
                c.close()
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        write_kb(kb, res, [], rules["version"])
        store = KbStore.open(kb)
        prj = [e for e in store.entities() if e["kind"] == "项目"]
        self.assertEqual(len(prj), 1)              # 同 pno → 同一张项目卡
        ev_links = store.links(prj[0]["entity_id"], "event")
        self.assertEqual(sorted(l["ref_id"] for l in ev_links),
                         ["evt_p1", "evt_p2"])

    def test_信号manifest挂卡_未知pno即建卡(self):
        rules = _load_rules()
        led = _mini_ledger()
        conn = open_main_ro(_db_path(led))
        try:
            res = [derive_event(conn, "evt_t", rules)]
        finally:
            conn.close()
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        rows = [{"signal_id": "SIG-X1", "project_id": "PRJ-KB-T1",
                 "company_id": "C1", "status": "ok"}]
        write_kb(kb, res, rows, rules["version"])
        store = KbStore.open(kb)
        prj = [e for e in store.entities()
               if e["kind"] == "项目"
               and e["names"]["canonical"] == "PRJ-KB-T1"]
        self.assertEqual(len(prj), 1)              # 信号到站即挂卡(未调查)
        sig_links = store.links(prj[0]["entity_id"], "signal")
        self.assertEqual([l["ref_id"] for l in sig_links], ["SIG-X1"])
        self.assertEqual(prj[0]["attributes"].get("company_id"), "C1")


class TestDeterminismAndReadOnly(unittest.TestCase):
    def test_同源两遍派生逐字节一致(self):
        led = _mini_ledger()
        src = _db_path(led)
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        derive_db(src, "evt_t", kb)
        h1 = hashlib.sha256(Path(kb).read_bytes()).hexdigest()
        derive_db(src, "evt_t", kb)
        h2 = hashlib.sha256(Path(kb).read_bytes()).hexdigest()
        self.assertEqual(h1, h2)

    def test_主库零写入_mode_ro强制(self):
        led = _mini_ledger()
        src = _db_path(led)
        before = hashlib.sha256(Path(src).read_bytes()).hexdigest()
        derive_db(src, "evt_t",
                  os.path.join(tempfile.mkdtemp(), "kb.db"))
        self.assertEqual(
            hashlib.sha256(Path(src).read_bytes()).hexdigest(), before)
        conn = open_main_ro(src)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute(
                    "INSERT INTO events(event_id,kind,envelope_json,"
                    "created_at,updated_at) VALUES('x','rumor','{}','','')")
        finally:
            conn.close()


class TestStore(unittest.TestCase):
    def test_卡种开放注册(self):
        """schema 不 CHECK kind:新卡种(如演进项关系卡)注册进 kb_rules
        card_kinds 后存储层即收——kind 不写死 schema(照 META_KEYS 疫苗
        教训的反面:开放注册由配置承担)。"""
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        store = KbStore.create(kb)
        store.write_all(
            [{"entity_id": "ent_x", "kind": "关系卡",
              "names": {"canonical": "A-供货于-B", "aliases": []},
              "attributes": {}, "created_at": "2026-10-26"}],
            [], [], [], [], "kb-0.1")
        store2 = KbStore.open(kb)
        kinds = {e["kind"] for e in store2.entities()}
        self.assertIn("关系卡", kinds)

    def test_缺文件拒开(self):
        with self.assertRaises(KbStoreError):
            KbStore.open(os.path.join(tempfile.mkdtemp(), "nope.db"))


class TestCli(unittest.TestCase):
    def test_cli单库模式端到端(self):
        led = _mini_ledger()
        src = _db_path(led)
        kb = os.path.join(tempfile.mkdtemp(), "kb.db")
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-derive",
             "--source-db", src, "--event-id", "evt_t", "--kb", kb],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertTrue(Path(kb).exists())
        # 单库模式缺 --event-id = rc 2(门禁)
        proc2 = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-derive",
             "--source-db", src, "--kb",
             os.path.join(tempfile.mkdtemp(), "kb.db")],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc2.returncode, 2)


if __name__ == "__main__":
    unittest.main()
