# -*- coding: utf-8 -*-
"""K2 S3 第四通道测试(2026-10-27)。

覆盖(蓝图 §九 K2 验收预注册):
- 三通道检索:归一/字段契约(pno)/simhash 同文;正卡区限定;public 限定;
- lineage 继承:served item 预设原根;S4 守卫现算放行(空预设零差异);
  入账后 independent_roots 不变=「KB 证据不能单独支撑已证实」涌现(§七);
- Executor 注入:kb 件+回执并入观察包,quality 不被 KB 改写;off 零痕迹;
- 单次服务记忆 + hits_max 上限(改表即生效疫苗,照数值参数从规则表纪律);
- 门禁:--kb on 缺库 rc=2;回放配对(录制未开 kb→rc=2;混版本拒服务);
- evidence_index 内容快照(派生侧)与 META_KEYS/RUN_ARGS_FLAG_MAP 疫苗链。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import (  # noqa: E402
    ROOT, make_domain, make_ledger)
from sense2act.kb_derive import (  # noqa: E402
    derive_event, load_rules, open_main_ro, write_kb)
from sense2act.kb_retrieve import (  # noqa: E402
    KbRetrieveError, KbRetriever, kb_version_of)
from sense2act.models.base import ActionType, ClaimCategory  # noqa: E402
from sense2act.models.decision import DecisionSheet, GapStep  # noqa: E402
from sense2act.models.envelope import (  # noqa: E402
    InvestigationEnvelope, RumorSignal)
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402
from sense2act.models.observation import (  # noqa: E402
    CollectReceipt, CollectRequest, EvidenceItemLite, ObservationPackage)
from sense2act.stages.s3_executor import Executor  # noqa: E402
from sense2act.stages.s4_observer import Observer  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]
_CONFIG = _REPO / "data" / "config"
_BUYER = "测试采购中心"


def _db_path(led) -> str:
    return led.conn.execute("PRAGMA database_list").fetchone()[2]


def _envelope(event_id="evt_t"):
    return InvestigationEnvelope(
        kind="rumor", event_id=event_id,
        rumor=RumorSignal(subject_name=_BUYER, summary="测试主体传闻",
                          source="weibo.com", signal_time="2026-10-20",
                          clues=[]))


def _mini_ledger(pending: bool = False):
    """C1 待证实(单根,→未决区)/C2 已证实双根采集件(ccgp+gsxt,→正卡)。"""
    led = make_ledger()
    led.register_event(_envelope(), "竞情", "evt_t")
    bp = AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text=f"{_BUYER}与代理商存在委托关系",
                       category=ClaimCategory.RELATION, section_index=1,
                       priority=3, entity_names=[_BUYER]),
            ClaimDraft(id="C2", text=f"{_BUYER}机房改造项目预算一百万元",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=5, entity_names=[_BUYER]),
        ],
        hypotheses=[], gaps=[])
    led.install_blueprint("evt_t", bp, aliases={})
    add_ev(led, "ccgp", f"公告:{_BUYER}机房改造项目已发布公告", "C1", 1)
    if not pending:
        led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE claim_id='evt_t:C2'")
        led.conn.commit()
        e = add_ev(led, "ccgp", f"公告:{_BUYER}机房改造项目预算一百万元",
                   "C2", 1)
        led.set_ruling("evt_t:C2", e, "支持", 1)
        e = add_ev(led, "gsxt", f"工商登记显示{_BUYER}注册资本伍佰万元",
                   "C2", 1)
        led.set_ruling("evt_t:C2", e, "支持", 1)
    return led


def add_ev(led, root, content, draft, round_no):
    eid = led.insert_evidence("evt_t", {
        "platform": root, "source": root, "title": content[:30],
        "content": content, "lineage_root": root, "author_type": "官方",
        "credibility": "高"}, round_no)
    led.associate(f"evt_t:{draft}", eid, round_no)
    return eid


def _mini_kb(led, event_id="evt_t"):
    rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
    conn = open_main_ro(_db_path(led))
    try:
        res = derive_event(conn, event_id, rules)
    finally:
        conn.close()
    kb = os.path.join(tempfile.mkdtemp(), "kb.db")
    write_kb(kb, [res], [], rules["version"])
    return kb


def _retriever(led=None, pending=False, event_id="evt_t"):
    kb = _mini_kb(led or _mini_ledger(pending), event_id)
    return KbRetriever(kb, _CONFIG)


class TestRetrieverChannels(unittest.TestCase):
    def setUp(self):
        self.ret = _retriever(pending=False)

    def tearDown(self):
        self.ret.close()

    def test_归一通道_正卡证据回灌(self):
        items, report = self.ret.retrieve(f"{_BUYER}预算多少")
        self.assertEqual(report.platform, "kb")
        self.assertEqual(report.status, "success")
        self.assertEqual(len(items), 2)                  # C2 双根都服务
        self.assertTrue(any("机房改造" in i.content for i in items))
        self.assertIn("归一", report.message)

    def test_字段契约_pno锚点_走事件路由(self):
        """项目卡 v1 只挂 event 链:契约通道经事件前缀扫该案正卡 claim。"""
        sys.path.insert(0, str(_REPO / "tests"))
        from test_kb_derive import _tender_ledger
        tled = _tender_ledger()
        tled.conn.execute(
            "UPDATE claims SET status='已证实' WHERE claim_id='evt_p:C1'")
        tled.conn.commit()
        ret = _retriever(tled, event_id="evt_p")
        try:
            items, report = ret.retrieve(
                "该项目预算情况如何", search_context={"project_no": "PRJ-KB-T1"})
            self.assertEqual(report.status, "success")
            self.assertTrue(any("机房改造" in i.content for i in items))
        finally:
            ret.close()

    def test_simhash同文通道_复验回灌(self):
        items, _ = self.ret.retrieve(
            f"公告:{_BUYER}机房改造项目预算一百万元")     # 与证据内容全同=距离0
        self.assertTrue(any("预算一百万元" in i.content for i in items))

    def test_未决区零服务_消费通道纪律(self):
        """§七:只服务正卡区——待证实(未决区)不进 KB 通道。"""
        ret = _retriever(pending=True)
        try:
            items, report = ret.retrieve(f"{_BUYER} 委托关系")
            self.assertEqual(report.status, "empty")
            self.assertEqual(items, [])
        finally:
            ret.close()

    def test_internal零服务_敏感红线(self):
        led = _mini_ledger()
        eid = led.insert_evidence("evt_t", {
            "platform": "internal_history", "source": "internal_history",
            "title": "内部账期记录", "content": f"内部记录:{_BUYER}账期三十天",
            "lineage_root": "internal_history", "credibility": "中",
            "sensitive": 1}, 1)
        led.associate("evt_t:C2", eid, 1)
        ret = _retriever(led)
        try:
            items, _ = ret.retrieve(f"{_BUYER} 账期")
            self.assertFalse(any("账期" in i.content for i in items))
        finally:
            ret.close()

    def test_单次服务记忆(self):
        first, r1 = self.ret.retrieve(_BUYER)
        self.assertTrue(first)
        second, r2 = self.ret.retrieve(_BUYER)
        self.assertEqual(second, [])
        self.assertEqual(r2.status, "empty")

    def test_hits_max上限_改表即生效疫苗(self):
        """数值参数从规则表消费:临时 config 目录改 kb_rules.retrieval
        →检索器行为跟着变(照 ess_rules 纪律,回归必须含改表断言)。"""
        tmp_cfg = Path(tempfile.mkdtemp())
        for f in ("aliases.json", "watchlist.json", "credibility_rules.json"):
            shutil.copy2(_CONFIG / f, tmp_cfg / f)
        rules = json.loads((_CONFIG / "kb_rules.json").read_text(
            encoding="utf-8-sig"))
        rules["retrieval"]["hits_max"] = 1
        (tmp_cfg / "kb_rules.json").write_text(
            json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        ret = KbRetriever(_mini_kb(_mini_ledger()), tmp_cfg)
        try:
            items, _ = ret.retrieve(_BUYER)
            self.assertEqual(len(items), 1)              # 上限=1(表驱动)
        finally:
            ret.close()

    def test_lineage继承_item预设原根(self):
        items, _ = self.ret.retrieve(_BUYER)
        self.assertEqual({i.lineage_root for i in items}, {"ccgp", "gsxt"})


class TestLineageInheritance(unittest.TestCase):
    def test_S4守卫_预设根优先_空预设现算(self):
        led = make_ledger()
        led.register_event(_envelope(), "竞情", "evt_t")
        observer = Observer(led, make_domain())
        obs = ObservationPackage(round=2, main_target="C2", action_brief="t",
                                 items=[
                                     EvidenceItemLite(
                                         platform="kb", source="kb",
                                         content="kb 件甲", lineage_root="gsxt"),
                                     EvidenceItemLite(
                                         platform="ccgp", source="ccgp",
                                         content="普通采集件")])
        processed = observer.process("evt_t", 2, obs)
        by_content = {p["content"]: p for p in processed}
        self.assertEqual(by_content["kb 件甲"]["lineage_root"], "gsxt")
        self.assertEqual(by_content["普通采集件"]["lineage_root"], "ccgp")

    def test_KB复曝_独立根计数不变(self):
        """蓝图 §九 K2 验收②:KB 证据继承原根→independent_roots 不变=
        「KB 证据不能单独支撑已证实」自然涌现(零 S6 改动)。"""
        led = make_ledger()
        led.register_event(_envelope(), "竞情", "evt_t")
        e = add_ev(led, "gsxt", f"登记:{_BUYER}注册资本伍佰万元", "C2", 1)
        led.set_ruling("evt_t:C2", e, "支持", 1)
        before = led.independent_roots("evt_t:C2")
        observer = Observer(led, make_domain())
        obs = ObservationPackage(round=2, main_target="C2", action_brief="t",
                                 items=[EvidenceItemLite(
                                     platform="kb", source="kb",
                                     title=f"登记:{_BUYER}注册资本伍佰万元(转载)",
                                     content=f"登记:{_BUYER}注册资本伍佰万元",
                                     lineage_root="gsxt")])
        item = observer.process("evt_t", 2, obs)[0]
        eid = led.insert_evidence("evt_t", item, 2)
        self.assertIsNotNone(eid)
        led.associate("evt_t:C2", eid, 2)
        led.set_ruling("evt_t:C2", eid, "支持", 2)
        after = led.independent_roots("evt_t:C2")
        self.assertEqual(set(after["支持"]), {"gsxt"})      # 根集合不变
        # 反事实对照:同件若无继承(S4 现算 platform=kb → 根=kb)会涨出
        # 第二根——守卫的存在意义
        item2 = dict(item)
        item2["lineage_root"] = "kb"
        item2["title"] = "无继承对照件"
        eid2 = led.insert_evidence("evt_t", item2, 2)
        if eid2:                                            # 内容不同→可插
            led.associate("evt_t:C2", eid2, 2)
            led.set_ruling("evt_t:C2", eid2, "支持", 2)
            self.assertIn("kb", led.independent_roots("evt_t:C2")["支持"])

    def test_旧fixture零迁移_lineage_root可选(self):
        self.assertEqual(
            EvidenceItemLite(platform="ccgp", source="ccgp",
                             content="x").lineage_root, "")


class _StubGate:
    calls = 0
    remaining = 5

    def send(self, req, *, mode="LIVE"):
        return CollectReceipt(request=req, items=[], source_reports=[],
                              status="empty", tool=req.tool)


class _StubRouter:
    def route(self, *a, **k):
        return CollectRequest(query="q", platforms=["ccgp"])


class _StubExplorer:
    def explore(self, gap, hints, claim_text=""):
        return {"reachable": False, "items": [], "trajectory": []}


def _sheet():
    return DecisionSheet(round=1, main_target="C2", action=ActionType.TOOL,
                         gap_chain=[GapStep(gap=f"{_BUYER}预算",
                                            acceptance="需公告")],
                         search_hints=[_BUYER, "预算"])


class TestExecutorAugment(unittest.TestCase):
    def test_kb件与回执并入_quality不改写(self):
        ret = _retriever()
        try:
            ex = Executor(_StubGate(), _StubRouter(), _StubExplorer(),
                          kb_retriever=ret)
            obs, quality = ex.execute(_sheet(), 1, f"{_BUYER}预算多少")
            self.assertEqual(quality, "poor")               # 网络扑空仍是 poor
            kb_reports = [r for r in obs.source_reports
                          if r.platform == "kb"]
            self.assertEqual(len(kb_reports), 1)
            self.assertEqual(kb_reports[0].status, "success")
            self.assertTrue(any(i.platform == "kb" for i in obs.items))
            self.assertIn("+kb×", obs.action_brief)
        finally:
            ret.close()

    def test_off_不装配_零痕迹(self):
        ex = Executor(_StubGate(), _StubRouter(), _StubExplorer())
        obs, _ = ex.execute(_sheet(), 1, f"{_BUYER}预算多少")
        self.assertFalse(any(r.platform == "kb" for r in obs.source_reports))
        self.assertFalse(any(i.platform == "kb" for i in obs.items))


class TestGatesAndVersions(unittest.TestCase):
    def test_混版本拒服务(self):
        import sqlite3
        kb = _mini_kb(_mini_ledger())
        conn = sqlite3.connect(kb)
        conn.execute("INSERT INTO kb_anchor(event_id,kb_version)"
                     " VALUES('x','kb-9.9')")
        conn.commit()
        conn.close()
        with self.assertRaises(KbRetrieveError):
            kb_version_of(kb)

    def test_缺库拒开(self):
        with self.assertRaises(KbRetrieveError):
            KbRetriever(os.path.join(tempfile.mkdtemp(), "nope.db"))

    def test_cli_kb_on缺库_rc2(self):
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", "examples/supplier_b/envelope.json", "--replay",
             "examples/supplier_b/responses.json", "--seed",
             "examples/supplier_b/evidence_seed.json", "--event-id", "evt_x",
             "--db", os.path.join(tempfile.mkdtemp(), "t.db"),
             "--kb", "on", "--kb-path",
             os.path.join(tempfile.mkdtemp(), "nope.db")],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--kb on 需 KB 库存在", proc.stderr)

    def test_回放配对_录制未开kb_rc2(self):
        """dalian 录制件 meta 无 kb 键(=录制未开)→ --kb on 回放被拦。
        其余参数按录制 meta 对齐(三开关 on),让 kb 守卫成为第一拦截点。"""
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", "eval/bench/tender_dalian_police/envelope.json",
             "--replay", "eval/bench/tender_dalian_police/responses_r1.json",
             "--collect-replay",
             "eval/bench_staging/tender_dalian_police/collection_r1.json",
             "--crawler", "local", "--assoc", "entity_text",
             "--predicate-form", "on", "--corpus-bound-code", "on",
             "--t1-independence", "on",
             "--event-id", "evt_tender_dalian_police_r1",
             "--db", os.path.join(tempfile.mkdtemp(), "t.db"),
             "--kb", "on", "--kb-path", _mini_kb(_mini_ledger())],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=180)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("录制件未开 --kb", proc.stderr)

    def test_off_全链零痕迹_离线回放(self):
        """蓝图 §九 K2 验收①(kb off 断言面):全链零 kb 件零 kb 回执;
        全链零扰动的读数面=eval 三套件逐案 A_w 不变。"""
        db = os.path.join(tempfile.mkdtemp(), "t.db")
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", "examples/supplier_b/envelope.json", "--replay",
             "examples/supplier_b/responses.json", "--seed",
             "examples/supplier_b/evidence_seed.json", "--event-id", "evt_t",
             "--db", db, "--kb", "off"],
            cwd=str(_REPO), capture_output=True, text=True,
            encoding="utf-8", timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        import sqlite3
        conn = sqlite3.connect(db)
        n_kb = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE platform='kb'").fetchone()[0]
        conn.close()
        self.assertEqual(n_kb, 0)


class TestContentSnapshotAndVaccines(unittest.TestCase):
    def test_派生快照_检索自包含(self):
        """evidence_index 内容快照列:派生后 kb.db 自包含(检索零主库依赖)。"""
        ret = _retriever()
        try:
            items, _ = ret.retrieve(_BUYER)
            self.assertTrue(all(i.content for i in items))
            self.assertTrue(any("机房改造" in i.content for i in items))
        finally:
            ret.close()

    def test_META_KEYS疫苗链_kb同源(self):
        from sense2act import evaluate as ev
        from sense2act.run_meta import META_KEYS
        self.assertIn("kb", META_KEYS)
        self.assertIn("kb", ev.RUN_ARGS_FLAG_MAP)
        self.assertEqual(ev.RUN_ARGS_FLAG_MAP["kb"], "--kb")

    def test_loader_retrieval段校验(self):
        d = tempfile.mkdtemp()
        bad = Path(d) / "kb_rules.json"
        doc = json.loads((_CONFIG / "kb_rules.json").read_text(
            encoding="utf-8-sig"))
        doc["retrieval"]["hits_max"] = 0
        bad.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(Exception):
            load_rules(bad, _CONFIG)


if __name__ == "__main__":
    unittest.main()
