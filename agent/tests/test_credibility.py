"""ESS 证据强度分(credibility.py)测试(方案 v0.3 §十五验收:阶段一)。

覆盖:锚点判定表驱动 / 修正与夹取(方案 §4.7 三走例)/ 六公理 property
(P1-P5 计分公理 + P6 S6 隔离负测试)/ 与 ledger.independent_roots 口径一致 /
as_of_round 时点重算 / 规则表降级语义 / ess.json 旁车 schema 与 decision_log
概要 / --ess 开关(零行为参数)。
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (  # noqa: E402
    ROOT as H_ROOT, add_evidence, install_simple, make_domain, make_ledger,
)
from sense2act.config import DomainConfig, InvestigationConfig  # noqa: E402
from sense2act.credibility import (  # noqa: E402
    EssRulesError, anchor_lookup, collect_factors, compute_all, load_rules,
    score_claim, tier_of,
)
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

RULES = load_rules(H_ROOT / "data" / "config" / "ess_rules.json")
AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def fac(**kw) -> dict:
    """因子快照工厂(collect_factors 输出形状的最小默认)。"""
    d = dict(claim_id="evt_t:C1", draft_id="C1", text="断言", category="事实",
             status="待证实", corpus_bound=False, unreachable=False,
             valid_window_days=None, support_roots=[], oppose_roots=[],
             tried=False, latest_support_fetched_at="")
    d.update(kw)
    return d


# ---------------------------------------------------------------- 锚点表


class TestAnchorTable(unittest.TestCase):
    """档位判定表驱动:8 锚点逐行 + 冲突 + 形态未覆盖(不装分)。"""

    def test_row85_双根权威(self):
        self.assertEqual(anchor_lookup("已证实", 2, True, False, False, RULES),
                         (85, "强(双根权威)"))

    def test_row75_权威单源(self):
        self.assertEqual(anchor_lookup("已证实", 1, True, False, False, RULES),
                         (75, "强(权威单源)"))

    def test_row70_双根普通(self):
        self.assertEqual(anchor_lookup("已证实", 2, False, False, False, RULES),
                         (70, "中(双根普通)"))

    def test_row60_自证(self):
        self.assertEqual(anchor_lookup("已证实", 1, False, True, False, RULES),
                         (60, "中(自证)"))

    def test_row50_候审_三形态(self):
        # s≥2 ∨ (s=1∧a):待证实却有判决梯门槛形态(卷宗截断/谓词门场景)
        self.assertEqual(anchor_lookup("待证实", 2, False, False, False, RULES),
                         (50, "候审"))
        self.assertEqual(anchor_lookup("待证实", 1, True, False, False, RULES),
                         (50, "候审"))
        self.assertEqual(anchor_lookup("待证实", 3, True, True, True, RULES),
                         (50, "候审"))

    def test_row30_孤证(self):
        self.assertEqual(anchor_lookup("待证实", 1, False, True, False, RULES),
                         (30, "弱(孤证)"))

    def test_row15_查过没有(self):
        self.assertEqual(anchor_lookup("待证实", 0, False, False, True, RULES),
                         (15, "弱(查过没有)"))

    def test_row5_未触及(self):
        self.assertEqual(anchor_lookup("待证实", 0, False, False, False, RULES),
                         (5, "未触及"))

    def test_冲突不评分(self):
        self.assertEqual(anchor_lookup("矛盾", 2, True, False, False, RULES),
                         (None, "冲突"))

    def test_已证实形态未覆盖_不装分(self):
        # 理论不可达(判决梯只出四种已证实路径);防手工改库等脏状态
        val, name = anchor_lookup("已证实", 1, False, False, False, RULES)
        self.assertIsNone(val)
        self.assertEqual(name, "已证实但形态未覆盖")

    def test_规则表锚点与代码表名同序(self):
        names = [a["name"] for a in RULES["anchors"]]
        values = [a["value"] for a in RULES["anchors"]]
        self.assertEqual(values, [85, 75, 70, 60, 50, 30, 15, 5])
        self.assertEqual(names[0], "强(双根权威)")
        self.assertEqual(names[-1], "未触及")

    def test_锚值从规则表消费_改表即生效(self):
        """回归测试(V3 敏感性抓到的 bug):条件在代码、数值在规则表——
        改 ess_rules.json 锚值必须改变分数,不许代码硬编码兜底。"""
        import copy
        rl = copy.deepcopy(RULES)
        for a in rl["anchors"]:
            if a["name"] == "中(自证)":
                a["value"] = 40                     # 60 → 40(跨中/弱边界 45)
        r = score_claim(fac(status="已证实", corpus_bound=True,
                            support_roots=["signal_layer"]),
                        rl, AS_OF, {"ccgp"})
        self.assertEqual(r["score"], 40)
        self.assertEqual(r["tier"], "弱")

    def test_档位边界与分数永不打架(self):
        self.assertEqual(tier_of(85, RULES), "强")
        self.assertEqual(tier_of(75, RULES), "强")
        self.assertEqual(tier_of(74, RULES), "中")
        self.assertEqual(tier_of(45, RULES), "中")
        self.assertEqual(tier_of(44, RULES), "弱")
        self.assertEqual(tier_of(8, RULES), "弱")
        self.assertEqual(tier_of(7, RULES), "未触及")
        self.assertEqual(tier_of(0, RULES), "未触及")
        self.assertIsNone(tier_of(None, RULES))


# ---------------------------------------------------------------- 修正与夹取


class TestCorrections(unittest.TestCase):
    """Decay/Oppose 修正项与 clamp(方案 §4.2/§4.7 走例逐条)。"""

    def test_走例1_信封自证单根(self):
        r = score_claim(fac(status="已证实", corpus_bound=True,
                            support_roots=["signal_layer"]),
                        RULES, AS_OF, {"ccgp"})
        self.assertEqual(r["score"], 60)
        self.assertEqual(r["anchor_name"], "中(自证)")
        self.assertEqual(r["tier"], "中")

    def test_走例2_双权威根加头条反方(self):
        r = score_claim(fac(status="已证实", support_roots=["ccgp", "ggzy"],
                            oppose_roots=["www.toutiao.com"]),
                        RULES, AS_OF, {"ccgp", "ggzy"})
        self.assertEqual(r["score"], 75)  # 85−0−10

    def test_走例3_双普通根时效衰减(self):
        fetched = (AS_OF - timedelta(days=25)).isoformat(timespec="seconds")
        r = score_claim(fac(status="已证实", support_roots=["x", "y"],
                            valid_window_days=30,
                            latest_support_fetched_at=fetched),
                        RULES, AS_OF, {"ccgp"})
        self.assertEqual(r["score"], 58)  # 70−⌊15×25/30⌋=70−12

    def test_衰减封顶_age超窗(self):
        fetched = (AS_OF - timedelta(days=40)).isoformat(timespec="seconds")
        r = score_claim(fac(status="已证实", support_roots=["x", "y"],
                            valid_window_days=7,
                            latest_support_fetched_at=fetched),
                        RULES, AS_OF, {"ccgp"})
        self.assertEqual(r["score"], 55)  # 70−15(封顶)

    def test_无窗与无支持证据不衰减(self):
        base = dict(status="已证实", support_roots=["ccgp", "ggzy"])
        self.assertEqual(score_claim(fac(**base), RULES, AS_OF,
                                     {"ccgp"})["score"], 85)
        self.assertEqual(score_claim(
            fac(status="已证实", support_roots=["ccgp", "ggzy"],
                valid_window_days=30), RULES, AS_OF, {"ccgp"})["score"], 85)

    def test_未来时间戳不负衰减(self):
        fetched = (AS_OF + timedelta(days=1)).isoformat(timespec="seconds")
        r = score_claim(fac(status="已证实", support_roots=["x", "y"],
                            valid_window_days=30,
                            latest_support_fetched_at=fetched),
                        RULES, AS_OF, {"ccgp"})
        self.assertEqual(r["score"], 70)

    def test_反方根封顶2(self):
        r = score_claim(fac(status="已证实", support_roots=["ccgp", "ggzy"],
                            oppose_roots=["a", "b", "c"]),
                        RULES, AS_OF, {"ccgp"})
        self.assertEqual(r["oppose"], 20)   # 10×min(3,2)
        self.assertEqual(r["score"], 65)    # 85−20

    def test_夹取下限0(self):
        r = score_claim(fac(status="待证实", support_roots=[],
                            oppose_roots=["a", "b"], tried=False),
                        RULES, AS_OF, {"ccgp"})
        # 待证实∧s=0 走 tried=False→5;oppose 对未触及锚同样压(公式统一)
        self.assertLessEqual(r["score"], 5)
        self.assertGreaterEqual(r["score"], 0)

    def test_冲突输出null分数(self):
        r = score_claim(fac(status="矛盾", support_roots=["x", "y"],
                            oppose_roots=["ccgp"]), RULES, AS_OF, {"ccgp"})
        self.assertIsNone(r["score"])
        self.assertIsNone(r["tier"])
        self.assertEqual(r["anchor_name"], "冲突")

    def test_steps结构化逐行(self):
        r = score_claim(fac(status="已证实", support_roots=["ccgp", "x"],
                            oppose_roots=["y"]), RULES, AS_OF, {"ccgp"})
        for step in r["steps"]:
            self.assertIn("factor", step)
            self.assertIn("value", step)
            self.assertIn("effect", step)
        self.assertIn("锚点", [s["factor"] for s in r["steps"]])
        self.assertIn("分数", [s["factor"] for s in r["steps"]])


# ---------------------------------------------------------------- 六公理


class TestAxioms(unittest.TestCase):
    """P1-P5 计分公理(property,固定因子轴扫描)+ P6 隔离负测试。"""

    def _score(self, **kw) -> int | None:
        return score_claim(fac(**kw), RULES, AS_OF, {"ccgp"})["score"]

    def test_p1_增独立支持根分不减(self):
        seq = [self._score(status="待证实", tried=True,
                           support_roots=list(rs))
               for rs in ([], ["x"], ["x", "y"], ["x", "y", "z"],
                          ["x", "ccgp"])]
        self.assertEqual(seq, sorted(seq))          # 15→30→50→50→50
        seq2 = [self._score(status="已证实", corpus_bound=True,
                            support_roots=list(rs))
                for rs in (["x"], ["x", "y"], ["x", "y", "z"], ["x", "ccgp"])]
        self.assertEqual(seq2, sorted(seq2))        # 60→70→70→85

    def test_p2_增独立反方根分不增(self):
        seq = [self._score(status="已证实", support_roots=["ccgp", "x"],
                           oppose_roots=[f"r{i}" for i in range(n)])
               for n in range(4)]
        self.assertEqual(seq, sorted(seq, reverse=True))    # 75→65→55→55

    def test_p3_老化分不增(self):
        seq = []
        for age in (0, 10, 25, 40):
            fetched = (AS_OF - timedelta(days=age)).isoformat(timespec="seconds")
            seq.append(self._score(status="已证实", support_roots=["x", "y"],
                                   valid_window_days=30,
                                   latest_support_fetched_at=fetched))
        self.assertEqual(seq, sorted(seq, reverse=True))    # 70→65→58→55

    def test_p4_权威替换普通分不减(self):
        # 同根数下,把普通根换成权威根(同向替换)分不减
        self.assertGreaterEqual(
            self._score(status="已证实", support_roots=["ccgp", "y"]),
            self._score(status="已证实", support_roots=["x", "y"]))
        self.assertGreaterEqual(
            self._score(status="已证实", corpus_bound=True,
                        support_roots=["ccgp"]),
            self._score(status="已证实", corpus_bound=True,
                        support_roots=["x"]))
        self.assertGreaterEqual(
            self._score(status="待证实", support_roots=["ccgp"]),
            self._score(status="待证实", support_roots=["x"]))

    def test_p5_同输入重算逐字节一致(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        add_evidence(led, "evt_t", "ccgp", "公告正文", "C1")
        add_evidence(led, "evt_t", "yifangbao", "转载正文", "C1")
        led.set_ruling("evt_t:C1", led.conn.execute(
            "SELECT evidence_id FROM evidence WHERE event_id='evt_t'"
            " ORDER BY evidence_id").fetchall()[0]["evidence_id"], "支持", 1)
        domain = make_domain()
        r1 = compute_all(led, "evt_t", RULES, AS_OF,
                         domain.authoritative_roots)
        r2 = compute_all(led, "evt_t", RULES, AS_OF,
                         domain.authoritative_roots)
        self.assertEqual(json.dumps(r1, sort_keys=True, ensure_ascii=False),
                         json.dumps(r2, sort_keys=True, ensure_ascii=False))

    def test_p6_s6不import_credibility(self):
        """隔离负测试:判决生产侧永不读分(物理隔离,P6)。"""
        src = (ROOT / "sense2act" / "stages" / "s6_verdict.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(
                        alias.name == "credibility"
                        or alias.name.endswith(".credibility"),
                        f"s6_verdict 禁止 import credibility: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                self.assertFalse(
                    mod == "credibility" or mod.endswith(".credibility"),
                    f"s6_verdict 禁止 from credibility import: {mod}")


# ---------------------------------------------------------------- 台账口径


class TestLedgerConsistency(unittest.TestCase):
    """s/o 与 LedgerRepository.independent_roots() 同口径(S6 判决原料)。"""

    def test_口径逐claim一致(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        e1 = add_evidence(led, "evt_t", "ccgp", "公告", "C1", round_no=1)
        e2 = add_evidence(led, "evt_t", "yifangbao", "转载", "C1", round_no=1)
        e3 = add_evidence(led, "evt_t", "xinhuanet.com", "反方", "C1", round_no=2)
        e4 = add_evidence(led, "evt_t", "noise", "无关项", "C2", round_no=2)
        led.set_ruling("evt_t:C1", e1, "支持", 1)
        led.set_ruling("evt_t:C1", e2, "支持", 2)
        led.set_ruling("evt_t:C1", e3, "矛盾", 2)
        led.set_ruling("evt_t:C2", e4, "无关", 2)
        factors = {f["claim_id"]: f
                   for f in collect_factors(led, "evt_t")}
        for cid in ("evt_t:C1", "evt_t:C2", "evt_t:C3"):
            ref = led.independent_roots(cid)
            f = factors[cid]
            self.assertEqual(f["support_roots"], ref["支持"], cid)
            self.assertEqual(f["oppose_roots"], ref["矛盾"], cid)
        self.assertTrue(factors["evt_t:C1"]["tried"] is False)
        self.assertTrue(factors["evt_t:C2"]["tried"] is True)   # 无关票
        self.assertEqual(factors["evt_t:C1"]["latest_support_fetched_at"] != "",
                         True)

    def test_全量汇总_schema(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        add_evidence(led, "evt_t", "ccgp", "公告", "C1")
        domain = make_domain()
        r = compute_all(led, "evt_t", RULES, AS_OF,
                        domain.authoritative_roots)
        for key in ("event_id", "rule_version", "as_of", "as_of_round",
                    "n_claims", "tier_counts", "claims", "replayable"):
            self.assertIn(key, r)
        self.assertEqual(r["rule_version"], "ess-0.2")
        self.assertEqual(r["n_claims"], 3)
        self.assertEqual(r["as_of_round"], None)
        # claims 按 claim_id 排序(确定性)
        ids = [c["claim_id"] for c in r["claims"]]
        self.assertEqual(ids, sorted(ids))


class TestAsOfRound(unittest.TestCase):
    """时点重算:as_of_round 按各表 round 列过滤(方案 §五)。"""

    def _seed(self) -> LedgerRepository:
        led = make_ledger()
        install_simple(led, "evt_t")
        e1 = add_evidence(led, "evt_t", "ccgp", "公告", "C1", round_no=1)
        led.set_ruling("evt_t:C1", e1, "支持", 1)
        e2 = add_evidence(led, "evt_t", "ggzy", "二根", "C1", round_no=2)
        led.set_ruling("evt_t:C1", e2, "支持", 2)
        led.update_claim_status("evt_t:C1", "已证实", None, 2)
        # 轮 2 才出生的 claim
        led.add_claim("evt_t", {
            "id": "C9", "text": "后来才登记", "category": "事实",
            "section_index": 1, "priority": 3, "priority_reason": "",
            "relation_type": None, "valid_window_days": None,
            "refresh_cycle_days": None, "parent": None,
            "entity_names": []}, 2)
        return led

    def test_回放到轮1(self):
        led = self._seed()
        domain = make_domain()
        r = compute_all(led, "evt_t", RULES, AS_OF,
                        domain.authoritative_roots, as_of_round=1)
        by_id = {c["claim_id"]: c for c in r["claims"]}
        self.assertNotIn("evt_t:C9", by_id)             # 轮 2 出生 → 不存在
        c1 = by_id["evt_t:C1"]
        self.assertEqual(c1["status"], "待证实")          # transitions 回放
        self.assertEqual(c1["s"], 1)                    # 轮 2 证据被滤掉
        self.assertEqual(c1["anchor_value"], 50)        # s=1∧权威根(ccgp)→候审

    def test_最新时点与不传等价(self):
        led = self._seed()
        domain = make_domain()
        r_none = compute_all(led, "evt_t", RULES, AS_OF,
                             domain.authoritative_roots)
        r_last = compute_all(led, "evt_t", RULES, AS_OF,
                             domain.authoritative_roots, as_of_round=2)
        self.assertEqual(
            json.dumps(r_none["claims"], sort_keys=True, ensure_ascii=False),
            json.dumps(r_last["claims"], sort_keys=True, ensure_ascii=False))
        c1 = {c["claim_id"]: c for c in r_none["claims"]}["evt_t:C1"]
        self.assertEqual(c1["status"], "已证实")
        self.assertEqual(c1["s"], 2)


# ---------------------------------------------------------------- 降级与挂载


class TestRulesLoading(unittest.TestCase):
    """规则表降级语义:缺失/损坏/schema 不合法 → EssRulesError,不装 0 分。"""

    def _write(self, tmp: str, obj) -> Path:
        p = Path(tmp) / "ess_rules.json"
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj),
                     encoding="utf-8")
        return p

    def test_仓库规则表可加载(self):
        rules = load_rules(H_ROOT / "data" / "config" / "ess_rules.json")
        self.assertEqual(rules["version"], "ess-0.2")
        self.assertEqual(rules["decay_per_window"], 15)
        self.assertEqual(rules["oppose_per_root"], 10)
        self.assertEqual(rules["oppose_cap_roots"], 2)

    def test_缺失(self):
        with self.assertRaises(EssRulesError):
            load_rules(Path(tempfile.mkdtemp()) / "nope.json")

    def test_损坏(self):
        p = self._write(tempfile.mkdtemp(), "{not json")
        with self.assertRaises(EssRulesError):
            load_rules(p)

    def test_schema_锚点数不对(self):
        p = self._write(tempfile.mkdtemp(), {
            "version": "x", "anchors": [{"value": 1, "name": "强(双根权威)"}],
            "tiers": {"强": 75, "中": 45, "弱": 8}})
        with self.assertRaises(EssRulesError):
            load_rules(p)

    def test_schema_锚值非降序(self):
        anchors = [{"value": v, "name": n} for v, n in
                   zip([75, 85, 70, 60, 50, 30, 15, 5],
                       ["强(权威单源)", "强(双根权威)", "中(双根普通)", "中(自证)",
                        "候审", "弱(孤证)", "弱(查过没有)", "未触及"])]
        p = self._write(tempfile.mkdtemp(), {
            "version": "x", "anchors": anchors,
            "tiers": {"强": 75, "中": 45, "弱": 8}})
        with self.assertRaises(EssRulesError):
            load_rules(p)

    def test_schema_缺桶边界(self):
        anchors = [{"value": v, "name": n} for v, n in
                   zip([85, 75, 70, 60, 50, 30, 15, 5],
                       ["强(双根权威)", "强(权威单源)", "中(双根普通)", "中(自证)",
                        "候审", "弱(孤证)", "弱(查过没有)", "未触及"])]
        p = self._write(tempfile.mkdtemp(), {
            "version": "x", "anchors": anchors, "tiers": {"强": 75}})
        with self.assertRaises(EssRulesError):
            load_rules(p)


class TestMount(unittest.TestCase):
    """finalize 末尾挂载:ess.json 旁车 + decision_log 概要 1 条 + --ess 开关。"""

    def _builder(self, led: LedgerRepository, tmp: str, ess=True,
                 config_dir=None) -> ReportBuilder:
        domain = (make_domain() if config_dir is None
                  else DomainConfig(config_dir=Path(config_dir)))
        config = InvestigationConfig(ess_enabled=ess)
        return ReportBuilder(led, domain, config, writer=None,
                             runs_dir=tmp)

    def test_旁车落盘与概要1条(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        add_evidence(led, "evt_t", "ccgp", "公告", "C1")
        tmp = tempfile.mkdtemp()
        out_dir = Path(tmp) / "evt_t"       # 生产挂载点=runs/<event_id>(finalize 已建)
        info = self._builder(led, tmp)._ess_finalize("evt_t", 3, out_dir)
        self.assertTrue(info["written"])
        self.assertEqual(info["rule_version"], "ess-0.2")
        ess = json.loads((out_dir / "ess.json").read_text(encoding="utf-8"))
        self.assertEqual(ess["event_id"], "evt_t")
        self.assertIn("tier_counts", ess)
        self.assertTrue(all("steps" in c for c in ess["claims"]))
        rows = led.conn.execute(
            "SELECT payload_json FROM decision_log"
            " WHERE event_id='evt_t' AND kind='其他'").fetchall()
        ess_rows = [r for r in rows if "ess" in json.loads(r["payload_json"])]
        self.assertEqual(len(ess_rows), 1)              # 概要恰 1 条
        payload = json.loads(ess_rows[0]["payload_json"])["ess"]
        self.assertEqual(payload["n_claims"], info["n_claims"])

    def test_开关关_零落盘零日志(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = tempfile.mkdtemp()
        info = self._builder(led, tmp, ess=False)._ess_finalize(
            "evt_t", 3, Path(tmp) / "evt_t")
        self.assertIsNone(info)
        self.assertFalse((Path(tmp) / "evt_t" / "ess.json").exists())
        n = led.conn.execute(
            "SELECT COUNT(*) AS c FROM decision_log"
            " WHERE event_id='evt_t' AND kind='其他'").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_降级_规则表缺失_注未评不装0分(self):
        led = make_ledger()
        install_simple(led, "evt_t")
        tmp = tempfile.mkdtemp()
        empty_cfg = tempfile.mkdtemp()
        info = self._builder(led, tmp, config_dir=empty_cfg)._ess_finalize(
            "evt_t", 3, Path(tmp) / "evt_t")
        self.assertFalse(info["written"])
        self.assertIn("reason", info)
        report = Path(tmp) / "evt_t" / "report.md"
        self.assertTrue(report.exists())
        self.assertIn("本报告未评", report.read_text(encoding="utf-8"))
        n = led.conn.execute(
            "SELECT COUNT(*) AS c FROM decision_log"
            " WHERE event_id='evt_t' AND kind='其他'").fetchone()["c"]
        self.assertEqual(n, 0)                          # 降级不写概要


if __name__ == "__main__":
    unittest.main()
