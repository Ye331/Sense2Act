# -*- coding: utf-8 -*-
"""证据链卷面改造三阶段测试(2026-11-06 方案 §七-7)——insights 升格(P7
双名兼容/存量迁移幂等)+ 模式表(改表即生效)+ 呈现治理(去壳两态/分流三支/
资格收紧)+ 关系词机械判据 + 解读桥(门槛参数/机器闸/无论证区不上图)+
转生闭环(清单/resume 注册/幂等)+ dot 引擎(定位器/兜底降级/确定性)+
格式矩阵(md-chain/png)。纪律继承:渲染期零 LLM/确定性/失败不伪装/
insights 永不回流判决(负测试延续)。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ALIASES, ROOT, add_evidence, make_ledger, rumor_envelope  # noqa: E402
from sense2act import chain_gen as cg  # noqa: E402
from sense2act import chain_graph as cgr  # noqa: E402
from sense2act import chain_svg as csv_  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

_CONFIG = ROOT / "data" / "config"
_AUTH = {"ccgp"}
_RULES = cgr.load_present_rules(str(_CONFIG))

_C4_TEXT = "解读将本信号定性为“商机”，依据是信封 kind 为 tender 且为公开可参与的投标机会"
_C6_TEXT = "解读自述：信封中 amountHistory 均为 null"
_C7_TEXT = "解读登记了三条基础缺口：该单位历史采购记录与金额基线缺失"


# ---------------------------------------------------------------- fixture

def _bp() -> AnalysisBlueprint:
    """v3 fixture:解读三支实证形态(定性 C4/元信息 C6/缺口建议 C7)+
    转述壳 C8(去壳两态)。H0 暴雷={C1,C2},H1 机会={C4(解读型),C5}。"""
    return AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司中标XX项目", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"],
                       gist="B公司中标XX项目"),
            ClaimDraft(id="C2", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C4", text=_C4_TEXT, category=ClaimCategory.FACT,
                       section_index=2, priority=3),
            ClaimDraft(id="C6", text=_C6_TEXT, category=ClaimCategory.FACT,
                       section_index=2, priority=3),
            ClaimDraft(id="C7", text=_C7_TEXT, category=ClaimCategory.FACT,
                       section_index=2, priority=3),
            ClaimDraft(id="C8", text="信封载明：项目编号 X1，采购方为B公司",
                       category=ClaimCategory.FACT, section_index=2, priority=4,
                       gist="信封载明：项目编号 X1"),
        ],
        hypotheses=[], gaps=[])


def _fixture() -> LedgerRepository:
    """C1 双根已证实(rootA+ccgp)/C2 孤证待证实/C5 派生自 C1 单根已证实/
    C4 解读型(种子单根自证已证实)/C8 壳命题(ccgp 权威根已证实)。
    无迁移:H1 n_verified=2(C4 解读型+C5)胜 H0(1);迁移后:H1 只剩
    C5 一票 → n_verified 2→1=资格收紧可观测(P4;成员集不动)。"""
    led = make_ledger()
    led.register_event(rumor_envelope("evt_v3"), "", "evt_v3")
    led.install_blueprint("evt_v3", _bp(), aliases=ALIASES)
    for cid, text in (("C4", _C4_TEXT), ("C6", _C6_TEXT), ("C7", _C7_TEXT)):
        led.add_claim("evt_v3", {"id": cid, "text": text, "category": "事实",
                                 "section_index": 2, "priority": 3}, 2)
    for hid, label, desc, cids in (
            ("H0", "暴雷", "资金链断裂", ("C1", "C2")),
            ("H1", "机会", "扩张信号", ("C4", "C5"))):
        led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES(?,?,?,?,0,0)",
            (f"evt_v3:{hid}", "evt_v3", label, desc))
        for cid in cids:
            led.conn.execute(
                "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
                " VALUES(?,?,'AND')", (f"evt_v3:{hid}", f"evt_v3:{cid}"))
    # C1 已证实:rootA+ccgp=双根(ccgp 边=直接证明,rootA 边=印证)
    e1 = add_evidence(led, "evt_v3", "rootA", "公告载明B公司中标", "C1",
                      title="中标公告")
    e2 = add_evidence(led, "evt_v3", "ccgp", "政采公告:B公司中标", "C1",
                      platform="ccgp", title="政采公告")
    led.set_ruling("evt_v3:C1", e1, "支持", 1)
    led.set_ruling("evt_v3:C1", e2, "支持", 1)
    led.update_claim_status("evt_v3:C1", "已证实", None, 1, e1)
    # C2 孤证
    e3 = add_evidence(led, "evt_v3", "rootC", "论坛帖称B公司拖欠工资", "C2",
                      title="论坛帖")
    led.set_ruling("evt_v3:C2", e3, "支持", 1)
    # C5 派生自 C1,单根已证实
    led.add_claim("evt_v3", {"id": "C5", "text": "B公司中标XX项目且金额放大",
                             "category": "事实", "section_index": 2,
                             "priority": 4, "parent": "C1"}, 2)
    e4 = add_evidence(led, "evt_v3", "rootB", "年报载明中标金额放大", "C5",
                      title="年报")
    led.set_ruling("evt_v3:C5", e4, "支持", 2)
    led.update_claim_status("evt_v3:C5", "已证实", None, 2, e4)
    # C4 解读型:种子单根自证已证实
    es = add_evidence(led, "evt_v3", "signal_layer", "信号解读字段陈述", "C4",
                      platform="signal_layer", title="信号层")
    led.set_ruling("evt_v3:C4", es, "支持", 2)
    led.update_claim_status("evt_v3:C4", "已证实", None, 2, es)
    # C8 壳命题:ccgp 权威根已证实(去壳两态测试件)
    e5 = add_evidence(led, "evt_v3", "ccgp", "政采公告载明项目编号 X1", "C8",
                      platform="ccgp", title="政采公告2")
    led.set_ruling("evt_v3:C8", e5, "支持", 2)
    led.update_claim_status("evt_v3:C8", "已证实", None, 2, e5)
    led.conn.commit()
    return led


def _migrated_kb(led: LedgerRepository, kbp: Path) -> Path:
    """存量迁移后的 kb(机械零 LLM 直调;等效 chain-gen 首步)。"""
    conn = cg.open_insights_rw(kbp)
    cg.migrate_legacy_insights(led, conn, "evt_v3", _RULES)
    conn.close()
    return kbp


def _build(led, kb=None, config_dir=None, seeds_path=None):
    return cgr.build_chain_graph({"evt_v3": led}, kb, "evt_v3",
                                 auth_roots=_AUTH,
                                 config_dir=str(config_dir or _CONFIG),
                                 seeds_path=seeds_path)


def _by_draft(graph, draft_id):
    return next(c for c in graph["nodes"]["claims"]
                if c["draft_id"] == draft_id)


class _FakeLLM:
    model_name = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)

    def generate_json(self, system_prompt, user_prompt, *, stage=""):
        if not self.replies:
            raise RuntimeError("回执耗尽")
        return self.replies.pop(0)


_UPSTREAM_TEXT = "上游解读:公开招标构成可参与的合作机会,值得跟进"


def _put_bridge(kbp: Path, conclusion="evt_v3:C5",
                basis='["evt_v3:C1"]', fp_text=None) -> None:
    """写 upstream 源行+interpretation 桥行(等效 chain-gen 产物)。"""
    basis_list = json.loads(basis)
    conn = cg.open_insights_rw(kbp)
    cg._put(conn, sid="upstream:evt_v3:0", event_id="evt_v3",
            kind="signal_interpretation", claim_id="", value=_UPSTREAM_TEXT,
            fingerprint=cgr.legacy_fp(_UPSTREAM_TEXT), source="upstream",
            status="anchored")
    conn.execute(
        "INSERT OR REPLACE INTO insights(insight_id,event_id,kind,"
        "claim_id,value,fingerprint,model,prompt_version,status,source)"
        " VALUES('interp:upstream:evt_v3:0','evt_v3','interpretation',?,"
        "?,?,?,?,'anchored','internal')",
        (conclusion,
         json.dumps({"source": "upstream:evt_v3:0", "conclusion": conclusion,
                     "basis": basis_list, "rationale": "招标公告语义"},
                    ensure_ascii=False, sort_keys=True),
         fp_text or cgr.interp_fp(_UPSTREAM_TEXT, basis_list),
         "fake-model", cg.PROMPT_VERSION))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- §七.1 insights 升格

class InsightsUpgradeTests(unittest.TestCase):
    """P7 升格:新库=insights 物理表+旧名视图;老库=补三列+反向视图;
    旧表名照读照写(触发器路由);双名读口。"""

    def test_fresh_db_insights_physical_view_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_insights_rw(kbp)
            kinds = {r[0]: r[1] for r in conn.execute(
                "SELECT name, type FROM sqlite_master"
                " WHERE name IN ('insights','chain_sidecar')")}
            self.assertEqual(kinds["insights"], "table")
            self.assertEqual(kinds["chain_sidecar"], "view")
            # 旧形态 INSERT(一阶段纪律)经触发器照写
            conn.execute(
                "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
                "value) VALUES('polarity:e:C1','e','polarity','e:C1','正向')")
            row = conn.execute("SELECT * FROM insights").fetchone()
            self.assertEqual(row["insight_id"], "polarity:e:C1")
            self.assertEqual(row["source"], "internal")   # 老路=internal
            conn.close()

    def test_legacy_db_upgrades_additive(self):
        """升格前老库(chain_sidecar 物理,无三列)→补列+反向视图;原行
        照读,经 insights 名写新 kind=路由到底表(照写)。"""
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = sqlite3.connect(kbp)
            conn.executescript(
                "CREATE TABLE chain_sidecar (sidecar_id TEXT PRIMARY KEY,"
                " event_id TEXT NOT NULL, kind TEXT NOT NULL, claim_id TEXT"
                " NOT NULL, hypothesis_id TEXT NOT NULL DEFAULT '', value TEXT"
                " NOT NULL, rationale TEXT NOT NULL DEFAULT '', fingerprint"
                " TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',"
                " prompt_version TEXT NOT NULL DEFAULT '', created_at TEXT"
                " NOT NULL DEFAULT '');")
            conn.execute(
                "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
                "value) VALUES('polarity:e:C1','e','polarity','e:C1','正向')")
            conn.commit()
            conn.close()
            conn = cg.open_insights_rw(kbp)   # 升格
            row = conn.execute("SELECT * FROM insights").fetchone()
            self.assertEqual(row["insight_id"], "polarity:e:C1")
            self.assertEqual(row["source"], "")   # 存量行新列如实空
            cg._put(conn, sid="legacy:e:C9", event_id="e", kind="gap_hint",
                    claim_id="e:C9", value="解读登记缺口", source="legacy",
                    status="received", legacy_claim_id="e:C9")
            n_old = conn.execute(
                "SELECT COUNT(*) FROM chain_sidecar").fetchone()[0]
            self.assertEqual(n_old, 2)
            conn.close()

    def test_sidecar_rows_dual_name_read(self):
        """双名读口:insights 在=读正名;视图被删退老表名;两者皆无=空表。"""
        self.assertEqual(cgr.sidecar_rows(None, "evt_v3", "polarity"), [])
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_insights_rw(kbp)
            cg._put(conn, event_id="evt_v3", kind="polarity", claim_id="c",
                    value="正向")
            conn.close()
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            self.assertEqual(len(cgr.sidecar_rows(kb, "evt_v3", "polarity")), 1)
            kb.execute("DROP VIEW chain_sidecar")   # 退化为只剩正名
            self.assertEqual(len(cgr.sidecar_rows(kb, "evt_v3", "polarity")), 1)
            kb.execute("DROP TABLE insights")
            self.assertEqual(cgr.sidecar_rows(kb, "evt_v3", "polarity"), [])
            kb.close()


# ---------------------------------------------------------------- §七.1/P9 存量迁移

class LegacyMigrationTests(unittest.TestCase):
    """P9:解读三类存量 claim→insights 镜像(source=legacy,回指,指纹锚定);
    幂等;主库零改动;壳命题不迁移(事实载体)。"""

    def test_migrate_three_branches_idempotent(self):
        led = _fixture()
        before = [dict(r) for r in led.conn.execute(
            "SELECT claim_id, text, status FROM claims ORDER BY claim_id")]
        with tempfile.TemporaryDirectory() as td:
            conn = cg.open_insights_rw(Path(td) / "kb.db")
            n = cg.migrate_legacy_insights(led, conn, "evt_v3", _RULES)
            self.assertEqual(n, 3)   # C4 定性/C6 元信息/C7 缺口;C8 壳不迁
            kinds = {r["claim_id"]: (r["kind"], r["source"], r["status"])
                     for r in conn.execute("SELECT * FROM insights")}
            self.assertEqual(kinds["evt_v3:C4"][0], "signal_interpretation")
            self.assertEqual(kinds["evt_v3:C6"][0], "detector_field")
            self.assertEqual(kinds["evt_v3:C7"][0], "gap_hint")
            for cid in kinds:
                self.assertEqual(kinds[cid][1], "legacy")
                self.assertEqual(kinds[cid][2], "received")
            # P9:回指+指纹锚定原文本
            row = conn.execute("SELECT * FROM insights WHERE insight_id=?",
                               ("legacy:evt_v3:C4",)).fetchone()
            self.assertEqual(row["legacy_claim_id"], "evt_v3:C4")
            self.assertEqual(row["fingerprint"], cgr.legacy_fp(_C4_TEXT))
            n2 = cg.migrate_legacy_insights(led, conn, "evt_v3", _RULES)
            self.assertEqual(n2, 0)   # 幂等
            conn.close()
        after = [dict(r) for r in led.conn.execute(
            "SELECT claim_id, text, status FROM claims ORDER BY claim_id")]
        self.assertEqual(before, after)   # 主库零改动(P9)

    def test_seed_label_subkind(self):
        led = _fixture()
        led.add_claim("evt_v3", {"id": "C9",
                                 "text": "解读选中的种子标签为“真实经营动态”",
                                 "category": "事实", "section_index": 2,
                                 "priority": 3}, 2)
        with tempfile.TemporaryDirectory() as td:
            conn = cg.open_insights_rw(Path(td) / "kb.db")
            cg.migrate_legacy_insights(led, conn, "evt_v3", _RULES)
            row = conn.execute("SELECT kind FROM insights WHERE insight_id=?",
                               ("legacy:evt_v3:C9",)).fetchone()
            self.assertEqual(row["kind"], "signal_meta")   # 种子标签机械细分
            conn.close()


# ---------------------------------------------------------------- §七.2 模式表

class PresentRulesTests(unittest.TestCase):
    """模式表:纯函数判据+改表即生效(照 ess_rules 数值参数纪律)。"""

    def test_classify_and_strip(self):
        self.assertEqual(cgr.classify_claim(
            "解读将本信号定性为“商机”，理由为公开招标构成机会", _RULES),
            "interpretation")
        self.assertEqual(cgr.classify_claim(
            "解读登记了三条基础缺口：A、B、C", _RULES), "gap_hint")
        self.assertEqual(cgr.classify_claim(
            "解读自述：信封中 amountHistory 均为 null", _RULES), "meta_info")
        self.assertIsNone(cgr.classify_claim("B公司拖欠工资", _RULES))
        self.assertIsNone(cgr.classify_claim(
            "解读类命题之外的普通句子", _RULES))   # 不在模式=保守留作事实
        self.assertEqual(cgr.strip_shell(
            "信封载明：项目编号 X1，采购方为B公司", _RULES),
            "项目编号 X1，采购方为B公司")
        self.assertEqual(cgr.strip_shell(
            "信封正文摘要载明潜在投标人应在政采云获取招标文件", _RULES),
            "潜在投标人应在政采云获取招标文件")
        self.assertEqual(cgr.strip_shell("B公司拖欠工资", _RULES),
                         "B公司拖欠工资")   # 未命中=原文

    def test_rules_table_hot_reload(self):
        """改表即生效:tmp 模式表删壳/收紧门槛,行为随表变。"""
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td)
            rules = dict(_RULES)
            rules["shells"] = []                     # 删壳表
            rules["bridge_min_verified"] = 2         # 收紧门槛
            (cfg / "claim_present_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            loaded = cgr.load_present_rules(str(cfg))
            self.assertEqual(cgr.strip_shell("信封载明：项目编号 X1", loaded),
                             "信封载明：项目编号 X1")   # 表空=不剥
            self.assertEqual(int(loaded.get("bridge_min_verified", 1)), 2)


# ---------------------------------------------------------------- §七.3 呈现治理

class GovernanceTests(unittest.TestCase):
    """去壳两态(已证实剥/待证实保留)/分流三支/资格收紧(P4)。"""

    def _routed_build(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        return g

    def test_shell_strip_only_when_verified(self):
        """去壳两态:已证实 C8 剥壳直陈;同壳待证实保留壳(三态纪律)。"""
        led = _fixture()
        led.add_claim("evt_v3", {"id": "C10",
                                 "text": "信封载明：投标截止 2026-12-04",
                                 "category": "事实", "section_index": 2,
                                 "priority": 3}, 2)
        led.conn.commit()
        g = _build(led)   # 无 kb:不涉分流,纯去壳判据
        self.assertEqual(_by_draft(g, "C8")["label"],
                         "项目编号 X1")   # 已证实→gist 剥壳
        c10 = _by_draft(g, "C10")
        self.assertEqual(c10["status"], "待证实")
        self.assertIn("信封载明", c10["label"])          # 待证实→保留壳

    def test_routing_three_branches(self):
        g = self._routed_build()
        c4 = _by_draft(g, "C4")
        c6 = _by_draft(g, "C6")
        c7 = _by_draft(g, "C7")
        self.assertEqual(c4["interpretive"], "bridge")
        self.assertEqual(c6["interpretive"], "meta")
        self.assertEqual(c7["interpretive"], "clue")
        for c in (c4, c6, c7):
            self.assertFalse(c["in_main"])       # 不进主图命题层(有边也不画)
            self.assertIsNone(c["gap"])          # 不进缺口图
        self.assertEqual(g["meta"]["n_routed"],
                         {"bridge": 1, "clue": 1, "meta": 1})
        # C4 有种子裁定边但被分流(无边不画之外的第二道排除)
        self.assertNotIn("解读将本信号定性",
                         json.dumps(g["rows"], ensure_ascii=False))

    def test_argument_qualification_excludes_interpretive(self):
        """资格收紧(P4):解读型不计已证实——H1 成色 2/2→1/2(负测试锁:
        资格不含解读型);成员集不动,只收资格计数。"""
        g0 = _build(_fixture())   # 无迁移基线:解读型 C4 计入
        self.assertEqual(g0["argument"]["hypothesis_id"], "evt_v3:H1")
        self.assertEqual(g0["argument"]["n_verified"], 2)
        g = self._routed_build()
        self.assertEqual(g["argument"]["hypothesis_id"], "evt_v3:H1")
        self.assertEqual(g["argument"]["n_verified"], 1)   # 只数事实成员
        self.assertEqual(g["argument"]["verdict_line"],
                         "部分立住:1/2 条已证实")
        self.assertIn("evt_v3:C4", g["argument"]["member_ids"])

    def test_pick_argument_unit_exclusion(self):
        """判据函数单元:exclude_ids 直接收窄 n_verified(两侧同源口)。"""
        hyps = [{"hypothesis_id": "h", "label": "L", "score": 0}]
        top, _ = cgr.pick_argument(hyps, {"h": ["a", "b"]},
                                   {"a": "已证实", "b": "已证实"},
                                   exclude_ids={"b"})
        self.assertEqual(top["n_verified"], 1)
        self.assertEqual(top["member_ids"], ["a", "b"])   # 成员集不动


# ---------------------------------------------------------------- §七.4 关系词

class RelationWordTests(unittest.TestCase):
    """关系词六词(机械判据=判决梯已承认的量;红只留拆台)。"""

    def test_six_words(self):
        f = cgr.relation_of
        self.assertEqual(f("支持", "ccgp", ["ccgp"], {"ccgp"}), "直接证明")
        self.assertEqual(f("支持", "rootA", ["rootA", "ccgp"], {"ccgp"}),
                         "印证")          # 非权威边但双根成员
        self.assertEqual(f("支持", "rootC", ["rootC"], set()), "佐证")
        self.assertEqual(f("矛盾", "rootB", ["rootB"], set()), "反证")
        self.assertEqual(f("时效刷新", "rootT", ["rootT"], set()), "时效更新")

    def test_edges_carry_relation(self):
        g = _build(_fixture())
        rel = {(e["src"].rsplit(":", 1)[-1], e["dst"].rsplit(":", 1)[-1]):
               e["relation"] for e in g["edges"]["supports"]}
        self.assertEqual(rel[("E2", "C1")], "直接证明")   # ccgp 权威边
        self.assertEqual(rel[("E1", "C1")], "印证")       # 双根成员的普通根边
        self.assertEqual(rel[("E3", "C2")], "佐证")       # 单根

    def test_rows_rowwise_structure(self):
        """行式结构(v3):每行≈一条推理线;论证区行向右连,事实行止于命题层。"""
        g = _build(_fixture())
        rows = {r["claim_id"]: r for r in g["rows"]}
        self.assertIn("evt_v3:C5", rows)
        self.assertFalse(rows["evt_v3:C5"]["terminal"])   # 论证区成员非独立
        self.assertTrue(rows["evt_v3:C5"]["group"])       # 有归属组
        self.assertTrue(rows["evt_v3:C1"]["terminal"])    # 事实组行止于命题层
        ev0 = rows["evt_v3:C5"]["evidence"][0]
        self.assertIn("relation", ev0)


# ---------------------------------------------------------------- §七.5 解读桥

class BridgeTests(unittest.TestCase):
    """桥=纯结构零 LLM:interpretation 旁车指纹有效∧机器闸∧门槛(P3 参数
    表)→画;无论证区不上图;桥不进三态/记分。"""

    def test_bridge_drawn_with_gate_and_threshold(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        self.assertEqual(g["meta"]["n_bridges_drawn"], 1)
        b = g["bridges"][0]
        self.assertEqual(b["conclusion"], "evt_v3:C5")
        self.assertEqual(b["basis"], ["evt_v3:C1"])
        self.assertEqual(b["n_verified"], 1)      # C1 已证实
        self.assertTrue(b["target_group"])
        self.assertIn(b["target_group"],
                      [x["name"] for x in g["argument"]["groups"]])

    def test_bridge_blocked_by_threshold_from_table(self):
        """门槛改表收紧即生效(P3):min=2 而 basis 仅 1 条已证实→挡除计数。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "cfg"
            cfg.mkdir()
            rules = dict(_RULES)
            rules["bridge_min_verified"] = 2
            (cfg / "claim_present_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb, config_dir=cfg)
            kb.close()
        self.assertEqual(g["meta"]["n_bridges_drawn"], 0)
        self.assertEqual(g["meta"]["n_bridges_blocked"], 1)

    def test_bridge_blocked_on_fingerprint_drift(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp, fp_text="deadbeef")
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        self.assertEqual(g["meta"]["n_bridges_drawn"], 0)
        self.assertEqual(g["meta"]["n_bridges_blocked"], 1)

    def test_bridge_not_drawn_without_argument(self):
        """无论证区→桥不上图(计数交代,照 narrative 无论证区先例)。"""
        led = _fixture()
        for cid in ("evt_v3:C1", "evt_v3:C5"):
            led.update_claim_status(cid, "待证实", None, 3)
        led.conn.commit()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        self.assertIsNone(g["argument"])
        self.assertEqual(g["meta"]["n_bridges_drawn"], 0)
        self.assertEqual(g["meta"]["n_bridges_blocked"], 1)


# ---------------------------------------------------------------- §七.5 生成期

class GenPhaseTests(unittest.TestCase):
    """生成期:上游抽取(零 LLM)/interpretation 锚点(机器闸拒收幻觉)/
    转生清单+resume 注册(幂等+谱系)。"""

    def test_upstream_extraction_zero_llm(self):
        led = _fixture()
        env = {
            "kind": "tender",
            "tender": {"projectName": "B公司服务采购项目",
                       "projectSummary": "B公司服务采购项目公告;采购单位:B公司;"
                                         "正文摘要:公告载明服务内容。"
                                         "解读将本信号定性为商机,依据是公开可参与。"
                                         "解读登记了基础缺口:历史基线缺失。"}}
        led.conn.execute("UPDATE events SET envelope_json=? WHERE event_id=?",
                         (json.dumps(env, ensure_ascii=False), "evt_v3"))
        led.conn.commit()
        with tempfile.TemporaryDirectory() as td:
            conn = cg.open_insights_rw(Path(td) / "kb.db")
            n = cg.extract_upstream_insights(led, conn, "evt_v3", _RULES)
            self.assertEqual(n, 2)   # 两句解读段命中
            rows = {r["kind"]: r["value"] for r in conn.execute(
                "SELECT kind, value FROM insights WHERE source='upstream'")}
            self.assertIn("signal_interpretation", rows)
            self.assertIn("gap_hint", rows)
            self.assertIn("公开可参与", rows["signal_interpretation"])
            conn.close()

    def test_interp_anchor_machine_gate(self):
        """机器闸:幻觉 conclusion/basis/自锚→invalid 计数零落库;合法→落库+
        源行 received→anchored(机械迁移)。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_insights_rw(kbp)
            cg.migrate_legacy_insights(led, conn, "evt_v3", _RULES)
            text = "上游解读:B公司中标为真商机"
            cg._put(conn, sid="upstream:evt_v3:0", event_id="evt_v3",
                    kind="signal_interpretation", claim_id="", value=text,
                    fingerprint=cgr.legacy_fp(text), source="upstream",
                    status="received")
            conn.close()
            replies = [{"items": []}, {"items": []}, {"items": []},
                       {"groups": []}, {"chapter": "", "walkthrough": ""},
                       {"items": [
                           {"source_id": "upstream:evt_v3:0",
                            "conclusion": "evt_v3:幻觉",
                            "basis": ["evt_v3:C1"], "rationale": "x"},
                           {"source_id": "upstream:evt_v3:0",
                            "conclusion": "evt_v3:C5",
                            "basis": ["evt_v3:不存在"], "rationale": "x"},
                           {"source_id": "upstream:evt_v3:0",
                            "conclusion": "evt_v3:C5",
                            "basis": ["evt_v3:C5"], "rationale": "自锚"},
                           {"source_id": "upstream:evt_v3:0",
                            "conclusion": "evt_v3:C5",
                            "basis": ["evt_v3:C1"], "rationale": "公告坐实"}]}]
            stats = cg.run_chain_gen({"evt_v3": led}, kbp, "evt_v3",
                                     provider=_FakeLLM(replies),
                                     auth_roots=_AUTH, config_dir=_CONFIG)
            self.assertEqual(stats["interp_total"], 1)
            self.assertEqual(stats["interp_written"], 1)
            self.assertEqual(stats["interp_invalid"], 3)   # 幻觉/缺basis/自锚
            conn = cg.open_insights_rw(kbp)
            row = conn.execute("SELECT * FROM insights WHERE insight_id=?",
                               ("interp:upstream:evt_v3:0",)).fetchone()
            v = json.loads(row["value"])
            self.assertEqual(v["conclusion"], "evt_v3:C5")
            self.assertEqual(v["basis"], ["evt_v3:C1"])
            src = conn.execute("SELECT status FROM insights WHERE insight_id=?",
                               ("upstream:evt_v3:0",)).fetchone()
            self.assertEqual(src["status"], "anchored")    # 机械迁移
            conn.close()

    def test_reborn_list_and_register(self):
        """转生:清单=去壳命题草稿;注册=add_claim 派生口+reborn_from 谱系+
        源行 received→derived;幂等(已 derived 跳过)。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            conn = cg.open_insights_rw(kbp)
            drafts = cg.reborn_drafts(conn, led, "evt_v3", _RULES)
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["text"],
                             "该单位历史采购记录与金额基线缺失")   # 去壳
            conn.close()
            registered = cg.register_reborn(kbp, led, "evt_v3", _RULES, 3)
            self.assertEqual(len(registered), 1)
            cid = registered[0]
            row = led.claim_row(cid)
            self.assertEqual(row["reborn_from"], "legacy:evt_v3:C7")  # 谱系
            self.assertTrue(row["claim_id"].endswith(":RB1"))
            self.assertEqual(row["status"], "待证实")
            conn = cg.open_insights_rw(kbp)
            src = conn.execute("SELECT status FROM insights WHERE insight_id=?",
                               ("legacy:evt_v3:C7",)).fetchone()
            self.assertEqual(src["status"], "derived")
            conn.close()
            again = cg.register_reborn(kbp, led, "evt_v3", _RULES, 3)
            self.assertEqual(again, [])   # 幂等

    def test_reborn_drift_dropped(self):
        """legacy 行指纹漂移(主库文本变)→不进清单(失败不伪装)。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            led.conn.execute("UPDATE claims SET text=? WHERE claim_id=?",
                             ("解读登记了三条基础缺口：文本已被改写",
                              "evt_v3:C7"))
            led.conn.commit()
            conn = cg.open_insights_rw(kbp)
            self.assertEqual(cg.reborn_drafts(conn, led, "evt_v3", _RULES), [])
            conn.close()


# ---------------------------------------------------------------- §七.6/§七.4 渲染引擎

def _has_dot() -> bool:
    return csv_._find_dot() is not None


class DotEngineTests(unittest.TestCase):
    """dot 定位器优先级/兜底降级注记/确定性/注入防御(HTML-like label)。"""

    def test_find_dot_env_priority(self):
        """GRAPHVIZ_DOT > PATH > tools/graphviz(优先级拍板,§七.0)。"""
        exe = sys.executable   # 仅验证「env 存在即优先」,非 dot 亦可
        old = os.environ.get("GRAPHVIZ_DOT")
        try:
            os.environ["GRAPHVIZ_DOT"] = exe
            self.assertEqual(csv_._find_dot(), exe)
            os.environ["GRAPHVIZ_DOT"] = "Z:/no/such/dot.exe"
            hit = csv_._find_dot()
            self.assertTrue(hit is None or "dot" in Path(hit).name)
        finally:
            if old is None:
                os.environ.pop("GRAPHVIZ_DOT", None)
            else:
                os.environ["GRAPHVIZ_DOT"] = old

    def test_row_engine_no_downgrade_note_and_deterministic(self):
        """行式主引擎(降级注记退役,行式链与缺口退场):确定性逐字节一致。"""
        g = _build(_fixture())
        a = csv_.render_chain_svg(g, engine="fallback")
        b = csv_.render_chain_svg(g, engine="fallback")
        self.assertEqual(a, b)                       # 逐字节一致
        self.assertNotIn("降级布局", a)               # 行式=主引擎,注记退役
        self.assertIn("<svg", a)

    def test_fallback_without_argument_still_renders(self):
        led = _fixture()
        for cid in ("evt_v3:C1", "evt_v3:C5"):
            led.update_claim_status(cid, "待证实", None, 3)
        led.conn.commit()
        g = _build(led)
        svg = csv_.render_chain_svg(g, engine="fallback")
        self.assertIn("<svg", svg)

    def test_dot_source_escapes_html(self):
        """注入防御:标签文本过 HTML-like 转义(escape 优先)。"""
        g = _build(_fixture())
        g["nodes"]["claims"][0]["label"] = '<b>&"x</b>'
        src = csv_._dot_source(g)
        self.assertIn("&lt;b&gt;", src)
        self.assertIn("&amp;", src)
        self.assertIn("&quot;", src)
        self.assertNotIn('<b>&"x</b>', src)   # 原样串不出境

    def test_cards_five_types(self):
        """五类卡统一模型(§五):证据/命题/桥/论点条/结论齐全且同源。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        cards = csv_.chain_cards(g)
        self.assertTrue(cards["evidence"] and cards["claims"])
        self.assertEqual(len(cards["bridges"]), 1)
        self.assertTrue(any(x["kind"] == "arg" for x in cards["groups"]))
        self.assertIsNotNone(cards["conclusion"])
        self.assertIn("依据 1 项事实·已查实 1", cards["bridges"][0]["line"])

    @unittest.skipUnless(_has_dot(), "需要 Graphviz dot(winget install graphviz)")
    def test_dot_engine_renders_svg_and_png(self):
        g = _build(_fixture())
        svg = csv_.render_chain_svg(g)   # 自动档=dot
        self.assertIn("<svg", svg)
        self.assertNotIn("降级布局", svg)
        self.assertEqual(csv_.render_chain_svg(g), svg)   # 同版本确定性
        with tempfile.TemporaryDirectory() as td:
            p = csv_.render_chain_png(g, Path(td) / "c.png")
            self.assertTrue(p.exists() and p.stat().st_size > 0)
            self.assertEqual(p.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_png_without_dot_fails_loud(self):
        """无 dot 出 png=显式报错(失败不伪装,不用 svg 冒充 png)。"""
        old = os.environ.get("GRAPHVIZ_DOT")
        try:
            os.environ["GRAPHVIZ_DOT"] = "Z:/no/such/dot.exe"
            real_which = __import__("shutil").which

            def _no_which(name, path=None):
                return None if name == "dot" else real_which(name, path)
            with mock.patch("sense2act.chain_svg.shutil.which", _no_which):
                if csv_._find_dot() is not None:
                    self.skipTest("PATH/tools 上存在 dot,无法模拟无 dot")
                g = _build(_fixture())
                with self.assertRaises(csv_.ChainSvgError):
                    csv_.render_chain_png(g, "unused.png")
        finally:
            if old is None:
                os.environ.pop("GRAPHVIZ_DOT", None)
            else:
                os.environ["GRAPHVIZ_DOT"] = old


class MdChainTests(unittest.TestCase):
    """md-chain 文字版(§五 新增):行式缩进列表,关系词+[En] 锚齐备。"""

    def _graph_with_bridge(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        return g

    def test_md_chain_structure(self):
        g = self._graph_with_bridge()
        text = csv_.render_md_chain(g)
        self.assertIn("证据链(文字版)", text)
        self.assertIn("研判结论", text)
        self.assertIn("〔直接证明〕", text)      # 关系词
        self.assertIn("[E2]", text)              # 编号锚
        self.assertIn("解读:", text)             # 桥行
        self.assertEqual(csv_.render_md_chain(g), text)   # 确定性

    def test_md_chain_empty_when_no_rows(self):
        g = {"nodes": {"claims": [], "evidence": []},
             "edges": {"supports": []},
             "clusters": [], "rows": [], "bridges": [], "argument": None,
             "fact_groups": []}
        self.assertEqual(csv_.render_md_chain(g), "")


class OverviewV3Tests(unittest.TestCase):
    """总览 v3 注记:分流三支/桥计数(机械模板零 LLM)。"""

    def test_routed_and_bridge_lines(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_bridge(kbp)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        text = "\n".join(csv_.overview_lines(g))
        self.assertIn("解读型分流:", text)
        self.assertIn("缺口线索 1 条", text)
        self.assertIn("解读定性 1 条", text)
        self.assertIn("元信息 1 条", text)
        self.assertIn("解读桥 1 座上图", text)
        self.assertIn("正本仍在台账", text)      # 不冒充删证


# ================================================================ v4 批1(四阶段 §三~§六:断行卫生/全文呈现/谱系归并/扇出汇流)

def _merge_fixture():
    """v4 批1 fixture:C1 同文撞号家族三代(C1d/C1d2=add_claim 同 draft_id
    异文派生,parent=被撞原条=星形)+真派生 C2(显式 parent=C1,id 无撞号
    形态→不进家族闭包=跨族前提边保留件)+C9 矛盾件(同证据反证=异关系
    不并件)。三代挂同件长标题证据(rootX 单根佐证)→同组 3 边=扇出汇流
    触发件。无 hypotheses→全落机械事实组(同主语 B公司)。三代状态:
    C1 已证实(round2)/C1d、C1d2 待证实→代表=纯最新代 C1d2 卡面待证实
    (拍板① 活例,不让旧代已证实掩盖新代待证实)。"""
    led = make_ledger()
    led.register_event(rumor_envelope("evt_m"), "", "evt_m")
    led.install_blueprint("evt_m", AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司中标XX项目金额45232元",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C9", text="B公司中标资格被质疑",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3, entity_names=["B公司"]),
        ], hypotheses=[], gaps=[]), aliases=ALIASES)
    d1 = led.add_claim("evt_m", {"id": "C1", "text": "B公司中标XX项目金额45232元。",
                                 "category": "事实", "section_index": 2,
                                 "priority": 4, "entity_names": ["B公司"]}, 2,
                       aliases=ALIASES)
    d2 = led.add_claim("evt_m", {"id": "C1", "text": "B公司中标XX项目金额45232元(复核)",
                                 "category": "事实", "section_index": 2,
                                 "priority": 4, "entity_names": ["B公司"]}, 3,
                       aliases=ALIASES)
    led.add_claim("evt_m", {"id": "C2", "text": "B公司中标XX项目且金额显著放大",
                            "category": "事实", "section_index": 2,
                            "priority": 4, "parent": "C1",
                            "entity_names": ["B公司"]}, 3, aliases=ALIASES)
    e1 = led.insert_evidence(
        "evt_m",
        {"platform": "official_website", "source": "rootX",
         "title": "很长的中标公告标题需要超过二十六字符的机器投影裁断阈值",
         "content": "公告载明B公司中标金额45232元", "lineage_root": "rootX",
         "author_type": "官方", "credibility": "高"}, 2)
    e2 = led.insert_evidence(
        "evt_m",
        {"platform": "forum", "source": "rootY", "title": "论坛帖",
         "content": "帖称金额显著放大", "lineage_root": "rootY",
         "author_type": "自媒体", "credibility": "低"}, 3)
    for cid in ("evt_m:C1", d1, d2):
        led.associate(cid, e1, 2)
        led.set_ruling(cid, e1, "支持", 2)
    led.associate("evt_m:C2", e2, 3)
    led.set_ruling("evt_m:C2", e2, "支持", 3)
    led.associate("evt_m:C9", e1, 2)
    led.set_ruling("evt_m:C9", e1, "矛盾", 2)
    led.update_claim_status("evt_m:C1", "已证实", None, 2, e1)
    led.conn.commit()
    return led, d1, d2


