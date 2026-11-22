# -*- coding: utf-8 -*-
"""知识库链层测试(C1,2026-10-26)——方案《…链compute_chain-设计方案…》§八
预注册清单 ①-⑮。纪律继承(方案 §七):确定性(排序全显式,同输入逐字节)、
双只读(主库+kb.db 哈希不变)、失败不伪装(缺库/缺件如实报错)、同根折算
(T1 同函数=口径一致)、强度与 ess 重放一致(score_claim 同参)。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import ALIASES, ROOT, add_evidence, install_simple, make_ledger  # noqa: E402
from sense2act import kb_chain as kc  # noqa: E402
from sense2act.credibility import load_rules as load_ess  # noqa: E402
from sense2act.kb_derive import derive_event, load_rules, write_kb  # noqa: E402
from sense2act.kb_store import KbStore  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402
from sense2act.store.ledger import SIMHASH_NEAR_DUP_DIST, LedgerRepository  # noqa: E402

_REPO = ROOT
_CONFIG = _REPO / "data" / "config"
_ESS = load_ess(_CONFIG / "ess_rules.json")
_AUTH = {"ccgp"}


def _db_file(led: LedgerRepository) -> str:
    return led.conn.execute("PRAGMA database_list").fetchone()[2]


class _Base(unittest.TestCase):
    """两 claim 三态齐备的最小台账:C1 已证实(双采集根)/C2 待证实(合格根,
    未决)/C3 待证实(无合格根=OQ 语义)/H0 含 C1+C2。"""

    def setUp(self):
        self.led = make_ledger()
        bp = AnalysisBlueprint(
            claims=[
                ClaimDraft(id="C1", text="B公司中标XX项目", category=ClaimCategory.FACT,
                           section_index=2, priority=5, entity_names=["B公司"]),
                ClaimDraft(id="C2", text="B公司拖欠工资", category=ClaimCategory.FACT,
                           section_index=2, priority=5, entity_names=["B公司"]),
                ClaimDraft(id="C3", text="B公司涉及海外诉讼", category=ClaimCategory.FACT,
                           section_index=2, priority=4, entity_names=["B公司"]),
            ],
            hypotheses=[],
            gaps=[])
        self.led.register_event(_rumor_env(), "", "evt_t")
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)
        # H0 = C1 AND C2(假设链fixture);人裁不动
        self.led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES('evt_t:H0','evt_t','暴雷','d',0,0)")
        for cid in ("evt_t:C1", "evt_t:C2"):
            self.led.conn.execute(
                "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
                " VALUES('evt_t:H0',?,'AND')", (cid,))
        self.led.conn.commit()
        # C1:两独立采集根+判决已证实(带 transition)
        e1 = add_evidence(self.led, "evt_t", "rootA", "公告载明B公司中标",
                          "C1", round_no=1, platform="ccgp")
        e2 = add_evidence(self.led, "evt_t", "rootB", "另一来源:B公司中标",
                          "C1", round_no=1)
        self.led.set_ruling("evt_t:C1", e1, "支持", 1)
        self.led.set_ruling("evt_t:C1", e2, "支持", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1, e1)
        # C2:单采集根(孤证,待证实)
        add_evidence(self.led, "evt_t", "rootC", "论坛帖称B公司拖欠工资",
                     "C2", round_no=1)
        # C3:纯种子根(无合格根→状态则不过→OQ 挂载对象)
        add_evidence(self.led, "evt_t", "signal_layer", "信封摘要称涉诉",
                     "C3", round_no=0, platform="signal_layer")
        self.mains = kc.open_mains([_db_file(self.led)], _CONFIG)
        self.kb_path = None
        self.kb = None

    def tearDown(self):
        kc.close_mains(self.mains)
        if self.kb is not None:
            self.kb.close()
        for p in (self.kb_path,):
            if p and os.path.exists(p):
                os.unlink(p)

    def _kb(self) -> sqlite3.Connection:
        """派生一次→kb.db ro 打开(链消费入口)。"""
        if self.kb is not None:
            return self.kb
        rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
        meta = {"predicate_form": True, "corpus_bound_code": True,
                "t1_independence": True, "recorded_at": "2026-10-26T00:00:00+00:00"}
        res = derive_event(self.led.conn, "evt_t", rules, meta)
        tmp = tempfile.mkdtemp()
        self.kb_path = os.path.join(tmp, "kb.db")
        write_kb(self.kb_path, [res], [], rules["version"])
        self.kb = kc.open_kb_ro(self.kb_path)
        return self.kb

    def _ctx(self):
        return {"ess_rules": _ESS, "auth_roots": _AUTH,
                "near_dup_dist": SIMHASH_NEAR_DUP_DIST}


def _rumor_env(event_id="evt_t"):
    from helpers import rumor_envelope
    return rumor_envelope(event_id)


# ---------------------------------------------------------------- 内核 ①-④

class TestCore(unittest.TestCase):
    def setUp(self):
        self.b = _Base("setUp")
        self.b.setUp()

    def tearDown(self):
        self.b.tearDown()

    def test_01_内核六步逐字段(self):
        """§八-①:证据集/根折并/强度/状态史逐字段断言(现有台账 fixture)。"""
        core = kc.chain_core(self.b.mains, self.b._kb(), "evt_t:C1", **self.b._ctx())
        self.assertEqual(core["claim"]["status"], "已证实")
        self.assertEqual(len(core["evidence"]), 2)
        self.assertTrue(all(e["ruling"] == "支持" for e in core["evidence"]))
        self.assertEqual(len(core["roots"]["支持"]["独立"]), 2)
        self.assertEqual(core["roots"]["支持"]["折并"], 0)
        self.assertEqual(core["strength"]["anchor_name"], "中(双根普通)")
        self.assertEqual(core["transitions"][0]["new_status"], "已证实")
        self.assertTrue(core["cards"], "kb.db 有挂载时应出卡")

    def test_02_as_of_round回放对transitions(self):
        """§八-②:as_of_round 回放正确性(对照 transitions 重放)。"""
        full = kc.chain_core(self.b.mains, None, "evt_t:C1")
        self.assertEqual(full["claim"]["status"], "已证实")
        past = kc.chain_core(self.b.mains, None, "evt_t:C1", as_of_round=0)
        self.assertEqual(past["claim"]["status"], "待证实")
        self.assertEqual(past["transitions"], [], "回放不看到未来")
        self.assertEqual(past["evidence"], [], "轮前挂账不出现")
        self.assertEqual(past["roots"]["支持"]["独立"], [])

    def test_03_根折并与判决侧同函数(self):
        """§八-③:T1 内容级折并——同文转载两根折一(与判决侧同函数)。"""
        e3 = add_evidence(self.b.led, "evt_t", "rootA2",
                          "公告载明B公司中标", "C1", round_no=2)
        self.b.led.set_ruling("evt_t:C1", e3, "支持", 2)
        core = kc.chain_core(self.b.mains, None, "evt_t:C1", **self.b._ctx())
        self.assertEqual(core["roots"]["支持"]["原始"], ["rootA", "rootA2", "rootB"])
        self.assertEqual(core["roots"]["支持"]["独立"], ["rootA", "rootB"])
        self.assertEqual(core["roots"]["支持"]["折并"], 1)

    def test_04_强度与ess重放一致(self):
        """§八-④:score_claim 同参(ess_rules+as_of)重放,逐条一致。"""
        from sense2act.credibility import collect_factors, score_claim
        as_of = datetime(2026, 10, 26, tzinfo=timezone.utc)
        core = kc.chain_core(self.b.mains, None, "evt_t:C1", as_of=as_of,
                             **self.b._ctx())
        f = [x for x in collect_factors(self.b.led, "evt_t")
             if x["claim_id"] == "evt_t:C1"][0]
        want = score_claim(f, _ESS, as_of, _AUTH)
        self.assertEqual(core["strength"], want)

    def test_05_ess规则未载如实不装分(self):
        """§七 失败不伪装:ess_rules=None→strength=None,不装 0 分。"""
        core = kc.chain_core(self.b.mains, None, "evt_t:C1", auth_roots=_AUTH)
        self.assertIsNone(core["strength"])

    def test_06_主库无此claim如实报错(self):
        """§七 失败不伪装:主库无此 claim→报错非空链。"""
        with self.assertRaises(kc.KbChainError):
            kc.chain_core(self.b.mains, None, "evt_t:C99")

    def test_07_主库路由歧义与缺库(self):
        with self.assertRaises(kc.KbChainError):
            kc.chain_core({}, None, "evt_t:C1")


# ---------------------------------------------------------------- 投影 ⑤-⑬

class TestProjections(unittest.TestCase):
    def setUp(self):
        self.b = _Base("setUp")
        self.b.setUp()

    def tearDown(self):
        self.b.tearDown()

    def test_08_排序确定性_同输入逐字节(self):
        """§八-⑤:五投影 dict+md 同输入重算逐字节一致。"""
        kb = self.b._kb()
        ctx = self.b._ctx()
        ent = _find_ent(kb, "B新能源科技有限公司")
        checks = [
            ("conclusion", lambda: kc.chain_conclusion(
                self.b.mains, kb, "evt_t:C1", **ctx)),
            ("timeline", lambda: kc.chain_timeline(
                self.b.mains, kb, "evt_t:C1", **ctx)),
            ("topic", lambda: kc.chain_topic(self.b.mains, kb, ent, **ctx)),
            ("hypothesis", lambda: kc.chain_hypothesis(
                self.b.mains, kb, "evt_t", "evt_t:H0", **ctx)),
        ]
        for kind, fn in checks:
            d1, d2 = fn(), fn()
            self.assertEqual(json.dumps(d1, sort_keys=True),
                             json.dumps(d2, sort_keys=True), kind)
            self.assertEqual(kc.render(kind, d1), kc.render(kind, d1), kind)

    def test_09_话题链分区现算_zone漂移对照(self):
        """§八-⑥+§六-决策3:分区用现算状态;快照过期=漂移呈现非矛盾。"""
        kb = self.b._kb()
        ent = _find_ent(kb, "B新能源科技有限公司")
        t = kc.chain_topic(self.b.mains, kb, ent, **self.b._ctx())
        # 现算:C1 已证实(正卡)/C2 待证实(未决)/C3 待证实但无合格根→不进分区
        zones = {m["claim_id"]: m["status"] for m in t["members"]}
        self.assertEqual(zones.get("evt_t:C1"), "已证实")
        self.assertEqual(zones.get("evt_t:C2"), "待证实")
        self.assertNotIn("evt_t:C3", zones, "无合格根不进结论分区(在 OQ 段)")
        # 漂移注入:后台把 C2 翻已证实→快照 zone=未决 与现算(正卡)漂移
        self.b.led.update_claim_status("evt_t:C2", "已证实", None, 2)
        t2 = kc.chain_topic(self.b.mains, kb, ent, **self.b._ctx())
        self.assertGreaterEqual(t2["zone_drift"], 1, "快照漂移必须被计数")
        md = kc.render("topic", t2)
        self.assertIn("漂移", md)

    def test_10_话题链三段与entity_link逐行一致(self):
        """§八-⑦:信号轴/案卷互链/结论分区/OQ 行与 entity_link 逐行一致。"""
        kb = self.b._kb()
        ent = _find_ent(kb, "B新能源科技有限公司")
        links = kb.execute(
            "SELECT ref_kind, ref_id FROM entity_link WHERE entity_id=?",
            (ent,)).fetchall()
        t = kc.chain_topic(self.b.mains, kb, ent, **self.b._ctx())
        self.assertEqual({s["ref_id"] for s in t["signals"]},
                         {l["ref_id"] for l in links if l["ref_kind"] == "signal"})
        self.assertEqual({e["ref_id"] for e in t["events"]},
                         {l["ref_id"] for l in links if l["ref_kind"] == "event"})
        self.assertEqual({m["claim_id"] for m in t["members"]},
                         {l["ref_id"] for l in links if l["ref_kind"] == "claim"})
        self.assertEqual({r["claim_id"] for r in t["oq"]},
                         {l["ref_id"] for l in links if l["ref_kind"] == "oq"})
        # OQ 行=未过状态则的待证实(C3 纯种子)——三态纪律在卡上的投影
        self.assertIn("evt_t:C3", {r["claim_id"] for r in t["oq"]})

    def test_11_未调查项目卡信号轴非空(self):
        """§八-⑧:项目卡未开卷=信号轴非空、结论分区空(K1 信号挂卡直查)。"""
        # rumor 信封无 project_id→补项目锚+挂一条到站信号(manifest 通道)
        self.b.led.conn.execute(
            "UPDATE events SET project_id='PRJ-KB-C1' WHERE event_id='evt_t'")
        self.b.led.conn.commit()
        self.b._kb()                    # 先建一次,拿到路径
        self.b.kb.close(); self.b.kb = None
        rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
        res = derive_event(self.b.led.conn, "evt_t", rules, {})
        write_kb(self.b.kb_path, [res],
                 [{"project_id": "PRJ-KB-C1", "signal_id": "SIG-001",
                   "company_id": ""}], rules["version"])
        kb = kc.open_kb_ro(self.b.kb_path)
        self.b.kb = kb
        rows = kb.execute("SELECT entity_id FROM entity WHERE kind='项目'"
                          ).fetchall()
        self.assertTrue(rows, "项目锚卡须存在")
        t = kc.chain_topic(self.b.mains, kb, rows[0]["entity_id"],
                           **self.b._ctx())
        self.assertEqual([s["ref_id"] for s in t["signals"]], ["SIG-001"],
                         "未开卷项目卡=信号轴非空")
        self.assertEqual(t["members"], [], "未开卷=零结论分区")
        md = kc.render("topic", t)
        self.assertIn("SIG-001", md)

    def test_12_证据履历链反查一致(self):
        """§八-⑨:履历链与 evidence_index/claim_evidence 逐条一致。"""
        kb = self.b._kb()
        fp = kb.execute("SELECT fingerprint FROM evidence_index").fetchone()[0]
        p = kc.chain_provenance(self.b.mains, kb, fp)
        row = kb.execute("SELECT * FROM evidence_index WHERE fingerprint=?",
                         (fp,)).fetchone()
        self.assertEqual(sorted(p["origin_refs"]),
                         sorted(json.loads(row["origin_refs_json"])))
        # 反查引用结论:每条 cited_by 在主库 claim_evidence 可对
        for m in p["cited_by"]:
            got = self.b.led.conn.execute(
                "SELECT ruling FROM claim_evidence WHERE claim_id=? AND"
                " evidence_id=?", (m["claim_id"], m["via_evidence"])).fetchone()
            self.assertIsNotNone(got)
            self.assertEqual(m["ruling"], got["ruling"])
        # 同根成员指纹行数≥1(谱系折算的"数给你看")
        for rt, n in p["sibling_fingerprints"].items():
            self.assertGreaterEqual(n, 1)

    def test_13_指纹不存在如实报错(self):
        kb = self.b._kb()
        with self.assertRaises(kc.KbChainError):
            kc.chain_provenance(self.b.mains, kb, "no_such_fp")

    def test_14_假设链记分与standings逐字段一致(self):
        """§八-⑫:记分行=hypothesis_standings 现成返回(含人裁旗标)。"""
        kb = self.b._kb()
        h = kc.chain_hypothesis(self.b.mains, kb, "evt_t", "evt_t:H0",
                                **self.b._ctx())
        st = [s for s in self.b.led.hypothesis_standings("evt_t")
              if s["hypothesis_id"] == "evt_t:H0"][0]
        self.assertEqual(h["standing"], st)
        self.assertFalse(h["standing"]["adjudicated"], "fixture 无人裁")
        # 人裁钉死后旗标随行(P1-8;只钉假设层不动 claim 层)
        self.b.led.record_adjudication("evt_t", 2, "hypothesis", "evt_t:H0",
                                       "暴雷", selected=True)
        h2 = kc.chain_hypothesis(self.b.mains, kb, "evt_t", "evt_t:H0",
                                 **self.b._ctx())
        self.assertTrue(h2["standing"]["adjudicated"])
        self.assertEqual(h2["standing"]["verified"], 2, "覆写=成员全按已证实")
        md = kc.render("hypothesis", h2)
        self.assertIn("人裁已钉", md)

    def test_15_假设链成员行与结论链同源(self):
        """§八-⑬:成员行=内核一行式(点开=结论链 A)。"""
        kb = self.b._kb()
        h = kc.chain_hypothesis(self.b.mains, kb, "evt_t", "evt_t:H0",
                                **self.b._ctx())
        for m in h["members"]:
            core = kc.chain_core(self.b.mains, kb, m["claim_id"],
                                 **self.b._ctx())
            self.assertEqual(m["status"], core["claim"]["status"])
            self.assertEqual(m["score"],
                             core["strength"]["score"] if core["strength"] else None)
        self.assertEqual(h["ops"], {"evt_t:C1": "AND", "evt_t:C2": "AND"})

    def test_16_假设不存在如实报错(self):
        with self.assertRaises(kc.KbChainError):
            kc.chain_hypothesis(self.b.mains, None, "evt_t", "evt_t:H9")


# ---------------------------------------------------------------- OQ 挂载/敏感/零写入/CLI

class TestOqMountAndSafety(unittest.TestCase):
    def setUp(self):
        self.b = _Base("setUp")
        self.b.setUp()

    def tearDown(self):
        self.b.tearDown()

    def test_17_oq挂载门槛_锚定命中才挂(self):
        """§八-⑩:OQ 挂载=锚定 join 命中∧已注册实体卡;C3(纯种子+锚定命中)
        挂 OQ;案卷卡/项目卡不挂 oq。"""
        kb = self.b._kb()
        oq = kb.execute(
            "SELECT entity_id, ref_id FROM entity_link WHERE ref_kind='oq'"
            " ORDER BY ref_id").fetchall()
        by_ref = {r["ref_id"]: r["entity_id"] for r in oq}
        self.assertIn("evt_t:C3", by_ref)
        ent = _find_ent(kb, "B新能源科技有限公司")
        self.assertEqual(by_ref["evt_t:C3"], ent)
        # C1 已证实/C2 待证实有合格根→走 claim 链接不重复挂 oq
        self.assertNotIn("evt_t:C1", by_ref)
        self.assertNotIn("evt_t:C2", by_ref)
        for r in oq:    # 只挂实体卡
            kind = kb.execute("SELECT kind FROM entity WHERE entity_id=?",
                              (r["entity_id"],)).fetchone()[0]
            self.assertEqual(kind, "实体")

    def test_18_无锚定待证实不挂oq(self):
        """§八-⑩b:主语无锚定命中的待证实→零 oq(从严=不子串宽进)。"""
        self.b.led.add_claim("evt_t", {"id": "C9", "text": "无主语新断言",
                                       "category": "事实"}, 1)
        rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
        res = derive_event(self.b.led.conn, "evt_t", rules, {})
        oq = [l for l in res["links"] if l["ref_kind"] == "oq"
              and l["ref_id"].endswith(":C9")]
        self.assertEqual(oq, [])

    def test_19_敏感件存在性过滤(self):
        """§八-⑪:internal 证据/指纹——md 只出存在性+定位符,内容不出境。"""
        self.b.led.conn.execute(
            "UPDATE evidence SET sensitive=1 WHERE event_id='evt_t'")
        self.b.led.conn.commit()
        kb = self.b._kb()
        core = kc.chain_core(self.b.mains, kb, "evt_t:C1", **self.b._ctx())
        md = kc.render("conclusion", core)
        internal = [e for e in core["evidence"] if e["sensitivity"] == "internal"]
        if internal:
            self.assertNotIn(internal[0]["title"], md)
            self.assertNotIn(internal[0]["content"][:20], md)
            self.assertIn("敏感件", md)
            self.assertIn(internal[0]["evidence_id"], md, "定位符保留")
        # dict 保留数据+标记(K2 消费侧过滤;呈现层过滤已断言)
        self.assertTrue(all("sensitivity" in e for e in core["evidence"]))

    def test_20_双库零写入_哈希不变(self):
        """§八-⑭:链全程零写入——主库+kb.db 前后 sha256 一致。"""
        import shutil
        work = tempfile.mkdtemp()
        db2 = os.path.join(work, "copy.db")
        kb2 = os.path.join(work, "copy_kb.db")
        src = sqlite3.connect(_db_file(self.b.led))
        dst = sqlite3.connect(db2)
        src.backup(dst)          # WAL 安全复制(shutil.copy 拷不到未 checkpoint 页)
        dst.close()
        src.close()
        self.b._kb()
        shutil.copy(self.b.kb_path, kb2)
        h_main = _sha(db2)
        h_kb = _sha(kb2)
        mains = kc.open_mains([db2], _CONFIG)
        kb = kc.open_kb_ro(kb2)
        try:
            ent = _find_ent(kb, "B新能源科技有限公司")
            kc.chain_conclusion(mains, kb, "evt_t:C1", **self.b._ctx())
            kc.chain_topic(mains, kb, ent, **self.b._ctx())
            fp = kb.execute("SELECT fingerprint FROM evidence_index"
                            ).fetchone()[0]
            kc.chain_provenance(mains, kb, fp)
            kc.chain_hypothesis(mains, kb, "evt_t", "evt_t:H0",
                                **self.b._ctx())
        finally:
            kc.close_mains(mains)
            kb.close()
        self.assertEqual(_sha(db2), h_main, "主库零写入")
        self.assertEqual(_sha(kb2), h_kb, "kb.db 零写入")

    def test_21_cli端到端smoke(self):
        """§八-⑮:CLI kb-chain conclusion/topic 端到端(rc=0,输出含关键词)。"""
        import subprocess
        env = dict(os.environ, PYTHONUTF8="1")
        kbp = self.b._kb() and self.b.kb_path    # 确保已建;连接留缓存复用
        cmd = [sys.executable, "-m", "sense2act", "kb-chain",
               "--kind", "conclusion", "--claim", "evt_t:C1",
               "--db", _db_file(self.b.led), "--kb", kbp]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace", cwd=str(_REPO))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("结论链", r.stdout)
        cmd2 = [sys.executable, "-m", "sense2act", "kb-chain",
                "--kind", "topic", "--entity", _find_ent(self.b._kb(),
                                                         "B新能源科技有限公司"),
                "--db", _db_file(self.b.led), "--kb", kbp,
                "--format", "json"]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, env=env,
                            encoding="utf-8", errors="replace", cwd=str(_REPO))
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn('"members"', r2.stdout)
        # 门禁:缺必配参数 rc=2
        r3 = subprocess.run(
            [sys.executable, "-m", "sense2act", "kb-chain",
             "--kind", "conclusion", "--db", _db_file(self.b.led)],
            capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace", cwd=str(_REPO))
        self.assertEqual(r3.returncode, 2)


# ---------------------------------------------------------------- 工具

def _sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _find_ent(kb: sqlite3.Connection, canonical: str) -> str:
    for r in kb.execute("SELECT entity_id, names_json FROM entity"):
        if json.loads(r["names_json"]).get("canonical") == canonical:
            return r["entity_id"]
    raise AssertionError(f"卡不存在: {canonical}")


# _Base 复用挂到类上(测试方法分组归属两类,共享装配)
_Base._kb_path = lambda self: self.kb_path

if __name__ == "__main__":
    unittest.main()
