# -*- coding: utf-8 -*-
"""证据链卷面改造二阶段测试(2026-11-05 方案 §九-6)——v2 装配(论证区资格
排序/无边不画/种子挡除/事实组聚类/材料概括三档/状态史行)+ 渲染器(确定性/
注入防御/三线系统/缺口图矛盾行)+ 生成期三新 kind(ev_summary/grouping/
narrative;覆盖完整性校验/人工覆写/失败不伪装)。纪律继承一阶段:渲染期零
LLM/确定性逐字节一致/失败不伪装/不回流负测试延续。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

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


def _bp() -> AnalysisBlueprint:
    return AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="B公司中标XX项目", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"],
                       gist='B公司中标"XX"项目'),
            ClaimDraft(id="C2", text="B公司拖欠工资", category=ClaimCategory.FACT,
                       section_index=2, priority=5, entity_names=["B公司"]),
            ClaimDraft(id="C3", text="B公司涉及海外诉讼", category=ClaimCategory.FACT,
                       section_index=2, priority=4, entity_names=["B公司"]),
            ClaimDraft(id="C4", text="B公司已破产清算", category=ClaimCategory.FACT,
                       section_index=2, priority=3, entity_names=["B公司"]),
            ClaimDraft(id="C6", text="B公司注册资本实缴到位", category=ClaimCategory.FACT,
                       section_index=2, priority=3, entity_names=["B公司"]),
        ],
        hypotheses=[], gaps=[])


def _fixture() -> LedgerRepository:
    """v2 卷面 fixture(一阶段缺口 fixture 扩展):+种子挡除件(signal_layer
    支持裁定)+元陈述 C7(仅种子单根自证已证实)+反证 C9(矛盾=红线)。
    H0 暴雷={C1,C2}(挣到论证区:有已证实成员),H1 机会={C5},H2 反驳={C9}。"""
    led = make_ledger()
    led.register_event(rumor_envelope("evt_g"), "", "evt_g")
    led.install_blueprint("evt_g", _bp(), aliases=ALIASES)
    for hid, label, desc, cids in (
            ("H0", "暴雷", "资金链断裂", ("C1", "C2")),
            ("H1", "机会", "扩张信号", ("C5",)),
            ("H2", "反驳", "反向情形", ("C9",))):
        led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES(?,?,?,?,0,0)",
            (f"evt_g:{hid}", "evt_g", label, desc))
        for cid in cids:
            led.conn.execute(
                "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
                " VALUES(?,?,'AND')", (f"evt_g:{hid}", f"evt_g:{cid}"))
    # C1 已证实:rootA(普通)+ccgp(权威)=双根
    e1 = add_evidence(led, "evt_g", "rootA", "公告载明B公司中标", "C1",
                      title="中标公告")
    e2 = add_evidence(led, "evt_g", "ccgp", "政采公告:B公司中标", "C1",
                      platform="ccgp", title="政采公告")
    led.set_ruling("evt_g:C1", e1, "支持", 1)
    led.set_ruling("evt_g:C1", e2, "支持", 1)
    led.update_claim_status("evt_g:C1", "已证实", None, 1, e1)
    # C2 孤证:rootC 支持+1 无关票
    e3 = add_evidence(led, "evt_g", "rootC", "论坛帖称B公司拖欠工资", "C2",
                      title="论坛帖")
    led.set_ruling("evt_g:C2", e3, "支持", 1)
    e4 = add_evidence(led, "evt_g", "rootC", "与拖欠无关的财务通报", "C2")
    led.set_ruling("evt_g:C2", e4, "无关", 1)
    # C3 挂证未裁(无边待证实);C4 查证排除(unreachable)
    add_evidence(led, "evt_g", "rootD", "挂账未裁的报道内容", "C3")
    led.conn.execute(
        "UPDATE claims SET unreachable=1 WHERE claim_id='evt_g:C4'")
    # C6 谓词不足:ccgp 权威单根(候审)
    e5 = add_evidence(led, "evt_g", "ccgp", "登记公告载明B公司信息", "C6",
                      platform="ccgp", title="登记公告")
    led.set_ruling("evt_g:C6", e5, "支持", 1)
    # C5 派生自 C1(前提边;自身无直接证据→有边入图)
    led.add_claim("evt_g", {"id": "C5", "text": "B公司中标XX项目且金额放大",
                            "category": "事实", "section_index": 2,
                            "priority": 4, "parent": "C1"}, 2)
    # C7 元陈述(「信封未提供 X 字段」类):仅种子单根自证已证实
    led.add_claim("evt_g", {"id": "C7", "text": "信封未提供 relevance 字段",
                            "category": "事实", "section_index": 2,
                            "priority": 3}, 2)
    eseed = add_evidence(led, "evt_g", "signal_layer", "招标信号基础字段:项目编号 X1",
                         "C7", platform="signal_layer", title="信号层信封基础字段")
    led.set_ruling("evt_g:C7", eseed, "支持", 2)
    led.update_claim_status("evt_g:C7", "已证实", None, 2, eseed)
    # C9 矛盾:rootB 反证(红线;status=矛盾)
    led.add_claim("evt_g", {"id": "C9", "text": "B公司已被法院裁定破产",
                            "category": "矛盾", "section_index": 2,
                            "priority": 4, "entity_names": ["B公司"]}, 2)
    e6 = add_evidence(led, "evt_g", "rootB", "裁判文书网无破产裁定记录", "C9",
                      title="核查记录")
    led.set_ruling("evt_g:C9", e6, "矛盾", 2)
    led.update_claim_status("evt_g:C9", "矛盾", None, 2, e6)
    led.conn.commit()
    return led


def _build(led, kb=None, seeds_path=None):
    return cgr.build_chain_graph({"evt_g": led}, kb, "evt_g",
                                 auth_roots=_AUTH, seeds_path=seeds_path)


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


# ---------------------------------------------------------------- v2 装配

class ArgumentTests(unittest.TestCase):
    """论证区资格与排序(§4-1 机械判据;挣来的章:有已证实成员才立)。"""

    def test_qualified_only_with_verified(self):
        g = _build(_fixture())
        self.assertIsNotNone(g["argument"])
        self.assertEqual(g["argument"]["label"], "暴雷")
        self.assertEqual(g["argument"]["n_verified"], 1)
        self.assertEqual(g["argument"]["n_members"], 2)
        self.assertIn("1/2", g["argument"]["verdict_line"])   # 部分立住
        self.assertEqual(g["argument"]["grouping_source"], "fallback")

    def test_runner_up_and_untouched_in_overview(self):
        g = _build(_fixture())
        states = {h["label"]: h["state"] for h in g["overview"]["hypotheses"]}
        self.assertEqual(states["暴雷"], "chosen")
        self.assertEqual(states["机会"], "unearned")     # 成员在查无已证实
        self.assertEqual(states["反驳"], "unearned")

    def test_no_qualified_no_argument(self):
        led = _fixture()
        # 拆掉 C1 的已证实 → 全卷无已证实成员 → 无论证区,不冒充
        led.conn.execute(
            "UPDATE claims SET status='待证实' WHERE claim_id='evt_g:C1'")
        led.conn.commit()
        g = _build(led)
        self.assertIsNone(g["argument"])
        self.assertTrue(all(h["state"] != "chosen"
                            for h in g["overview"]["hypotheses"]))

    def test_ordering_verified_desc_then_score_then_id(self):
        from sense2act.chain_graph import pick_argument
        hyps = [{"hypothesis_id": "e:HA", "label": "A", "score": 0},
                {"hypothesis_id": "e:HB", "label": "B", "score": 5},
                {"hypothesis_id": "e:HC", "label": "C", "score": 9}]
        hc = {"e:HA": ["e:x1", "e:x2"], "e:HB": ["e:y1"],
              "e:HC": ["e:z1"]}
        st = {"e:x1": "已证实", "e:x2": "已证实", "e:y1": "已证实",
              "e:z1": "已证实"}
        top, runners = pick_argument(hyps, hc, st)
        self.assertEqual(top["hypothesis_id"], "e:HA")   # 成员数 2>1 优先于记分
        self.assertEqual([r["hypothesis_id"] for r in runners], ["e:HC", "e:HB"])
        # 同成员数→记分降序;同记分→id 升序
        st2 = {"e:x1": "已证实", "e:y1": "已证实", "e:z1": "已证实"}
        top2, _ = pick_argument(hyps, hc, st2)
        self.assertEqual(top2["hypothesis_id"], "e:HC")   # score 9 最高


class NoEdgeTests(unittest.TestCase):
    """无边不画(§4-2)+种子挡除(§4-4)+自证元陈述计数(诚实注记不冒充)。"""

    def test_unadjudicated_claim_not_in_main(self):
        g = _build(_fixture())
        c3 = _by_draft(g, "C3")
        self.assertFalse(c3["in_main"])           # 挂证未裁=无边
        self.assertEqual(c3["gap"]["type"], "未触及")
        in_fact_groups = {c for fg in g["fact_groups"]
                          for c in fg["claim_ids"]}
        self.assertNotIn(c3["claim_id"], in_fact_groups)

    def test_seed_evidence_blocked(self):
        g = _build(_fixture())
        self.assertEqual(g["meta"]["n_seed_hidden"], 1)
        roots = {e["root"] for e in g["nodes"]["evidence"]}
        self.assertNotIn("signal_layer", roots)   # 种子卡不进图
        self.assertNotIn("招标信号基础字段", json.dumps(
            [e["summary"] for e in g["nodes"]["evidence"]], ensure_ascii=False))
        c7 = _by_draft(g, "C7")
        self.assertFalse(c7["in_main"])           # 仅种子支持→挡后无边
        self.assertEqual(c7["status"], "已证实")
        self.assertEqual(g["overview"]["n_no_edge_verified"], 1)  # 总览注记
        self.assertEqual(g["overview"]["n_no_edge_pending"], 2)   # C3,C4

    def test_gap_claims_absent_from_main_svg(self):
        g = _build(_fixture())
        svg = csv_.render_main_svg(g)
        self.assertNotIn("海外诉讼", svg)          # C3(无边)不进主图
        self.assertNotIn("破产清算", svg)          # C4(无边)不进主图
        self.assertIn("中标", svg)                # C1 在场


class FactGroupTests(unittest.TestCase):
    """机械事实组框(§3.1:主语对象确定性聚类,零 LLM;论证区成员不重复入框)。"""

    def test_cluster_by_subject_entity(self):
        g = _build(_fixture())
        names = {fg["name"]: fg["claim_ids"] for fg in g["fact_groups"]}
        self.assertIn("关于B公司的事实", names)
        # C9(B公司)进事实组;论证区成员(C1,C2)不在事实组
        self.assertIn(_by_draft(g, "C9")["claim_id"], names["关于B公司的事实"])
        arg_ids = set(g["argument"]["member_ids"])
        for fg in g["fact_groups"]:
            self.assertFalse(arg_ids & set(fg["claim_ids"]))

    def test_frame_title_mechanical(self):
        g = _build(_fixture())
        for fg in g["fact_groups"]:
            self.assertTrue(fg["name"].startswith(("关于", "其他事实")))


class MaterialTests(unittest.TestCase):
    """材料卡(§4-4):概括三档(旁车指纹有效>词典短语兜底)+sensitive 不出境。"""

    def test_dict_phrase_fallback_nonempty(self):
        g = _build(_fixture())
        ev = {e["evidence_id"]: e for e in g["nodes"]["evidence"]}
        e1 = next(e for e in ev.values() if e["title"] == "中标公告")
        self.assertTrue(e1["summary"])            # 不留空
        self.assertNotEqual(e1["summary"], "中标公告")   # 清洗+出处,非标题直译
        self.assertIn("(", e1["summary"])         # 带出处

    def test_sensitive_summary_masked(self):
        led = _fixture()
        es = add_evidence(led, "evt_g", "rootS", "内部敏感材料内容XYZ", "C2",
                          title="内部件")
        led.set_ruling("evt_g:C2", es, "支持", 2)
        led.conn.execute(
            "UPDATE evidence SET sensitive=1 WHERE evidence_id=?", (es,))
        led.conn.commit()
        g = _build(led)
        e = next(e for e in g["nodes"]["evidence"] if e["evidence_id"] == es)
        self.assertTrue(e["sensitive"])
        self.assertNotIn("XYZ", e["summary"])     # 内容不出境(定位符)
        self.assertIn("internal", e["summary"])

    def test_summary_sidecar_consumed_with_drift(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_sidecar_rw(kbp)
            ev = next(e for e in cgr_build_evs(led) if e["title"] == "中标公告")
            good = cgr.ev_summary_fp(ev["title"], ev["content"])
            cg._put(conn, event_id="evt_g", kind="ev_summary",
                    claim_id=ev["evidence_id"], hypothesis_id="",
                    value="B公司中标XX项目(公告)", rationale="",
                    fingerprint=good, model="m")
            cg._put(conn, event_id="evt_g", kind="ev_summary",
                    claim_id="evt_g:E不存在的件", hypothesis_id="",
                    value="幻觉概括", rationale="", fingerprint="x", model="m")
            conn.close()
            kb = cgr.sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        ev = {e["evidence_id"]: e for e in g["nodes"]["evidence"]}
        e1 = next(e for e in ev.values() if e["title"] == "中标公告")
        self.assertEqual(e1["summary"], "B公司中标XX项目(公告)")
        self.assertEqual(g["meta"]["n_summary_drifted"], 1)   # 幻觉 id=漂移计数


def cgr_build_evs(led):
    """fixture 材料集(与 chain_gen 同判据:裁定在案∧非种子)。"""
    out = []
    for r in led.conn.execute(
            "SELECT DISTINCT e.evidence_id, e.title, e.content, e.platform"
            " FROM claim_evidence ce JOIN evidence e"
            " ON e.evidence_id=ce.evidence_id WHERE e.event_id='evt_g'"):
        if (r["platform"] or "") != "signal_layer":
            out.append({"evidence_id": r["evidence_id"], "title": r["title"],
                        "content": r["content"]})
    return sorted(out, key=lambda x: x["evidence_id"])


class HistoryTests(unittest.TestCase):
    """状态史行(命题卡三层之三;时序链精华吸收位=机械模板)。"""

    def test_verb_mapping(self):
        self.assertEqual(
            cgr.history_line("2026-10-30T09:00:00", [
                {"new_status": "已证实", "cause": None, "ts": "2026-11-08T10:00:00"},
                {"new_status": "待证实", "cause": "矛盾翻转",
                 "ts": "2026-11-12T10:00:00"}]),
            "立项 10-30 → 查实 11-08 → 翻转回落 11-12")

    def test_refresh_and_truncation_note(self):
        line = cgr.history_line("2026-10-30T09:00:00", [
            {"new_status": "待证实", "cause": "状态刷新",
             "ts": f"2026-11-{d:02d}T10:00:00"} for d in range(1, 7)])
        self.assertIn("复查刷新", line)
        self.assertIn("共 6 次状态变化", line)     # 截断注记(防线)
        self.assertEqual(line.count("→") + 1, cgr.MAX_HISTORY_NODES + 0)

    def test_claim_card_history_present(self):
        g = _build(_fixture())
        self.assertIn("立项", _by_draft(g, "C1")["history"])
        self.assertIn("查实", _by_draft(g, "C1")["history"])


class GroupingConsumeTests(unittest.TestCase):
    """分组三档消费:人工(seeds draft_id)>旁车(指纹)>机械平铺;
    覆盖完整性两侧同校验。"""

    def test_validate_grouping_exact_cover(self):
        ok = [{"name": "论点", "members": ["a", "b"]}]
        self.assertTrue(cgr.validate_grouping(ok, ["a", "b"]))
        self.assertFalse(cgr.validate_grouping([{"name": "论点",
                                                 "members": ["a"]}], ["a", "b"]))
        self.assertFalse(cgr.validate_grouping([{"name": "论点",
                                                 "members": ["a", "b", "c"]}],
                                               ["a", "b"]))
        self.assertFalse(cgr.validate_grouping([{"name": "论点",
                                                 "members": ["a", "a"]}],
                                               ["a", "b"]))
        self.assertFalse(cgr.validate_grouping([{"name": "", "members": ["a"]}],
                                               ["a"]))
        self.assertFalse(cgr.validate_grouping("x", ["a"]))

    def test_sidecar_grouping_with_drift(self):
        led = _fixture()
        members = ["evt_g:C1", "evt_g:C2"]
        groups = [{"name": "中标事实", "members": ["evt_g:C1"]},
                  {"name": "欠薪传闻", "members": ["evt_g:C2"]}]
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_sidecar_rw(kbp)
            cg._put(conn, event_id="evt_g", kind="grouping", claim_id="",
                    hypothesis_id="evt_g:H0", value=json.dumps(
                        groups, ensure_ascii=False),
                    rationale="",
                    fingerprint=cgr.grouping_fp("暴雷", members), model="m")
            conn.close()
            kb = cgr.sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        self.assertEqual(g["argument"]["grouping_source"], "sidecar")
        self.assertEqual([x["name"] for x in g["argument"]["groups"]],
                         ["中标事实", "欠薪传闻"])

    def test_manual_grouping_priority_and_draft_resolution(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "s.json"
            seeds.write_text(json.dumps(
                [{"label": "暴雷", "grouping": [
                    {"name": "人工论点", "members": ["C2"]},
                    {"name": "人工论点二", "members": ["C1"]}]}],
                ensure_ascii=False), encoding="utf-8")
            g = _build(led, seeds_path=seeds)
        self.assertEqual(g["argument"]["grouping_source"], "manual")
        self.assertEqual([x["name"] for x in g["argument"]["groups"]],
                         ["人工论点", "人工论点二"])
        self.assertEqual(g["argument"]["groups"][0]["members"], ["evt_g:C2"])

    def test_manual_grouping_bad_entry_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "s.json"
            seeds.write_text(json.dumps(
                [{"label": "暴雷", "grouping": [{"name": " ", "members": ["C1"]}]},
                 {"label": "暴雷", "grouping": "not-a-list"},
                 {"label": "另一假设", "grouping": [{"name": "好论点",
                                                     "members": ["x"]}]}],
                ensure_ascii=False), encoding="utf-8")
            out = cgr.load_manual_grouping(seeds)
        self.assertEqual(list(out), ["另一假设"])

    def test_narrative_consumed_only_with_valid_fingerprint(self):
        led = _fixture()
        members = ["evt_g:C1", "evt_g:C2"]
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_sidecar_rw(kbp)
            cg._put(conn, event_id="evt_g", kind="narrative", claim_id="",
                    hypothesis_id="evt_g:H0", value=json.dumps(
                        {"chapter": "欠薪已由公告坐实;传闻仍单一来源。",
                         "walkthrough": "先看中标事实,再看传闻。"},
                        ensure_ascii=False), rationale="",
                    fingerprint=cgr.narrative_fp("暴雷", members), model="m")
            cg._put(conn, event_id="evt_g", kind="narrative", claim_id="",
                    hypothesis_id="evt_g:H2", value=json.dumps(
                        {"chapter": "漂移件", "walkthrough": "x"},
                        ensure_ascii=False), rationale="",
                    fingerprint="bad", model="m")
            conn.close()
            kb = cgr.sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        self.assertEqual(g["argument"]["narrative"]["chapter"],
                         "欠薪已由公告坐实;传闻仍单一来源。")
        self.assertEqual(g["meta"]["n_gen_drifted"], 1)


# ---------------------------------------------------------------- 生成期三新 kind

class GenNewKindsTests(unittest.TestCase):
    """ev_summary/grouping/narrative 生成期:落库/stats/校验拒收/失败不伪装。"""

    def _run(self, replies, seeds_path=None):
        led = _fixture()
        kbp = Path(tempfile.mkdtemp()) / "kb.db"
        stats = cg.run_chain_gen({"evt_g": led}, kbp, "evt_g",
                                 provider=_FakeLLM(replies), auth_roots=_AUTH,
                                 config_dir=_CONFIG, seeds_path=seeds_path)
        return led, kbp, stats

    def test_three_kinds_written_and_consumed(self):
        replies = [
            {"items": []},   # polarity
            {"items": []},   # channel
            {"items": [{"evidence_id": "evt_g:E1", "summary": "公告载明中标"},
                       {"evidence_id": "evt_g:E幻觉", "summary": "幻觉件"}]},
            {"groups": [{"name": "中标事实", "members": ["evt_g:C1"]},
                        {"name": "欠薪传闻", "members": ["evt_g:C2"]}]},
            {"chapter": "欠薪已由公告坐实;传闻单一来源。",
             "walkthrough": "先中标事实后传闻。"},
        ]
        led, kbp, stats = self._run(replies)
        self.assertEqual(stats["ev_summary_total"], 5)   # 裁定在案非种子
        self.assertEqual(stats["ev_summary_written"], 1)
        self.assertEqual(stats["ev_summary_invalid"], 1)  # 幻觉 id
        self.assertEqual(stats["grouping_written"], 1)
        self.assertEqual(stats["narrative_written"], 1)
        kb = cgr.sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        g = _build(led, kb=kb)
        kinds = {r["kind"] for r in kb.execute("SELECT kind FROM chain_sidecar")}
        kb.close()
        kbp.unlink()
        # 三阶段(2026-11-06):chain-gen 首步存量迁移把 C7「信封未提供…」
        # 元陈述复制入 insights(source=legacy,detector_field)=新行为;五老
        # kind 断言随语义演进同步(照 R6 扇出断言同步先例)
        self.assertEqual(kinds, {"ev_summary", "grouping", "narrative",
                                 "detector_field"})
        self.assertEqual(g["argument"]["grouping_source"], "sidecar")
        self.assertTrue(g["argument"]["narrative"])
        e1 = next(e for e in g["nodes"]["evidence"]
                  if e["evidence_id"] == "evt_g:E1")
        self.assertEqual(e1["summary"], "公告载明中标")

    def test_ev_summary_excludes_seed_platform(self):
        # 种子件 id 不在材料集:LLM 硬喂种子 id=无效计数,零落库
        led0 = _fixture()
        seed_id = next(r[0] for r in led0.conn.execute(
            "SELECT evidence_id FROM evidence WHERE platform='signal_layer'"))
        replies = [{"items": []}, {"items": []},
                   {"items": [{"evidence_id": seed_id, "summary": "x"}]},
                   {"groups": []}, {"chapter": "", "walkthrough": ""}]
        led, kbp, stats = self._run(replies)
        kb = cgr.sqlite3.connect(kbp)
        n = kb.execute("SELECT COUNT(*) FROM chain_sidecar WHERE"
                       " kind='ev_summary'").fetchone()[0]
        kb.close()
        kbp.unlink()
        self.assertEqual(stats["ev_summary_total"], 5)   # 种子不在材料集
        self.assertEqual(stats["ev_summary_invalid"], 1)  # 硬喂种子 id=拒收
        self.assertEqual(n, 0)

    def test_grouping_inexact_cover_rejected(self):
        replies = [{"items": []}, {"items": []},
                   {"items": []},
                   {"groups": [{"name": "只盖一条", "members": ["evt_g:C1"]}]},
                   {"chapter": "c", "walkthrough": "w"}]
        led, kbp, stats = self._run(replies)
        kb = cgr.sqlite3.connect(kbp)
        n = kb.execute("SELECT COUNT(*) FROM chain_sidecar WHERE kind='grouping'"
                       ).fetchone()[0]
        kb.close()
        kbp.unlink()
        self.assertEqual(stats["grouping_invalid"], 1)   # 漏→拒收退机械平铺
        self.assertEqual(n, 0)
        self.assertEqual(stats["narrative_written"], 1)  # narrative 不受牵连

    def test_manual_grouping_skips_llm(self):
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "s.json"
            seeds.write_text(json.dumps(
                [{"label": "暴雷", "grouping": [
                    {"name": "人工论点", "members": ["C1", "C2"]}]}],
                ensure_ascii=False), encoding="utf-8")
            replies = [{"items": []}, {"items": []}, {"items": []},
                       {"groups": [{"name": "LLM 论点",
                                    "members": ["evt_g:C1", "evt_g:C2"]}]},
                       {"chapter": "c", "walkthrough": "w"}]
            led = _fixture()
            kbp = Path(td) / "kb.db"
            stats = cg.run_chain_gen({"evt_g": led}, kbp, "evt_g",
                                     provider=_FakeLLM(replies),
                                     auth_roots=_AUTH, config_dir=_CONFIG,
                                     seeds_path=seeds)
            self.assertEqual(stats["grouping_manual_skipped"], 1)
            kb = cgr.sqlite3.connect(kbp)
            n = kb.execute("SELECT COUNT(*) FROM chain_sidecar WHERE"
                           " kind='grouping'").fetchone()[0]
            kb.close()
        self.assertEqual(n, 0)   # 人工优先:LLM 分组不落库

    def test_llm_failure_new_kinds_no_writes(self):
        led = _fixture()
        kbp = Path(tempfile.mkdtemp()) / "kb.db"

        class _Boom:
            model_name = "boom"

            def generate_json(self, *a, **k):
                raise RuntimeError("api down")

        stats = cg.run_chain_gen({"evt_g": led}, kbp, "evt_g",
                                 provider=_Boom(), auth_roots=_AUTH,
                                 config_dir=_CONFIG)
        self.assertIn("api down", stats["ev_summary_error"])
        self.assertIn("api down", stats["grouping_error"])
        self.assertIn("api down", stats["narrative_error"])
        conn = sqlite3.connect(kbp)
        # 三阶段(2026-11-06):「LLM 失败零落库」只约束 LLM kind——存量迁移
        # 是机械步骤(零 LLM),provider 全炸时仍如实入 insights(语义演进同步)
        n = conn.execute(
            "SELECT COUNT(*) FROM chain_sidecar WHERE kind IN ('polarity',"
            "'channel','ev_summary','grouping','narrative','interpretation')"
        ).fetchone()[0]
        conn.close()
        kbp.unlink()
        self.assertEqual(n, 0)


# ---------------------------------------------------------------- 渲染器

class SvgRenderTests(unittest.TestCase):
    """确定性/注入防御/三线系统/论证区框/缺口图矛盾行/总览四态文案。"""

    def setUp(self):
        self.led = _fixture()
        self.g = _build(self.led)

    def test_deterministic_byte_identical(self):
        self.assertEqual(csv_.render_main_svg(self.g),
                         csv_.render_main_svg(_build(self.led)))
        self.assertEqual(csv_.render_gap_svg(self.g),
                         csv_.render_gap_svg(_build(self.led)))
        self.assertEqual(csv_.main_view_html(self.g),
                         csv_.main_view_html(_build(self.led)))

    def test_injection_defense(self):
        led = _fixture()
        led.conn.execute(
            "UPDATE claims SET gist='<script>alert(\"x\")</script>&若成立"
            "' WHERE claim_id='evt_g:C1'")
        led.conn.commit()
        g = _build(led)
        for artifact in (csv_.render_main_svg(g), csv_.render_gap_svg(g),
                         csv_.main_view_html(g)):
            self.assertNotIn("<script>", artifact)
            self.assertNotIn('"x"', artifact)

    def test_three_line_system(self):
        svg = csv_.render_main_svg(self.g)
        self.assertIn('stroke="#b0b0b0" stroke-width="1.5"', svg)   # 支撑灰实
        self.assertIn('stroke="#c9302c" stroke-width="2.5"', svg)   # 反证红
        self.assertIn('stroke-dasharray="5 3" marker-end', svg)     # 前提虚弧
        self.assertIn("前提</text>", svg)
        self.assertNotIn("共据", svg)                               # 共据不画线

    def test_argument_frame_and_narrative_block(self):
        g = self.g
        svg = csv_.render_main_svg(g)
        self.assertIn("论证区 ·「暴雷」·部分立住:1/2 条已证实", svg)  # 区名成色
        self.assertIn("成员命题", svg)                 # 机械平铺组名
        # 章首研判:无旁车=无该段(失败不伪装;块底色特征串不在场)
        self.assertNotIn("#eef5fb", svg)

    def test_narrative_block_rendered(self):
        led = _fixture()
        members = ["evt_g:C1", "evt_g:C2"]
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_sidecar_rw(kbp)
            cg._put(conn, event_id="evt_g", kind="narrative", claim_id="",
                    hypothesis_id="evt_g:H0", value=json.dumps(
                        {"chapter": "公告坐实中标;传闻单一来源。",
                         "walkthrough": "先事实后传闻。"},
                        ensure_ascii=False), rationale="",
                    fingerprint=cgr.narrative_fp("暴雷", members), model="m")
            conn.close()
            kb = cgr.sqlite3.connect(kbp)
            kb.row_factory = sqlite3.Row
            g = _build(led, kb=kb)
            kb.close()
        svg = csv_.render_main_svg(g)
        self.assertIn("公告坐实中标;传闻单一来源。", svg)

    def test_material_card_notes(self):
        svg = csv_.render_main_svg(self.g)
        self.assertIn("[E1]", svg)
        self.assertIn("出处", svg)                     # 出处行

    def test_same_root_note_when_multi(self):
        led = _fixture()
        e1b = add_evidence(led, "evt_g", "rootA", "公告转载:B公司中标", "C1",
                           title="转载件")
        led.set_ruling("evt_g:C1", e1b, "支持", 2)
        led.conn.commit()
        g = _build(led)
        svg = csv_.render_main_svg(g)
        self.assertIn("同根 2 件,互不为独立佐证", svg)

    def test_party_note(self):
        led = _fixture()
        ep = add_evidence(led, "evt_g", "company_web", "官网自述已复工", "C2",
                          platform="official_site", author_type="当事方",
                          title="官网公告")
        led.set_ruling("evt_g:C2", ep, "支持", 2)
        led.conn.commit()
        g = _build(led)
        self.assertIn("当事方自述,不单独作数", csv_.render_main_svg(g))

    def test_gap_svg_rows_and_return_condition(self):
        gap = csv_.render_gap_svg(self.g)
        self.assertIn("〔孤证〕", gap)                 # 四型徽章
        self.assertIn("〔查证排除〕", gap)
        self.assertIn("〔谓词不足〕", gap)
        self.assertIn("〔已反对〕", gap)               # 矛盾行(无四型徽章语义)
        self.assertIn("翻回条件:两独立正向方可翻回", gap)
        self.assertIn("B公司拖欠工资", gap)
        self.assertNotIn("已证实", gap.split("〔已反对〕")[0])  # 已证实无缺口

    def test_gap_svg_empty_when_no_gaps(self):
        g = {"nodes": {"claims": []}}
        self.assertEqual(csv_.render_gap_svg(g), "")

    def test_overview_lines_four_states(self):
        lines = csv_.overview_lines(self.g)
        text = "\n".join(lines)
        self.assertIn("「暴雷」挣到论证区(1/2 条已证实)", text)
        self.assertIn("无证明材料", text)
        self.assertIn("仅信封字段自证", text)          # 元陈述注记
        self.assertIn("已查无载明 1 次", text)          # 无关票注记
        self.assertIn("挂证 1 件未裁", text)

    def test_overview_runner_up_line(self):
        led = _fixture()
        # 让「机会」也挣到(C5 加权威根+已证实)→ 居次行
        e7 = add_evidence(led, "evt_g", "ccgp", "政采公告载明金额放大", "C5",
                          platform="ccgp", title="政采二")
        led.set_ruling("evt_g:C5", e7, "支持", 2)
        led.update_claim_status("evt_g:C5", "已证实", None, 2, e7)
        led.conn.commit()
        g = _build(led)
        text = "\n".join(csv_.overview_lines(g))
        self.assertIn("「机会」亦已立(1 条已证实),居次,不立区", text)

    def test_html_zero_external_dependency(self):
        html = csv_.main_view_html(self.g)
        self.assertNotIn("mermaid", html)
        self.assertNotIn("jsdelivr", html)             # CDN 退役
        self.assertNotIn("fonts.ready", html)
        self.assertIn("<svg", html)
        self.assertIn("证据附录", html)

    def test_dict_phrase_variants(self):
        self.assertEqual(cgr._dict_phrase("", "", ""), "材料")
        self.assertEqual(cgr._dict_phrase("", "", "微博"), "材料(微博)")
        self.assertIn("(ccgp)", cgr._dict_phrase("", "正文第一句。第二句", "ccgp"))
        # 2026-11-07 四阶段 §四 裁断退役:词典短语去 EV_PHRASE_CLIP 硬裁
        # (标题/首句本身短,「保险」变裁断事实)——旧断言 len<=30 随语义
        # 演进同步为全文保留+出处(照三阶段 kinds 集合断言同步先例)
        long_p = cgr._dict_phrase("很长的标题" * 20, "", "x")
        self.assertNotIn("…", long_p)             # 无省略号(不裁断)
        self.assertTrue(long_p.startswith("很长的标题"))   # 全文保留


class WrapHygieneTests(unittest.TestCase):
    """断行卫生(四阶段 §三 批1-A):不可断单元(编号/金额/时刻/域名)
    整词断只在词边界;单 token 超行宽=内部硬断不溢出;标点避头尾。"""

    def test_real_anchors_unbroken(self):
        text = ("项目编号1409992026AGK00184,金额999910.0,时刻09:30,"
                "域名ccgp.gov.cn,证据E12均已核实")
        lines = csv_._wrap(text, 160, 13)
        for tok in ("1409992026AGK00184", "999910.0", "09:30",
                    "ccgp.gov.cn", "E12"):
            self.assertTrue(any(tok in ln for ln in lines), f"{tok} 被断开")

    def test_overwide_token_inner_breaks(self):
        """token 超行宽(病态长串)=内部硬断,不溢出(失败不伪装排版版)。"""
        lines = csv_._wrap("编号1409992026AGK00184结尾", 100, 13)
        self.assertEqual("".join(lines), "编号1409992026AGK00184结尾")
        self.assertTrue(all(csv_._tw(ln, 13) <= 100 + 0.01 for ln in lines))

    def test_long_token_inner_break_no_overflow(self):
        lines = csv_._wrap("A" * 80, 100, 13)
        self.assertGreater(len(lines), 1)                    # 内部硬断发生
        self.assertTrue(all(csv_._tw(ln, 13) <= 100 + 0.01 for ln in lines))
        self.assertEqual("".join(lines), "A" * 80)           # 不丢字符

    def test_kinsoku(self):
        lines = csv_._wrap("中文断行测试:行首不得为句读闭合标点,行尾"
                           "不得为开放括号《书名号与「引号」均禁尾", 60, 13)
        self.assertGreater(len(lines), 2)
        for ln in lines:
            if ln:
                self.assertNotIn(ln[0], csv_._NO_HEAD, f"行首禁则: {ln!r}")
                self.assertNotIn(ln[-1], csv_._NO_TAIL, f"行尾禁则: {ln!r}")

    def test_deterministic(self):
        s = "编号X2026AGK00184与金额999910.0及「引号」文本,断行确定性"
        self.assertEqual(csv_._wrap(s, 90, 12), csv_._wrap(s, 90, 12))


class FlipLabelTests(unittest.TestCase):
    """批1.5 拍板③(2026-11-07 用户澄清):边标签=关系词;翻转条件文案
    (过程中间态机器表述)撤出图面,flip 字段 JSON 全量保留。"""

    def test_relation_only_no_flip_on_edges(self):
        g = _build(_fixture())   # C1 双根已证实:两条边均带 flip
        dot = csv_._dot_source(g)
        self.assertIn('label="直接证明"', dot)    # 关系词上图
        self.assertIn('label="印证"', dot)
        self.assertNotIn("此根失效→回待证实", dot)   # 过程性文案撤出图面
        self.assertNotIn("唯一根失效→回待证实", dot)
        # JSON 对账通道全量:flip 字段仍在
        flips = [e.get("flip") for e in g["edges"]["supports"] if e.get("flip")]
        self.assertTrue(any("此根失效→回待证实" in f for f in flips))
        self.assertIn('label="反证"', dot)        # 反证红边关系词保留


class CliV2SmokeTests(unittest.TestCase):
    """chain-graph --format svg|html 冒烟(真实 fixture 端到端)。"""

    def test_html_out(self):
        import subprocess
        led = _fixture()
        dbf = Path(led.conn.execute("PRAGMA database_list").fetchone()[2])
        led.conn.close()
        out = Path(tempfile.mkdtemp()) / "cv.html"
        env_db = str(dbf)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "sense2act", "chain-graph",
                 "--event", "evt_g", "--db", env_db, "--kb", "data/kb.db",
                 "--format", "html", "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8",
                cwd=str(ROOT), timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            html = out.read_text(encoding="utf-8")
            self.assertIn("主图", html)
            self.assertIn("<svg", html)
        finally:
            dbf.unlink(missing_ok=True)


class SvgTreeLayoutTests(unittest.TestCase):
    """推理树布局(2026-11-06 二改):材料贴身同列/走廊折线/前提右走廊。"""

    def setUp(self):
        self.led = _fixture()
        self.g = _build(self.led)

    def test_material_collocated_with_claim(self):
        """材料卡 x 与其主目标命题卡 x 同列(贴身咬合,不再底部平铺)。"""
        import re
        svg = csv_.render_main_svg(self.g)
        xs = re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="[\d.]+" height="[\d.]+"'
            r' rx="6" fill="(#\w+)"', svg)
        ev_cols = {x for x, y, f in xs if f == "#fcf8e3"}
        claim_cols = {x for x, y, f in xs
                      if f in ("#dff0d8", "#f2dede", "#f5f5f5")}
        self.assertTrue(ev_cols)                      # 材料在场
        self.assertTrue(ev_cols & claim_cols, "材料应与命题同列")

    def test_corridor_polyline_and_premise_lane(self):
        """支撑/前提线=三段走廊折线(非长斜线散指);前提=右走廊虚线。"""
        import re
        svg = csv_.render_main_svg(self.g)
        polys = re.findall(
            r'<path d="M[\d.,]+ L[\d.,]+ L[\d.,]+ L[\d.,]+" fill="none"'
            r' stroke="([^"]+)"', svg)
        self.assertIn("#999999", polys)               # 前提走廊线(C1→C5)
        self.assertIn("#b0b0b0", polys)               # 支撑走廊线
        # 「前提」标签在走廊旁(两字词例外纪律)
        self.assertIn("前提</text>", svg)

    def test_argument_node_and_funnel(self):
        """假设节点(顶部研判收束)+名条行+收束线在场。"""
        svg = csv_.render_main_svg(self.g)
        self.assertIn("论证区 ·「暴雷」·部分立住:1/2 条已证实", svg)
        self.assertIn("fill=\"#d9edf7\"", svg)        # 假设节点底色


# ------------------------------------------------ 呈现完整化 B 线(2026-11-09)

class PresentationCompleteTests(unittest.TestCase):
    """证据链呈现完整化(施工单 20261109):B-1 虚框卡注记三行+矛盾翻回行/
    B-2 待查证小区(无边命题上图不上线)/B-3 缺口图退役+总览注记改写/
    B-4 裁定逐条清单(件 5 引文落账)。纪律:JSON 字段只加不改/未裁三不/
    渲染只消费/确定性/台账零写入/注入防御。"""

    def setUp(self):
        self.led = _fixture()
        self.g = _build(self.led)
        self.by = {c["draft_id"]: c for c in self.g["nodes"]["claims"]}

    def _draft(self, cid):
        return next(c for c in self.g["nodes"]["claims"]
                    if c["draft_id"] == cid)

    # ---- B-1 虚框卡注记三行

    def test_b1_pending_card_note_rows(self):
        """虚框卡注记(忻州 c9 孤证形态):状态行回退〔待查证〕+缺口型短语
        (四型徽章与查证计数撤出卡面,行式链与缺口退场);证据形态行保留;
        gap 加法式键 next/next_channel 数据层保留。"""
        c2 = self.by["C2"]
        self.assertEqual(c2["gap"]["next"], "第二个独立来源相互印证")
        self.assertIn("next_channel", c2["gap"])
        self.assertEqual(c2["gap"]["next_channel"], c2["gap"]["channel"])
        line = csv_._status_line(c2)
        self.assertIn("〔待查证〕单一来源", line)      # 型徽章+计数撤
        self.assertNotIn("孤证", line)
        self.assertNotIn("已查", line)
        self.assertEqual(csv_._morph_line(c2.get("evidence_morph")),
                         "证据:1 独立来源(official_website)")
        c6 = self.by["C6"]
        self.assertIn("〔待查证〕", csv_._status_line(c6))
        self.assertIn("载明性核对未过", csv_._status_line(c6))  # 缺口型短语保留
        self.assertEqual(csv_._morph_line(c6.get("evidence_morph")),
                         "证据:1 独立来源(ccgp)")

    def test_b1_flipback_note_on_contradiction(self):
        """矛盾卡翻回条件注记行随缺口退场撤出主图(数据层 flipback_note
        保留;行式链与缺口退场拍板统一)。"""
        c9 = self.by["C9"]
        self.assertEqual(c9.get("flipback_note"), "翻回条件:两独立正向方可翻回")
        dot = csv_._dot_source(self.g)
        self.assertNotIn("翻回条件:两独立正向方可翻回", dot)
        self.assertNotIn("翻回条件:两独立正向方可翻回",
                         csv_.render_chain_svg(self.g, engine="fallback"))
        self.assertNotIn("(翻回条件:两独立正向方可翻回)",
                         csv_.render_md_chain(self.g))

    def test_b1_evidence_morph_pure_function(self):
        """evidence_morph 纯函数:独立根数+同源反证(矛盾根⊆支持根)判据;
        无支持无反证=None(零痕迹省行)。"""
        edges = [{"src": "E1", "ruling": "支持", "root": "r1"},
                 {"src": "E2", "ruling": "支持", "root": "r2"},
                 {"src": "E3", "ruling": "矛盾", "root": "r1"}]
        m = cgr.evidence_morph(edges, {})
        self.assertEqual(m["n_support_roots"], 2)
        self.assertTrue(m["same_source_contra"])      # 反证根 r1⊆支持根
        self.assertEqual(m["n_contra_items"], 1)
        m2 = cgr.evidence_morph([{"src": "E1", "ruling": "支持", "root": "r1"},
                                 {"src": "E2", "ruling": "矛盾", "root": "r9"}],
                                {})
        self.assertFalse(m2["same_source_contra"])    # 异源反证
        # drawn_ev 空=来源回落谱系根定位符(失败不伪装)
        self.assertEqual(
            csv_._morph_line(m2), "证据:1 独立来源(r1)+1 条相反记载")
        self.assertIsNone(cgr.evidence_morph([], {}))
        self.assertIsNone(cgr.evidence_morph(
            [{"src": "E1", "ruling": "无关", "root": "r1"}], {}))

    def test_b1_json_keys_additive(self):
        """JSON 第一公主:新键只加不改——既有 gap 键集/claims 键集保持;
        payload_to_json 序列化含新键。"""
        g0 = _build(_fixture())
        old_gap_keys = {"type", "label", "tries", "unadjudicated",
                        "channel", "channel_rationale"}
        for c in g0["nodes"]["claims"]:
            if c.get("gap"):
                self.assertTrue(old_gap_keys <= set(c["gap"]))
        js = json.loads(cgr.payload_to_json(self.g))
        self.assertIn("rulings", js)                  # v6 顶键(加法式)
        self.assertIn("in_zone", js["nodes"]["claims"][0])
        self.assertIn("n_unruled_evidence", js["overview"])

    def test_b1_note_rows_five_carriers_same_source(self):
        """证据形态行五载体同源(补证通道/翻回条件随缺口退场撤出):dot/
        fallback/md-chain 三载体同模板(同串在场);纵向主图同样生效。"""
        dot = csv_._dot_source(self.g)
        fb = csv_.render_chain_svg(self.g, engine="fallback")
        md = csv_.render_md_chain(self.g)
        for artifact in (dot, fb, md):
            self.assertIn("证据:1 独立来源(official_website)", artifact)
            self.assertNotIn("尚差:", artifact)
        # 纵向主图卡窄(176px),morph 行按断行卫生折行——断言取折行安全片段
        self.assertIn("证据:1 独立来源(", csv_.render_main_svg(self.g))

    # ---- B-2 待查证小区

    def test_b2_zone_membership_two_types(self):
        """小区资格=无边∧非解读∧非家族∧『查证排除/未触及』两型:收 C3
        (未触及)/C4(查证排除);不收有边虚框(C2/C6)/已证实无边(C7)
        /派生有边(C5)。"""
        self.assertTrue(self.by["C3"]["in_zone"])
        self.assertTrue(self.by["C4"]["in_zone"])
        for cid in ("C2", "C6", "C7", "C5"):
            self.assertFalse(self.by[cid]["in_zone"], cid)

    def test_b2_zone_zero_edges_in_dot(self):
        """小区零边纪律(数据层 in_zone 保留);主图 dot 源不再声明
        cluster_zone(待查证小区随缺口退场撤出主图)。"""
        dot = csv_._dot_source(self.g)
        zlines = [ln for ln in dot.splitlines()
                  if ln.strip().startswith("Z") and "->" in ln]
        self.assertEqual(zlines, [])
        self.assertNotIn("cluster_zone", dot)
        self.assertNotIn("待查证(未上图)", dot)

    def test_b2_zone_exclusions(self):
        """挡除类(信封未提供 X,模式表 meta_info 前缀)不入小区=继续全
        隐藏(11-08 会话边界);解读型分流不入小区。"""
        rules = cgr.load_present_rules(_CONFIG)
        led = _fixture()
        led.add_claim("evt_g", {"id": "C8", "text": "信封未提供 amount_history 字段",
                                "category": "事实", "section_index": 2,
                                "priority": 3}, 2)
        led.conn.commit()
        g = _build(led)
        c8 = next(c for c in g["nodes"]["claims"]
                  if c["draft_id"] == "C8")
        self.assertEqual(cgr.classify_claim(c8["text"], rules), "meta_info")
        self.assertFalse(c8["in_zone"])   # 挡除前缀=全隐藏(即便无边待证实)
        meta_zone = [c for c in g["nodes"]["claims"]
                     if c.get("in_zone") and c["interpretive"]]
        self.assertEqual(meta_zone, [])

    def test_b2_zone_fallback_main_graph_no_band_and_determinism(self):
        """fallback 主图无待查证小区带(随缺口退场撤出)+渲染确定性;
        md-chain 待查证清单仍承载(见 test_b3_md_chain_keeps_pending_list)。"""
        fb1 = csv_.render_chain_svg(self.g, engine="fallback")
        fb2 = csv_.render_chain_svg(self.g, engine="fallback")
        self.assertEqual(fb1, fb2)                    # 逐字节一致
        self.assertNotIn("待查证(未上图)", fb1)
        self.assertNotIn("〔查证排除〕查过未见载明", fb1)
        self.assertNotIn("〔未触及〕", fb1)

    # ---- B-3 缺口图退役

    def test_b3_gap_section_retired(self):
        """html 卷面「缺口图」section 退役;render_gap_svg 函数保留=可回溯
        (照 mermaid 退役先例)。"""
        html = csv_.main_view_html(self.g)
        self.assertNotIn("缺口图", html)
        self.assertIn("支撑明细", html)               # B-4 改名后 section 上位
        g0 = {"nodes": {"claims": []}}                # 函数本体仍可用
        self.assertEqual(csv_.render_gap_svg(g0), "")

    def test_b3_overview_notes_rewritten(self):
        """总览注记改写(无证明材料撤出小区→见待查证清单)+未裁计数行。"""
        lines = csv_.overview_lines(self.g)
        text = "\n".join(lines)
        self.assertIn("无证明材料(未上图,见待查证清单)", text)
        self.assertNotIn("在待查证小区", text)
        self.assertIn("另有 1 份在案材料未入裁定(见支撑明细)", text)

    def test_b3_md_chain_keeps_pending_list(self):
        """md-chain 保留待证实清单形态(文字版天然承载);真空链(无 claim
        无裁定无小区)仍降级空串=失败不伪装。"""
        md = csv_.render_md_chain(self.g)
        self.assertIn("#### 待查证(未上图)", md)
        self.assertIn("〔未触及〕B公司涉及海外诉讼——尚未查证·挂证 1 件未裁",
                      md)
        self.assertIn("〔查证排除〕B公司已破产清算", md)
        empty = {"nodes": {"claims": [], "hypotheses": [], "evidence": []},
                 "edges": {}, "rows": [], "clusters": [],
                 "meta": {"n_claims": 0}}
        self.assertEqual(csv_.render_md_chain(empty), "")

    # ---- B-4 裁定逐条清单

    def test_b4_checklist_structure(self):
        """清单全量挂账对按主图命题序:C1 双根/逐条含无关票/C3 未裁注记;
        n_items=全量挂账数(含未裁)。"""
        order = [r["claim_id"].rsplit(":", 1)[-1]
                 for r in self.g["rulings"]]
        self.assertEqual(order[:2], ["C1", "C2"])     # 主图命题序(论证区先)
        r1 = self.g["rulings"][0]
        self.assertEqual((r1["n_items"], r1["n_roots"]), (2, 2))
        r2 = self.g["rulings"][1]
        rulings2 = [it["ruling"] for it in r2["items"]]
        self.assertEqual(rulings2, ["支持", "无关"])  # 无关票如实列行
        r3 = next(r for r in self.g["rulings"]
                  if r["claim_id"].endswith(":C3"))
        self.assertEqual(r3["unruled_nums"], ["E5"])  # 未裁不冒充方向
        self.assertEqual(r3["n_items"], 1)            # 全量挂账数含未裁

    def test_b4_direction_marks_and_dup_root(self):
        """md-chain 证据行加方向标;同根第二件起标「同根折并,不另计数」
        (独立根计数不虚高)。"""
        led = _fixture()
        e9 = add_evidence(led, "evt_g", "rootA", "公告转载:B公司中标", "C1",
                          title="转载稿")
        led.set_ruling("evt_g:C1", e9, "支持", 1)
        led.conn.commit()
        g = _build(led)
        r1 = g["rulings"][0]
        dups = [it.get("dup_root") for it in r1["items"]]
        self.assertEqual(dups, [False, False, True])  # 同根第二件标注
        self.assertEqual(r1["n_roots"], 2)            # 独立根不虚高
        md = csv_.render_md_chain(g)
        self.assertIn("→ 支持", md)
        self.assertIn("(同根折并,不另计数)", md)

    def test_b4_quote_from_opinion_log_and_fallback(self):
        """件 5 引文:意见 payload 带 quote → 清单方向标后附引文(md/html
        同源);旧台账无 quote 键=自动回落只列方向(两态各自如实)。"""
        led = _fixture()
        led.log("evt_g", 1, "意见", {
            "claim_id": "evt_g:C1", "verdict": "已证实",
            "judgments": [{"evidence_id": "evt_g:E2", "direction": "支持",
                           "quote": "政采公告:B公司中标",
                           "reason": "法定公示原文"}]})
        led.conn.commit()
        g = _build(led)
        it = g["rulings"][0]["items"][1]
        self.assertEqual(it["quote"], "政采公告:B公司中标")
        rl = csv_.ruling_list_lines(g)
        self.assertIn(
            "  - [E2] 政采公告(ccgp) → 支持(「政采公告:B公司中标」)", rl)
        md = csv_.render_md_chain(g)
        self.assertIn("(「政采公告:B公司中标」)", md)
        html = csv_.main_view_html(g)
        self.assertIn("(「政采公告:B公司中标」)", html)
        # 旧台账形态(无意见 log):无 quote 键=回落只列方向
        g0 = self.g
        self.assertNotIn("quote", g0["rulings"][0]["items"][0])
        self.assertIn("  - [E2] 政采公告(ccgp) → 支持",
                      csv_.ruling_list_lines(g0))

    def test_b4_md_html_same_source(self):
        """md/html 同源逐字节:ruling_list_lines 产文本两处一致(html 只过
        转义,不另写模板)。"""
        rl = csv_.ruling_list_lines(self.g)
        self.assertTrue(rl)
        md = csv_.render_md_chain(self.g)
        for ln in rl:
            self.assertIn(ln, md)
        html = csv_.main_view_html(self.g)
        for ln in rl[:3]:
            self.assertIn(_xhtml_escape(ln), html)

    # ---- 横切

    def test_x_ledger_zero_write(self):
        """台账零写入:build+全载体渲染前后各表行数不变(装配/渲染纯读)。"""
        tables = ("claims", "claim_evidence", "decision_log",
                  "claim_transitions", "events", "evidence", "entities")
        led2 = _fixture()

        def _counts():
            return {t: led2.conn.execute(
                f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        before = _counts()
        g = _build(led2)
        csv_.render_chain_svg(g, engine="fallback")
        csv_.render_md_chain(g)
        csv_.main_view_html(g)
        csv_.ruling_list_lines(g)
        self.assertEqual(before, _counts())

    def test_x_injection_defense(self):
        """注入防御:清单文本/小区卡过转义(html _x/dot _h);md 为纯文本
        载体保留原文(数据保真,markdown 源码形态)。"""
        led = _fixture()
        led.add_claim("evt_g", {"id": "CX",
                                "text": "<script>alert(1)</script> 恶意命题",
                                "category": "事实", "section_index": 2,
                                "priority": 3}, 2)
        e = add_evidence(led, "evt_g", "rootZ", "正文<img src=x onerror=1>",
                         "CX", title="<b>恶意标题</b>")
        led.set_ruling("evt_g:CX", e, "支持", 2)
        led.conn.commit()
        g = _build(led)
        html = csv_.main_view_html(g)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>恶意标题</b>", html)
        self.assertIn("&lt;script&gt;", html)
        md = csv_.render_md_chain(g)
        self.assertIn("<script>alert(1)</script> 恶意命题", md)  # md 纯文本原样
        dot = csv_._dot_source(g)
        self.assertNotIn("<b>恶意标题</b>", dot)
        self.assertIn("&lt;b&gt;恶意标题&lt;/b&gt;", dot)

    def test_x_determinism_full_pipeline(self):
        """确定性:同输入双渲染逐字节一致(html 整页/md-chain/裁定清单)。"""
        h1 = csv_.main_view_html(self.g)
        h2 = csv_.main_view_html(self.g)
        self.assertEqual(h1, h2)
        m1 = csv_.render_md_chain(self.g)
        m2 = csv_.render_md_chain(self.g)
        self.assertEqual(m1, m2)
        g2 = _build(self.led)
        self.assertEqual(csv_.ruling_list_lines(self.g),
                         csv_.ruling_list_lines(g2))


def _xhtml_escape(s: str) -> str:
    """html 侧同款转义(测试内联版,与 chain_svg._x 一致)。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


if __name__ == "__main__":
    unittest.main()