def _merge_build(led, config_dir=None):
    return cgr.build_chain_graph({"evt_m": led}, None, "evt_m",
                                 auth_roots=_AUTH,
                                 config_dir=str(config_dir or _CONFIG))


class LineagePureTests(unittest.TestCase):
    """家族判据纯函数(§五-1):撞号闭包∧两两近文双条件;真派生不连坐;
    转生不合;无 parent 不并。"""

    @staticmethod
    def _rows(*specs):
        out = []
        for cid, text, parent, reborn in specs:
            out.append({"claim_id": cid, "text": text,
                        "parent_claim_id": parent,
                        "reborn_from": reborn or ""})
        return out

    def test_chain_family_merged(self):
        rows = self._rows(
            ("e:c17", "B公司中标XX项目金额45232元", "", ""),
            ("e:c17a", "B公司中标XX项目金额45232元。", "e:c17", ""),
            ("e:c17aa", "B公司中标XX项目金额45232元(复核)", "e:c17a", ""))
        fams = cgr.lineage_families(rows)
        self.assertEqual(fams, [["e:c17", "e:c17a", "e:c17aa"]])

    def test_dissimilar_not_merged(self):
        rows = self._rows(
            ("e:C1", "B公司中标XX项目金额45232元", "", ""),
            ("e:C1d", "C公司在美国提起反垄断诉讼并要求禁售", "e:C1", ""))
        self.assertEqual(cgr.lineage_families(rows), [])

    def test_no_parent_not_merged(self):
        rows = self._rows(
            ("e:C1", "B公司中标XX项目金额45232元", "", ""),
            ("e:C5", "B公司中标XX项目金额45232元", "", ""))
        self.assertEqual(cgr.lineage_families(rows), [])

    def test_dissimilar_origin_no_cascade(self):
        """忻州实账形态:撞号分量含真异文原条(语义演进)→异文原条独立,
        近文后代子簇照常合并(近文关系自身成簇,不连坐整族)。"""
        rows = self._rows(
            ("e:c17", "本次代州黄酒产业学院建设物资采购属于忻州职业技术学院已公开的既定建设规划。", "", ""),
            ("e:c17a", "忻州职业技术学院已就代州黄酒产业学院建设物资采购项目发布公开招标公告。", "e:c17", ""),
            ("e:c17aa", "忻州职业技术学院已公开招标代州黄酒产业学院建设物资采购项目。", "e:c17", ""),
            ("e:c17aaa", "忻州职业技术学院已就代州黄酒产业学院建设物资采购项目公开招标。", "e:c17", ""))
        fams = cgr.lineage_families(rows)
        self.assertEqual(fams, [["e:c17a", "e:c17aa", "e:c17aaa"]])

    def test_explicit_derive_not_in_closure(self):
        """真派生(显式 parent,id 无撞号尾缀形态)不进家族闭包——不连坐
        家族合并(§五 病灶=撞号,真派生是推理谱系)。"""
        rows = self._rows(
            ("e:C1", "B公司中标XX项目金额45232元", "", ""),
            ("e:C1d", "B公司中标XX项目金额45232元。", "e:C1", ""),
            ("e:C5", "B公司中标XX项目且金额显著放大", "e:C1", ""))
        fams = cgr.lineage_families(rows)
        self.assertEqual(fams, [["e:C1", "e:C1d"]])

    def test_reborn_excluded(self):
        rows = self._rows(
            ("e:C1", "B公司中标XX项目金额45232元", "", ""),
            ("e:C1d", "B公司中标XX项目金额45232元。", "e:C1", "解读:e:C9"))
        self.assertEqual(cgr.lineage_families(rows), [])


class LineageMergeTests(unittest.TestCase):
    """谱系归并投影(§五):1 卡/家族+代表=纯最新代+史行全轨迹+族内前提
    边消失跨族保留+JSON/rows 全量+总览注记+台账零写入。"""

    def test_rep_latest_generation_and_projection(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        self.assertEqual(g["meta"]["n_lineage_families"], 1)
        self.assertEqual(g["meta"]["n_lineage_merged"], 2)
        self.assertEqual(g["meta"]["n_claims"], 5)          # JSON 全量不动
        self.assertEqual(len(g["rows"]), 5)                 # rows 全量不动
        fr = {c["claim_id"]: c["family_rep"] for c in g["nodes"]["claims"]}
        self.assertEqual(fr["evt_m:C1"], d2)                # 代表=纯最新代
        self.assertEqual(fr[d1], d2)
        self.assertEqual(fr[d2], "")                        # 代表自身无 rep
        cards = csv_.chain_cards(g)
        ids = [c["id"] for c in cards["claims"]]
        self.assertIn(d2, ids)
        self.assertNotIn("evt_m:C1", ids)                   # 旧代不单独成卡
        self.assertNotIn(d1, ids)
        # 家族卡证据=成员证据并(去重),仍挂 rootX 件
        card = next(c for c in cards["claims"] if c["id"] == d2)
        self.assertEqual(len(card["evidence"]), 1)          # 三代同件→1 条
        self.assertEqual(card["evidence"][0]["relation"], "佐证")

    def test_rep_status_is_latest_generation(self):
        """拍板①:卡面状态=时间最新代真实状态(C1 已证实不掩盖 C1d2 待证实)。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        card = next(c for c in csv_.chain_cards(g)["claims"] if c["id"] == d2)
        self.assertEqual(card["status"], "待证实")
        # 行式链与缺口退场(2026-11-09):状态行四型徽章与计数撤出,回退
        # 「〔待查证〕+缺口型短语」(旧断言「〔待查证·孤证〕」随语义演进
        # 同步注明,照三阶段先例)
        self.assertIn("〔待查证〕单一来源", card["status_line"])

    def test_family_history_full_track(self):
        """史行=家族全轨迹:三代立项+查实并存(已证实历史不丢)。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        card = next(c for c in csv_.chain_cards(g)["claims"] if c["id"] == d2)
        self.assertEqual(card["history"].count("立项"), 3)
        self.assertIn("查实", card["history"])

    def test_intra_family_derive_hidden_cross_kept(self):
        """JSON derives 全量保留;图上前提线退役(批1.5 拍板①),前提
        信息收编命题卡前提行(真派生跨族边=C2 卡「前提: C1」短 id)。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        json_pairs = [(x["src"], x["dst"]) for x in g["edges"]["derives"]]
        self.assertIn(("evt_m:C1", d1), json_pairs)          # JSON 全量
        self.assertIn(("evt_m:C1", "evt_m:C2"), json_pairs)  # 真派生保留
        dot = csv_._dot_source(g)
        self.assertEqual(dot.count('[label="前提"'), 0)      # 前提线退役
        c2 = next(c for c in csv_.chain_cards(g)["claims"]
                  if c["id"] == "evt_m:C2")
        self.assertEqual(c2["premises"], ["C1"])   # C1 无 gist→短 id 定位符

    def test_overview_note(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        text = "\n".join(csv_.overview_lines(g))
        self.assertIn("2 条跨轮同文谱系命题已归并为 1 卡", text)
        self.assertIn("正本仍在台账", text)

    def test_ledger_zero_write(self):
        """负测试:归并/汇流全在装配内存,台账零写入。"""
        led, d1, d2 = _merge_fixture()

        def _snap():
            tables = ("claims", "claim_transitions", "evidence",
                      "claim_evidence")
            return [led.conn.execute(f"SELECT * FROM {t}").fetchall()
                    for t in tables]
        before = _snap()
        _merge_build(led)
        self.assertEqual(before, _snap())


class FanoutMergeTests(unittest.TestCase):
    """扇出汇流(§六):(证据,关系词,目标组)≥fanin_merge_min→1 条汇流边;
    模式表改表即生效+缺键=关;异关系不并;JSON 全量;两引擎一致。"""

    def test_merge_metadata_and_json_full(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        ms = g["fanout_merges"]
        self.assertEqual(len(ms), 1)
        m = ms[0]
        self.assertEqual(m["relation"], "佐证")             # 单根非权威
        self.assertEqual(m["group"], "关于B新能源科技有限公司的事实")
        self.assertEqual(m["n"], 3)
        # members=投影口径(被并边的目标卡=家族代表;边数明细在 n 与
        # JSON supports 全量:三代各自→E1 三条边都在)
        self.assertEqual(m["members"], [d2])
        self.assertEqual(len(g["edges"]["supports"]), 5)    # 3 佐证+C2+1 反证全量
        self.assertEqual(g["meta"]["n_fanout_merged_edges"], 2)

    def test_threshold_param_table_and_missing_key(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        self.assertEqual(len(g["fanout_merges"]), 1)        # 首批 3:3 边并
        with tempfile.TemporaryDirectory() as td:
            rules = cgr.load_present_rules(str(_CONFIG))
            rules["fanin_merge_min"] = 5                    # 改表收紧
            (Path(td) / "claim_present_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            g5 = _merge_build(led, config_dir=td)
            self.assertEqual(g5["fanout_merges"], [])       # 不足阈值不并
            del rules["fanin_merge_min"]                    # 缺键=汇流关
            (Path(td) / "claim_present_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            g0 = _merge_build(led, config_dir=td)
            self.assertEqual(g0["fanout_merges"], [])

    def test_diff_relation_not_merged(self):
        """同证据同组的反证边(异关系词)不并入佐证汇流。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        self.assertTrue(all(m["relation"] != "反证" for m in g["fanout_merges"]))

    def test_dot_source_merged_edge(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        dot = csv_._dot_source(g)
        self.assertIn("佐证 ×3", dot)
        eid = g["fanout_merges"][0]["evidence_id"]
        ev_node = next(f"EV{i}" for i, e in enumerate(csv_.chain_cards(g)["evidence"])
                       if e["id"] == eid)
        self.assertIn(f"{ev_node} -> GA", dot)              # 汇流边入组锚点
        self.assertIn('lhead="cluster_g', dot)              # 批1.5:箭头贴框边
        # 成员边不逐条画:该证据仅剩反证一条逐条边(异关系词不并)
        self.assertEqual(dot.count(f"{ev_node} -> CL"), 1)

    def test_fallback_consistent_with_dot(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        svg = csv_.render_chain_svg_fallback(g)
        self.assertIn("佐证 ×3", svg)
        num = g["fanout_merges"][0]["evidence_id"].rsplit(":", 1)[-1]
        # 该证据卡=组内首行汇流 1 次+反证逐条边 1 次(异关系词不并)=2
        self.assertEqual(svg.count(f"[{num}]"), 2)
        self.assertIn("反证", svg)                          # 红线保留

    def test_legend_and_merge_note_in_html(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        html = csv_.main_view_html(g)
        self.assertIn("×N", html)                           # 汇流图例注记
        self.assertIn("同文谱系命题已归并", html)            # 归并图例注记


class FullTextTests(unittest.TestCase):
    """全文呈现(§四):无 gist 命题 label 全文(裁断退役);机器对账投影
    (证据 label 裁宽)保留——两态各锁。"""

    def test_no_gist_full_label(self):
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        c = next(x for x in g["nodes"]["claims"] if x["claim_id"] == d2)
        self.assertNotIn("…", c["label"])
        self.assertEqual(c["label"], "B公司中标XX项目金额45232元(复核)")
        c1 = next(x for x in g["nodes"]["claims"]
                  if x["claim_id"] == "evt_m:C1")
        self.assertEqual(c1["label"], c1["text"])

    def test_machine_projection_clip_kept(self):
        """机器对账通道不动:证据节点 label 裁宽(CLIP_EV)仍在,「…」保留;
        人看材料概括(summary)全文。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        ev = g["nodes"]["evidence"][0]
        self.assertIn("…", ev["label"])                     # 机器投影裁断保留
        self.assertNotIn("…", ev["summary"])                # 人看概括全文
        self.assertIn("…", cgr._render_mermaid(g))          # mermaid 附录同态


class LineServiceTests(unittest.TestCase):
    """批1.5 线务(2026-11-07):前提边退役下图收编卡内+框附着 lhead+
    边标签 flip 优先(拍板①③)。"""

    def test_premise_line_retired_card_line_kept(self):
        """dot/fallback 两引擎前提线均退役;前提信息在卡「前提:」行
        (gist 优先/短 id 兜底)。"""
        led, d1, d2 = _merge_fixture()
        g = _merge_build(led)
        dot = csv_._dot_source(g)
        self.assertEqual(dot.count('[label="前提"'), 0)
        svg = csv_.render_chain_svg_fallback(g)
        self.assertIn("前提: C1", svg)                        # 卡内前提行
        self.assertEqual(svg.count("前提</text>"), 0)         # 旧线标签消失

    def test_md_chain_premise_note(self):
        led, d1, d2 = _merge_fixture()
        md = csv_.render_md_chain(_merge_build(led))
        self.assertIn("(前提: C1)", md)

    def test_vertical_main_svg_unaffected(self):
        """纵向主图消费 nodes.claims(无 premises 键)=零影响(既有几何
        断言另锁纵向前提走廊线)。"""
        led, d1, d2 = _merge_fixture()
        svg = csv_.render_main_svg(_merge_build(led))
        self.assertNotIn("前提: ", svg)


# ---------------------------------------------------------------- 批2 F 线

_FUSION_TEXT = "B公司中标事实获2路独立材料共同印证"


def _put_fusion(kbp: Path, *, claim="evt_v3:C1",
                members=("evt_v3:E1", "evt_v3:E2"),
                text=_FUSION_TEXT, fp_bad=False) -> str:
    """命题级 fusion 旁车行(等效 chain-gen 产物,零 LLM)。返回 sid。"""
    led = _fixture()
    fps = {}
    for r in led.conn.execute(
            "SELECT evidence_id, title, content FROM evidence"
            " WHERE event_id='evt_v3'").fetchall():
        if r["evidence_id"] in set(members):
            fps[r["evidence_id"]] = cgr.ev_summary_fp(r["title"] or "",
                                                      r["content"] or "")
    ms = sorted(members)
    sid = "fusion:" + hashlib.sha256(
        json.dumps([claim, *ms]).encode("utf-8")).hexdigest()[:16]
    conn = cg.open_insights_rw(kbp)
    try:
        cg._put(conn, sid=sid, event_id="evt_v3", kind="fusion",
                claim_id=claim, hypothesis_id="",
                value=json.dumps({"claim": claim, "text": text, "members": ms},
                                 ensure_ascii=False, sort_keys=True),
                fingerprint="bad-fp" if fp_bad else cgr.fusion_fp(ms, fps),
                model="fake-model")
    finally:
        conn.close()
    return sid


class FusionGateTests(unittest.TestCase):
    """六重机器闸纯函数(批2 §四 F1):成员幻觉/全覆盖/不重复/最小件数/
    数字保真/形态闸逐一可拒;合法通过。"""

    _MATS = {"evt_v3:E1": {"title": "中标公告",
                           "content": "公告载明B公司中标金额45232元"},
             "evt_v3:E2": {"title": "政采公告",
                           "content": "政采公告:B公司中标"}}

    def _v(self, text, members):
        return cg.validate_fusion_item(text, members, self._MATS)

    def test_valid_passes(self):
        self.assertEqual(self._v("B公司中标金额45232元获2路独立材料共同印证",
                                 ["evt_v3:E1", "evt_v3:E2"]), "")

    def test_gate_hallucination_and_coverage(self):
        self.assertEqual(self._v("x", ["evt_v3:E1", "evt_v3:E99"]), "成员幻觉")
        self.assertEqual(self._v("x", ["evt_v3:E2"]), "最小件数")   # 1 件<2
        self.assertEqual(self._v("x", ["evt_v3:E1"]), "最小件数")

    def test_gate_dup_and_min(self):
        self.assertEqual(self._v("x", ["evt_v3:E1", "evt_v3:E1"]), "重复成员")

    def test_gate_number_fidelity(self):
        # 变形(千位归一后=45232 算保真;虚构新数字=失真)
        self.assertEqual(self._v("B公司中标金额45,232元获2路材料共同印证",
                                 ["evt_v3:E1", "evt_v3:E2"]), "")
        self.assertEqual(self._v("B公司中标金额9988元获2路材料共同印证",
                                 ["evt_v3:E1", "evt_v3:E2"]), "数字失真")

    def test_gate_list_form(self):
        # 分号并列 ≥2(3 段)=机械拼接
        self.assertEqual(self._v("公告载明中标;政采公告载明中标;另称拖欠",
                                 ["evt_v3:E1", "evt_v3:E2"]), "列表形态")
        # 序号前缀 ≥2
        self.assertEqual(self._v("1.公告载明中标 2.政采公告确认",
                                 ["evt_v3:E1", "evt_v3:E2"]), "列表形态")
        # 来源逐条复述即便不用分号也拒(用户实图抓到的「换标点罗列」漏洞)
        self.assertEqual(self._v("论坛帖称中标,公告亦确认",
                                 ["evt_v3:E1", "evt_v3:E2"]), "非共同归纳")
        self.assertEqual(self._v("B公司中标事实获2路材料共同印证",
                                 ["evt_v3:E1", "evt_v3:E2"]), "")


class FusionGenTests(unittest.TestCase):
    """生成期 ev_fusion(chain-gen-4):触发/落库/幂等/六闸拒收/敏感排除/
    回执耗尽留痕。"""

    def _run(self, replies, *, kbp=None):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kb_path = Path(td) / "kb.db"
            cg.open_insights_rw(kb_path).close()
            stats = cg.run_chain_gen({"evt_v3": led}, kb_path, "evt_v3",
                                     provider=_FakeLLM(list(replies)),
                                     auth_roots=_AUTH, config_dir=_CONFIG)
            conn = cg.open_insights_rw(kb_path)
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM insights WHERE kind='fusion'").fetchall()]
            conn.close()
        return stats, rows

    def test_gen_triggers_and_writes(self):
        # 命题级触发:C1 有 E1/E2 两件支持且 rootA/ccgp 双独立根;
        # C2/C5/C8 均单根,C3 单件,故唯一触发=C1。
        good = {"items": [{"claim": "evt_v3:C1", "text": _FUSION_TEXT,
                           "members": ["evt_v3:E1", "evt_v3:E2"]}]}
        replies = [{"items": []}, {"items": []}, {"items": []},
                   {"groups": []}, {"chapter": "", "walkthrough": ""},
                   good]
        stats, rows = self._run(replies)
        self.assertEqual(stats["fusion_total"], 1)
        self.assertEqual(stats["fusion_written"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt_version"], "chain-gen-4")
        v = json.loads(rows[0]["value"])
        self.assertEqual(v["claim"], "evt_v3:C1")
        self.assertEqual(sorted(v["members"]), ["evt_v3:E1", "evt_v3:E2"])
        # 幂等:同成员集重跑=同 sid 覆盖(行数不翻倍)
        stats2, rows2 = self._run(replies)
        self.assertEqual(stats2["fusion_written"], 1)
        self.assertEqual(len(rows2), 1)

    def test_gen_gate_rejects_zero_write(self):
        """六闸拒收(幻觉/列表形态/覆盖不全)=invalid 计数零落库。"""
        bads = [
            {"items": [{"claim": "evt_v3:C1", "text": _FUSION_TEXT,
                        "members": ["evt_v3:E1", "evt_v3:E99"]}]},   # 幻觉
            {"items": [{"claim": "evt_v3:C1",
                        "text": "公告载明中标;政采公告载明;论坛帖称拖欠",
                        "members": ["evt_v3:E1", "evt_v3:E2"]}]},     # 列表形态
            {"items": [{"claim": "evt_v3:C1", "text": _FUSION_TEXT,
                        "members": ["evt_v3:E1"]}]},                   # 最小件数
        ]
        for bad in bads:
            replies = [{"items": []}, {"items": []}, {"items": []},
                       {"groups": []}, {"chapter": "", "walkthrough": ""},
                       bad]
            stats, rows = self._run(replies)
            self.assertEqual(stats["fusion_written"], 0, bad)
            self.assertEqual(stats["fusion_invalid"], 1, bad)
            self.assertEqual(rows, [], bad)

    def test_gen_exhausted_reply_error_kept(self):
        """回执耗尽(融合步)=fusion_error 留痕零落库(失败不伪装)。"""
        replies = [{"items": []}, {"items": []}, {"items": []},
                   {"groups": []}, {"chapter": "", "walkthrough": ""}]
        stats, rows = self._run(replies)
        self.assertEqual(stats["fusion_total"], 1)
        self.assertEqual(stats["fusion_written"], 0)
        self.assertIn("回执耗尽", stats["fusion_error"])
        self.assertEqual(rows, [])


class FusionRenderTests(unittest.TestCase):
    """消费与呈现(F2):graph fusions 顶键/归属反查/边与并边/漂移与改表
    收紧/三载体渲染/无旁车零痕迹。"""

    def _build(self, *, fp_bad=False, members=("evt_v3:E1", "evt_v3:E2"),
               config_dir=None):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = _migrated_kb(led, Path(td) / "kb.db")
            _put_fusion(kbp, members=members, fp_bad=fp_bad)
            kb = sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = cgr.build_chain_graph(
                {"evt_v3": led}, kb, "evt_v3", auth_roots=_AUTH,
                config_dir=str(config_dir or _CONFIG))
            kb.close()
        return g

    def test_fusion_graph_topkey_and_edges(self):
        g = self._build()
        self.assertEqual(g["meta"]["n_fusion_cards"], 1)
        self.assertEqual(g["meta"]["n_fusion_drifted"], 0)
        fu = g["fusions"][0]
        self.assertEqual(fu["n"], 2)
        self.assertEqual(fu["anchors"], ["E1", "E2"])
        self.assertEqual(fu["claim_id"], "evt_v3:C1")
        self.assertEqual(fu["group"], "关于B新能源科技有限公司的事实")
        self.assertEqual(fu["relation"], "印证")
        cards = csv_.chain_cards(g)
        self.assertEqual(cards["fusions"][0]["id"], "FU0")
        # 人看主图中 E1/E2 卡和逐条边被融合卡替代;JSON supports 全量仍在
        c1 = next(c for c in cards["claims"] if c["id"] == "evt_v3:C1")
        self.assertEqual(c1["evidence"], [])
        self.assertTrue({"evt_v3:E1", "evt_v3:E2"} <=
                        {e["src"] for e in g["edges"]["supports"]})

    def test_fusion_drift_and_min_gate_config(self):
        g = self._build(fp_bad=True)
        self.assertEqual(g["fusions"], [])
        self.assertEqual(g["meta"]["n_fusion_drifted"], 1)
        # 改表收紧(fusion_min_members=3)→2 件融合行不消费=改表即生效
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "config"
            cfg.mkdir()
            rules = json.loads(
                (_CONFIG / "claim_present_rules.json").read_text(
                    encoding="utf-8"))
            rules["fusion_min_members"] = 3
            (cfg / "claim_present_rules.json").write_text(
                json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            g2 = self._build(config_dir=cfg)
        self.assertEqual(g2["fusions"], [])
        self.assertEqual(g2["meta"]["n_fusion_drifted"], 1)

    def test_fusion_three_carriers_render(self):
        g = self._build()
        src = csv_._dot_source(g)
        self.assertIn("▸ 多源印证 2 路", src)
        self.assertIn("FU0", src)
        self.assertIn("[E1] [E2]", src)
        self.assertIn('xlabel="印证 ×2"', src)
        self.assertNotIn("中标公告(official_website)", src)  # E1 卡折叠
        self.assertNotIn("政采公告(ccgp)", src)              # E2 卡折叠
        fb = csv_.render_chain_svg_fallback(g)
        self.assertIn("▸ 多源印证 2 路", fb)
        md = csv_.render_md_chain(g)
        self.assertIn("▸ 多源印证", md)
        self.assertIn("[E1] [E2]", md)
        html = csv_.main_view_html(g)
        self.assertIn("多源印证 N 件卡", html)   # 图例注记

    def test_fusion_no_sidecar_zero_trace(self):
        led = _fixture()
        g = _build(led)
        self.assertEqual(g["fusions"], [])
        self.assertEqual(g["meta"]["n_fusion_cards"], 0)
        src = csv_._dot_source(g)
        self.assertNotIn("FU0", src)
        self.assertNotIn("多源印证", src)

    def test_conflict_and_sensitive_never_enter_fusion(self):
        """用户实图反馈锁:E11 反证材料不得混入支撑性多源印证卡;
        sensitive 亦不得出境。fusion_scope 只收 ruling=支持∧非敏感。"""
        led = _fixture()
        # 给 C1 再挂一条矛盾和一条敏感支持,触发清单仍只含 E1/E2
        ex = add_evidence(led, "evt_v3", "rootZ", "反向记录", "C1",
                          title="反向登记")
        led.set_ruling("evt_v3:C1", ex, "矛盾", 3)
        es = led.insert_evidence(
            "evt_v3", {"platform": "official_website", "source": "secret",
                       "title": "敏感支持", "content": "内部材料称中标",
                       "lineage_root": "rootS", "author_type": "官方",
                       "credibility": "高", "sensitive": True}, 3)
        led.associate("evt_v3:C1", es, 3)
        led.set_ruling("evt_v3:C1", es, "支持", 3)
        claims = {r["claim_id"]: dict(r) for r in led.all_claims("evt_v3")}
        todo = cg.fusion_scope(led, "evt_v3", claims, fusion_min=2)
        c1 = next(mats for c, mats in todo if c["claim_id"] == "evt_v3:C1")
        self.assertEqual(set(c1), {"evt_v3:E1", "evt_v3:E2"})
        self.assertNotIn(ex, c1)
        self.assertNotIn(es, c1)


class NegativeFlowTests(unittest.TestCase):
    """负测试:insights/链图/渲染器永不回流判决与评分(分权红线)。"""

    def test_verdict_chain_zero_import(self):
        targets = (("stages", "s6_verdict"), ("credibility",),
                   ("predicate_check",), ("stages", "s2_planner"))
        for parts in targets:
            mod = ROOT / "sense2act" / Path(*parts).with_suffix(".py")
            src = mod.read_text(encoding="utf-8")
            for bad in ("chain_gen", "chain_graph", "chain_svg", "insights"):
                self.assertNotIn(bad, src, f"{mod.name} 不得引用 {bad}")


# ---------------------------------------------------------------- 批2 R 线

def _rline_seeds(td: Path, *, pol: dict | None = None) -> Path:
    """R 线 seeds:H1(机会)人工分组两组[C5=中标信号/C4=解读定性]
    (validate_grouping 恰好覆盖才收);pol=人工 polarity map(draft_id→向)。
    反向标 C5(事实成员)——C4 解读型被分流治理挡在主图外,收束线不可见;
    R1 的拆台信号锚在事实命题上=真实形态。"""
    entry: dict = {"label": "机会", "grouping": [
        {"name": "中标信号", "members": ["C5"]},
        {"name": "解读定性", "members": ["C4"]}]}
    if pol:
        entry["polarity"] = pol
    p = td / "seeds.json"
    p.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    return p


def _rline_put_polarity(kbp: Path, *, value="反向", fp_text=None) -> None:
    """polarity 旁车行(等效 chain-gen 产物;C5→H1)。"""
    led = _fixture()
    c = dict(led.claim_row("evt_v3:C5"))
    conn = cg.open_insights_rw(kbp)
    try:
        cg._put(conn, event_id="evt_v3", kind="polarity", claim_id="evt_v3:C5",
                hypothesis_id="evt_v3:H1", value=value, rationale="解读反向",
                fingerprint=fp_text or cgr.polarity_fp(
                    c["text"], c["status"], c.get("gist") or "", "机会"),
                model="fake-model")
    finally:
        conn.close()


class RLineTests(unittest.TestCase):
    """批2 R 线(§二):R1 极性上收束线(三档消费/反向计数/漂移不标/
    conveys 全量)+R2 关系强度分级线(三档粗细/两引擎一致)+R3 结论卡
    披露(两行模板/无 runner 零痕迹/聚合确定性/总览不受影响)。"""

    def _build_with(self, *, pol=None, sidecar_value=None, fp_bad=False):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            seeds = _rline_seeds(Path(td), pol=pol)
            g = None
            if sidecar_value is not None:
                kbp = Path(td) / "kb.db"
                _rline_put_polarity(kbp, value=sidecar_value,
                                    fp_text=("bad" if fp_bad else None))
                kb = sqlite3.connect(kbp)
                kb.row_factory = sqlite3.Row
                try:
                    g = cgr.build_chain_graph(
                        {"evt_v3": led}, kb, "evt_v3", auth_roots=_AUTH,
                        config_dir=str(_CONFIG), seeds_path=seeds)
                finally:
                    kb.close()
            else:
                g = cgr.build_chain_graph(
                    {"evt_v3": led}, None, "evt_v3", auth_roots=_AUTH,
                    config_dir=str(_CONFIG), seeds_path=seeds)
            return g

    # ---- R1 极性上收束线

    def test_polarity_tiers_manual_over_sidecar_over_none(self):
        """三档消费(人工>旁车>无):人工反向命中=组 n_rev 计数;旁车档
        独立可观测(无 seeds polarity 时旁车行生效);无标=全 none。"""
        # 人工档:C5 反向(组「中标信号」)
        g = self._build_with(pol={"C5": "反向"})
        gp = g["argument"]["group_polarity"]
        self.assertEqual([x["group"] for x in gp], ["中标信号", "解读定性"])
        self.assertEqual(gp[0], {"group": "中标信号", "n_pos": 0,
                                 "n_rev": 1, "n_none": 0})
        self.assertEqual(gp[1], {"group": "解读定性", "n_pos": 0,
                                 "n_rev": 0, "n_none": 1})
        self.assertEqual(g["argument"]["ticket"],
                         {"support": 0, "counter": 1, "checking": 1})
        # 旁车档:seeds 无 polarity,旁车行(指纹有效)生效
        g2 = self._build_with(sidecar_value="反向")
        gp2 = g2["argument"]["group_polarity"]
        self.assertEqual(gp2[0]["n_rev"], 1)
        conveys = {(e["src"], e["dst"]): e for e in g2["edges"]["conveys"]}
        self.assertEqual(conveys[("evt_v3:C5", "evt_v3:H1")]["manual"], False)
        # 无标档:全 none=ticket checking
        g3 = self._build_with()
        gp3 = g3["argument"]["group_polarity"]
        self.assertEqual(sum(x["n_rev"] for x in gp3), 0)
        self.assertEqual(g3["argument"]["ticket"],
                         {"support": 0, "counter": 0, "checking": 2})

    def test_manual_beats_sidecar(self):
        """人工优先:seeds polarity 与旁车行同在→人工值生效(conveys
        manual=True)。"""
        g = self._build_with(pol={"C5": "反向"}, sidecar_value="正向")
        conveys = {(e["src"], e["dst"]): e for e in g["edges"]["conveys"]}
        self.assertEqual(conveys[("evt_v3:C5", "evt_v3:H1")]["polarity"],
                         "反向")
        self.assertTrue(conveys[("evt_v3:C5", "evt_v3:H1")]["manual"])

    def test_drift_sidecar_not_marked(self):
        """漂移不标:旁车指纹失效→polarity=None(不冒充),组不算反向,
        收束线保持灰实线现状。"""
        g = self._build_with(sidecar_value="反向", fp_bad=True)
        gp = g["argument"]["group_polarity"]
        self.assertEqual(sum(x["n_rev"] for x in gp), 0)
        self.assertEqual(g["meta"]["sidecar_drifted"], 1)
        src = csv_._dot_source(g)
        self.assertNotIn("含反向", src)

    def test_counter_line_and_conveys_untouched(self):
        """含反向组收束线:dot 源标「● 含反向 ×1」+arrowhead=dot;
        fallback 同款小字+arrDot 红圆点;conveys JSON 全量不动(极性/
        rationale/manual 字段原样,加法式只新增 argument 键)。"""
        g = self._build_with(pol={"C5": "反向"})
        src = csv_._dot_source(g)
        self.assertIn("含反向 ×1", src)
        self.assertIn("arrowhead=dot", src)
        fb = csv_.render_chain_svg_fallback(g)
        self.assertIn("● 含反向 ×1", fb)
        self.assertIn("url(#arrDot)", fb)
        # 被分流的「解读定性」组(成员全为解读型)不渲染组卡=无收束线
        # (灰实线现状由 test_drift 隐式锁:无反向=无「含反向」标注)
        self.assertNotIn("解读定性", src)
        # conveys 全量不动
        c5 = next(e for e in g["edges"]["conveys"]
                  if e["src"] == "evt_v3:C5")
        self.assertEqual(c5["polarity"], "反向")
        self.assertEqual(c5["manual"], True)
        self.assertEqual(set(c5), {"src", "dst", "polarity", "rationale",
                                   "manual"})

    # ---- R2 关系强度分级线

    def test_line_width_tiers_dot(self):
        """dot 源=ortho+xlabel 外置标签(治交叉弧线/标签飘离),粗细三档:
        直接证明 3.0/印证 2.0/佐证 1.2;反证 2.5。"""
        g = _build(_fixture())
        src = csv_._dot_source(g)
        self.assertIn("splines=ortho", src)
        self.assertIn('xlabel="直接证明"', src)
        self.assertNotIn(' [label="直接证明"', src)
        self.assertIn("penwidth=3.0", src)      # e2 ccgp→C1 直接证明
        self.assertIn("penwidth=2.0", src)      # e1 rootA→C1 印证
        self.assertIn("penwidth=1.2", src)      # e3 rootC→C2 佐证
        led, _d1, _d2 = _merge_fixture()
        src2 = csv_._dot_source(_merge_build(led))
        self.assertIn('penwidth=2.5', src2)     # e1→C9 反证(红线不靠粗细分级)
        self.assertIn("penwidth=1.2", src2)     # e1→C1 族 佐证

    def test_line_width_fallback_consistent(self):
        """fallback stroke-width 同三档=两引擎一致;汇流边按关系词同档。"""
        g = _build(_fixture())
        fb = csv_.render_chain_svg_fallback(g)
        self.assertIn('stroke-width="3.0"', fb)
        self.assertIn('stroke-width="2.0"', fb)
        self.assertIn('stroke-width="1.2"', fb)
        led, _d1, _d2 = _merge_fixture()
        fb2 = csv_.render_chain_svg_fallback(_merge_build(led))
        self.assertIn('stroke-width="2.5"', fb2)   # 反证
        # 图例补句
        html = csv_.main_view_html(g)
        self.assertIn("线越粗=支撑越硬", html)

    # ---- R3 结论卡披露

    def test_conclusion_runner_and_ticket_lines(self):
        """两行模板:dot 源/fallback/md-chain 三载体同披露;聚合确定性
        (同输入两次构建逐字节一致)。"""
        g = self._build_with()
        self.assertEqual(g["argument"]["ticket"],
                         {"support": 0, "counter": 0, "checking": 2})
        g2 = self._build_with()
        self.assertEqual(g["argument"]["ticket"], g2["argument"]["ticket"])
        src = csv_._dot_source(g)
        self.assertIn("竞争假设:「暴雷」·已证实 1·居次", src)
        self.assertIn("票型:支撑组 0 ·拆台组 0 ·在查组 2", src)
        fb = csv_.render_chain_svg_fallback(g)
        self.assertIn("竞争假设:「暴雷」·已证实 1·居次", fb)
        self.assertIn("票型:支撑组 0 ·拆台组 0 ·在查组 2", fb)
        md = csv_.render_md_chain(g)
        self.assertIn("竞争假设:「暴雷」·已证实 1·居次", md)
        self.assertIn("票型:支撑组 0 ·拆台组 0 ·在查组 2", md)

    def test_no_runner_zero_trace(self):
        """无落选假设=竞争行零痕迹(不冒充);票型行仍在(组聚合独立于
        runner);总览行结构不受影响(ov_hyps 键集合不变)。"""
        led = _fixture()
        led.conn.execute("DELETE FROM hypothesis_claims WHERE hypothesis_id=?",
                         ("evt_v3:H0",))
        led.conn.execute("DELETE FROM hypotheses WHERE hypothesis_id=?",
                         ("evt_v3:H0",))
        led.conn.commit()
        with tempfile.TemporaryDirectory() as td:
            seeds = _rline_seeds(Path(td))
            g = cgr.build_chain_graph(
                {"evt_v3": led}, None, "evt_v3", auth_roots=_AUTH,
                config_dir=str(_CONFIG), seeds_path=seeds)
        src = csv_._dot_source(g)
        self.assertNotIn("竞争假设", src)
        self.assertIn("票型:支撑组 0 ·拆台组 0 ·在查组 2", src)
        ov = g["overview"]["hypotheses"]
        self.assertEqual([h["state"] for h in ov], ["chosen"])
        for h in ov:
            self.assertEqual(set(h), {"hypothesis_id", "label", "state",
                                      "n_verified", "n_members", "score"})

    def test_conclusion_card_model_fields(self):
        """chain_cards 结论卡字段(runners_up/group_polarity/ticket)=三
        投影同源;无论证区=整卡 None(零披露不冒充)。"""
        g = self._build_with()
        cards = csv_.chain_cards(g)
        con = cards["conclusion"]
        self.assertEqual(con["runners_up"],
                         [{"label": "暴雷", "n_verified": 1}])
        self.assertEqual(len(con["group_polarity"]), 2)
        self.assertEqual(con["ticket"],
                         {"support": 0, "counter": 0, "checking": 2})
        led, _d1, _d2 = _merge_fixture()
        cards2 = csv_.chain_cards(_merge_build(led))
        self.assertIsNone(cards2["conclusion"])   # 无论证区=无结论卡


if __name__ == "__main__":
    unittest.main()
