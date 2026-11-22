# -*- coding: utf-8 -*-
"""chain_web.v1 交互契约测试(2026-11-09;方案=《实施方案/证据链交互化与
专业化-设计方案-20261109.md》§四/§七,Phase 0)——适配器纯投影 + 校验器
+ CLI --format web。纪律照 PresentationCompleteTests 四件套先例:装配只
消费(graph 零 mutate)/确定性逐字节/台账零写入/注入防御;另锁敏感压净/
provenance 闭包/旁车不误标/折叠口径/降级不伪装。
"""
from __future__ import annotations

import copy
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

from helpers import ALIASES, ROOT, add_evidence, make_ledger, rumor_envelope  # noqa: E402
from sense2act import chain_gen as cg  # noqa: E402
from sense2act import chain_graph as cgr  # noqa: E402
from sense2act.chain_web_payload import (  # noqa: E402
    PROJECTION_VERSION, SCHEMA_VERSION, ChainWebError, build_chain_web, from_graph,
    validate_chain_web)
from sense2act.conclusion_present import build_conclusion_views  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402
from sense2act.store import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

_CONFIG = ROOT / "data" / "config"
_AUTH = {"ccgp"}
EV = "evt_w"
GENERATED_AT = "2026-11-11T00:00:00+00:00"


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
            # 注入防御样例:script 标签进台账,契约按数据保真透传(渲染层责任)
            ClaimDraft(id="C4", text="B公司已破产清算<script>alert(1)</script>",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3, entity_names=["B公司"]),
            ClaimDraft(id="C6", text="B公司注册资本实缴到位",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3, entity_names=["B公司"]),
            ClaimDraft(id="C10", text="B公司获得政府专项补贴",
                       category=ClaimCategory.FACT, section_index=2,
                       priority=3, entity_names=["B公司"]),
        ],
        hypotheses=[], gaps=[])


def _fixture(db_path: str | None = None) -> LedgerRepository:
    """契约 fixture(test_chain_svg v2 fixture 扩展):双根+权威/孤证/挂证
    未裁/查证排除(注入文本)/谓词不足/派生前提/种子元陈述/矛盾反证/敏感件
    /同 draft 撞号家族(C8,C8a 近文)/意见引文(s6 归档 quote/reason)。"""
    if db_path:
        led = LedgerRepository(connect(db_path), _CONFIG)
    else:
        led = make_ledger()
    led.register_event(rumor_envelope(EV), "", EV)
    led.install_blueprint(EV, _bp(), aliases=ALIASES)
    for hid, label, desc, cids in (
            ("H0", "暴雷", "资金链断裂", ("C1", "C2")),
            ("H1", "机会", "扩张信号", ("C5",)),
            ("H2", "反驳", "反向情形", ("C9",))):
        led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES(?,?,?,?,0,0)",
            (f"{EV}:{hid}", EV, label, desc))
        for cid in cids:
            led.conn.execute(
                "INSERT OR IGNORE INTO hypothesis_claims"
                "(hypothesis_id,claim_id,op) VALUES(?,?,'AND')",
                (f"{EV}:{hid}", f"{EV}:{cid}"))
    # C1 已证实:rootA(普通)+ccgp(权威)=双根;附意见引文(件 5)
    e1 = add_evidence(led, EV, "rootA", "公告载明B公司中标", "C1",
                      title="中标公告")
    e2 = add_evidence(led, EV, "ccgp", "政采公告:B公司中标", "C1",
                      platform="ccgp", title="政采公告")
    led.set_ruling(f"{EV}:C1", e1, "支持", 1)
    led.set_ruling(f"{EV}:C1", e2, "支持", 1)
    led.update_claim_status(f"{EV}:C1", "已证实", None, 1, e1)
    led.log(EV, 1, "意见", {"claim_id": f"{EV}:C1", "judgments": [
        {"evidence_id": e1, "quote": "公告载明B公司中标",
         "reason": "官方公告直载"}]})
    # C2 孤证:rootC 支持+1 无关票
    e3 = add_evidence(led, EV, "rootC", "论坛帖称B公司拖欠工资", "C2",
                      title="论坛帖")
    led.set_ruling(f"{EV}:C2", e3, "支持", 1)
    e4 = add_evidence(led, EV, "rootC", "与拖欠无关的财务通报", "C2")
    led.set_ruling(f"{EV}:C2", e4, "无关", 1)
    # C3 挂证未裁(无边待证实);C4 查证排除(unreachable;注入文本)
    add_evidence(led, EV, "rootD", "挂账未裁的报道内容", "C3")
    led.conn.execute(f"UPDATE claims SET unreachable=1 WHERE claim_id='{EV}:C4'")
    # C6 谓词不足:ccgp 权威单根(候审)
    e5 = add_evidence(led, EV, "ccgp", "登记公告载明B公司信息", "C6",
                      platform="ccgp", title="登记公告")
    led.set_ruling(f"{EV}:C6", e5, "支持", 1)
    # C5 派生自 C1(前提边;自身无直接证据→no_direct_evidence)
    led.add_claim(EV, {"id": "C5", "text": "B公司中标XX项目且金额放大",
                       "category": "事实", "section_index": 2,
                       "priority": 4, "parent": "C1"}, 2)
    # C7 元陈述:仅信封种子单根自证已证实
    led.add_claim(EV, {"id": "C7", "text": "信封未提供 relevance 字段",
                       "category": "事实", "section_index": 2, "priority": 3}, 2)
    eseed = add_evidence(led, EV, "signal_layer", "招标信号基础字段:项目编号 X1",
                         "C7", platform="signal_layer", title="信号层信封基础字段")
    led.set_ruling(f"{EV}:C7", eseed, "支持", 2)
    led.update_claim_status(f"{EV}:C7", "已证实", None, 2, eseed)
    # C9 矛盾:rootB 反证
    led.add_claim(EV, {"id": "C9", "text": "B公司已被法院裁定破产",
                       "category": "矛盾", "section_index": 2,
                       "priority": 4, "entity_names": ["B公司"]}, 2)
    e6 = add_evidence(led, EV, "rootB", "裁判文书网无破产裁定记录", "C9",
                      title="核查记录")
    led.set_ruling(f"{EV}:C9", e6, "矛盾", 2)
    led.update_claim_status(f"{EV}:C9", "矛盾", None, 2, e6)
    # C10 孤证且敏感(身份证触发源;仅标记不重写原文)
    e7 = add_evidence(led, EV, "rootS", "补贴公示:B公司获专项补贴", "C10",
                      title="补贴公示")
    led.set_ruling(f"{EV}:C10", e7, "支持", 2)
    led.conn.execute(f"UPDATE evidence SET sensitive=1 WHERE evidence_id='{e7}'")
    # C8 同文撞号家族(照 _merge_fixture 配方:add_claim 同 draft 撞号自动
    # 派生后缀 id+挂撞号父=闭包边;近文≥0.80 双条件成族;后代=最新代代表)
    led.add_claim(EV, {"id": "C8", "text": "B公司中标XX项目预算4800万",
                       "category": "事实", "section_index": 2,
                       "priority": 4, "entity_names": ["B公司"]}, 2)
    e8 = add_evidence(led, EV, "rootE", "预算公示:B公司中标XX项目 4800万",
                      "C8", title="预算公示")
    led.set_ruling(f"{EV}:C8", e8, "支持", 2)
    led.add_claim(EV, {"id": "C8", "text": "B公司中标XX项目预算4800万元",
                       "category": "事实", "section_index": 2,
                       "priority": 4, "entity_names": ["B公司"]}, 3,
                   aliases=ALIASES)
    led.conn.commit()
    return led


def _kb_with_fusion(kb_path: str) -> None:
    """临时 kb.db + C1 融合旁车(E1/E2 双根代表;指纹按 ev_summary_fp 现算)。"""
    conn = cg.open_insights_rw(kb_path)
    fps = {f"{EV}:E1": cgr.ev_summary_fp("中标公告", "公告载明B公司中标"),
           f"{EV}:E2": cgr.ev_summary_fp("政采公告", "政采公告:B公司中标")}
    members = [f"{EV}:E1", f"{EV}:E2"]
    value = json.dumps({"claim": f"{EV}:C1", "members": members,
                        "text": "双源共同载明B公司中标XX项目"},
                       ensure_ascii=False)
    conn.execute(
        "INSERT OR REPLACE INTO insights(insight_id,event_id,kind,claim_id,"
        "value,fingerprint) VALUES(?,?,?,?,?,?)",
        (f"fusion:{EV}:C1", EV, "fusion", f"{EV}:C1", value,
         cgr.fusion_fp(members, fps)))
    conn.commit()
    conn.close()


def _graph(led, kb=None, **kw):
    return cgr.build_chain_graph({EV: led}, kb, EV, auth_roots=_AUTH, **kw)


def _web(led, kb=None, as_of_round=None):
    graph = _graph(led, kb, as_of_round=as_of_round)
    return from_graph(graph, {EV: led}, kb, EV, as_of_round=as_of_round,
                      config_dir=_CONFIG, generated_at=GENERATED_AT)


def _node(payload, nid):
    return next(n for n in payload["nodes"] if n["id"] == nid)


def _claim(payload, draft):
    return next(c for c in payload["claims"] if c["draft_id"] == draft)


class SchemaTests(unittest.TestCase):
    """①契约顶层:键集/schema_version/五泳道/kind 元数据。"""

    def test_top_keys_and_layers(self):
        led = _fixture()
        p = _web(led)
        for k in ("schema_version", "event", "meta", "layers", "nodes",
                  "edges", "raw_edges", "edge_groups", "evidence", "claims", "verification",
                  "conclusions", "timeline", "stats", "omissions", "details"):
            self.assertIn(k, p)
        self.assertEqual(p["schema_version"], SCHEMA_VERSION)
        self.assertEqual([l["id"] for l in p["layers"]],
                         ["source", "fact", "verification", "reasoning",
                          "conclusion"])
        self.assertEqual(p["event"]["kind"], "rumor")   # 信封 kind 读回
        self.assertEqual(p["event"]["event_id"], EV)
        self.assertEqual(p["meta"]["generated_at"], GENERATED_AT)
        self.assertEqual(p["meta"]["projection_version"], PROJECTION_VERSION)
        self.assertRegex(p["meta"]["generator_revision"], r"^[0-9a-f]{16}$")
        self.assertEqual(validate_chain_web(p, strict=False), [])

    def test_meta_web_counters(self):
        led = _fixture()
        p = _web(led)
        web = p["meta"]["web"]
        self.assertEqual(web["n_nodes"], len(p["nodes"]))
        self.assertEqual(web["n_edges"], len(p["edges"]))
        self.assertEqual(web["n_evidence"], len(p["evidence"]))
        self.assertEqual(web["n_claims"], len(p["claims"]))
        self.assertEqual(web["n_collapsed"],
                         sum(1 for n in p["nodes"] if n["collapsed"]))


class NodeMappingTests(unittest.TestCase):
    """②五泳道派生:行序=graph.rows;折叠三口径(family/小区/融合证据)。"""

    def test_all_lanes_present_and_row_order(self):
        led = _fixture()
        kbp = os.path.join(tempfile.mkdtemp(), "kb.db")
        _kb_with_fusion(kbp)
        kb = sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        p = _web(led, kb)
        lanes = p["stats"]["lanes"]
        for lane in ("source", "fact", "verification", "reasoning",
                     "conclusion"):
            self.assertGreater(lanes[lane], 0, lane)
        # fact 行序 = graph.rows 行序(第八波行式贴身链语义)
        led_graph = _graph(led, kb)
        rows = [r["claim_id"] for r in led_graph["rows"]]
        facts = [n["id"].split(":", 1)[-1] for n in p["nodes"]
                 if n["type"] == "fact"
                 and n["position_hint"] < "fact:900"]
        heads = [cid for cid in facts if cid in set(rows)]
        self.assertEqual(heads, [c for c in rows
                                 if c in set(heads)])
        kb.close()

    def test_collapsed_family_zone_fusion(self):
        led = _fixture()
        p = _web(led)
        c8 = _node(p, f"cl:{EV}:C8")
        self.assertTrue(c8["collapsed"])          # 家族非代表(撞号原条)
        self.assertEqual(_claim(p, "C8")["family_rep"], f"{EV}:C8d")
        for draft in ("C3", "C4"):                # 待补证命题改为显式上图
            cid = f"{EV}:{draft}"
            self.assertFalse(_node(p, f"cl:{cid}")["collapsed"], draft)
            self.assertIn(_node(p, f"cl:{cid}")["evidence_state"],
                          ("unadjudicated", "no_direct"))
        # C8d(撞号后代=代表)经撞号前提边在主图,不属小区,不折叠
        self.assertFalse(_node(p, f"cl:{EV}:C8d")["collapsed"])
        self.assertIn(f"der:{EV}:C8|{EV}:C8d",
                      {e["id"] for e in p["edges"]})
        self.assertFalse(_node(p, f"cl:{EV}:C1")["collapsed"])
        # 融合证据折叠:kb 融合卡下 E1/E2 为成员→source 折叠
        kbp = os.path.join(tempfile.mkdtemp(), "kb.db")
        _kb_with_fusion(kbp)
        kb = sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        p2 = _web(led, kb)
        self.assertTrue(_node(p2, f"ev:{EV}:E1")["collapsed"])
        self.assertTrue(_node(p2, f"ev:{EV}:E2")["collapsed"])
        self.assertFalse(_node(p2, f"cl:{EV}:C1")["collapsed"])  # 目标命题不折
        kb.close()


class EdgeMappingTests(unittest.TestCase):
    """③四类边:关系词/极性/ruling 反查/种子不画边/融合聚合边。"""

    def test_support_relations_and_ruling_backref(self):
        led = _fixture()
        p = _web(led)
        by_id = {e["id"]: e for e in p["edges"]}
        e_c1_a = by_id[f"sup:{EV}:E1|{EV}:C1"]
        self.assertEqual(e_c1_a["relation_type"], "印证")     # 双根支持
        self.assertEqual(e_c1_a["polarity"], "正向")
        e_c1_b = by_id[f"sup:{EV}:E2|{EV}:C1"]
        self.assertEqual(e_c1_b["relation_type"], "直接证明")  # 权威根
        self.assertEqual(by_id[f"sup:{EV}:E3|{EV}:C2"]["relation_type"],
                         "佐证")                              # 单根
        e_c9 = by_id[f"sup:{EV}:E8|{EV}:C9"]
        self.assertEqual(e_c9["relation_type"], "反证")
        self.assertEqual(e_c9["polarity"], "反向")
        for e in p["edges"]:
            if e["relation_type"] in ("直接证明", "印证", "佐证", "反证",
                                      "时效更新"):
                self.assertTrue(e["ruling_ids"], e["id"])
                for rid in e["ruling_ids"]:
                    self.assertIn(rid, p["details"])

    def test_affiliation_edges_lanes_not_orphan(self):
        """泳道从属边(2026-11-10 交互反馈批):核验/入组/依据——
        verification/reasoning 此前 0 边悬空;从属字段线化,不创造新判定。"""
        led = _fixture()
        p = _web(led)
        by_id = {e["id"]: e for e in p["edges"]}
        node_types = {n["id"]: n["type"] for n in p["nodes"]}
        # 核验:每个 verification 节点恰一条 cl→vf,端点泳道正确
        vf_nodes = [nid for nid, t in node_types.items() if t == "verification"]
        self.assertTrue(vf_nodes)
        for nid in vf_nodes:
            node = _node(p, nid)
            if node.get("sub_type") in ("gap_hint", "meta_info"):
                continue
            cid = nid.split(":", 1)[1]
            e = by_id[f"vfc:{cid}"]
            self.assertEqual((e["source"], e["target"], e["relation_type"]),
                             (f"cl:{cid}", f"vf:{cid}", "核验"))
        # 每条 verification 节点都有入边(不再悬空)
        for e in p["edges"]:
            if e["relation_type"] in ("核验", "入组", "依据"):
                self.assertEqual(e["ruling_ids"], [])
                self.assertIn(e["claim_ids"][0],
                              {c["claim_id"] for c in p["claims"]})
        touched = {e["source"] for e in p["edges"]} | {e["target"] for e in p["edges"]}
        for nid, t in node_types.items():
            if t == "verification":
                if _node(p, nid).get("intentional_orphan"):
                    continue
                self.assertIn(nid, touched, f"{nid} verification 节点仍悬空")
        # 入组:论点组节点若在,每个成员命题有 cl→rg 边
        for n in p["nodes"]:
            if n.get("sub_type") == "论点组":
                gid = n["id"]
                members = p["details"][n["detail_ref"]]["members"]
                for cid in members:
                    self.assertIn(f"rgb:{gid.split(':', 1)[1]}|{cid}", by_id)
        # 依据:有解读桥时,basis 成员命题有 cl→br 边(fixture 无桥则此段空跑)
        br_edges = [e for e in p["edges"] if e["relation_type"] == "依据"]
        for e in br_edges:
            self.assertTrue(e["source"].startswith("cl:"))
            self.assertTrue(e["target"].startswith("br:"))

    def test_seed_hidden_and_derives_and_fusion_edge(self):
        led = _fixture()
        kbp = os.path.join(tempfile.mkdtemp(), "kb.db")
        _kb_with_fusion(kbp)
        kb = sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        p = _web(led, kb)
        by_id = {e["id"]: e for e in p["edges"]}
        self.assertNotIn(f"sup:{EV}:E7|{EV}:C7", by_id)   # 种子边不画(§4-4)
        # C7 语义归位为 meta_info，不再生成伪事实卡；但正本/
        # provenance 仍经 claims+verification 目录可反查。
        self.assertNotIn(f"cl:{EV}:C7", {n["id"] for n in p["nodes"]})
        self.assertEqual(_claim(p, "C7")["interpretive"], "meta_info")
        self.assertIn(f"{EV}:E7",
                      next(v for v in p["verification"]
                           if v["claim_id"] == f"{EV}:C7")["rep_evidence"])
        self.assertTrue(any(n.get("sub_type") == "meta_info"
                            and f"{EV}:C7" in n.get("member_claim_ids", [])
                            for n in p["nodes"]))
        der = by_id[f"der:{EV}:C1|{EV}:C5"]
        self.assertEqual(der["relation_type"], "前提")
        self.assertEqual(der["claim_ids"], [f"{EV}:C1", f"{EV}:C5"])
        fue = by_id[f"fue:{EV}:C1"]
        self.assertEqual((fue["source"], fue["target"], fue["relation_type"]),
                         (f"fu:{EV}:C1", f"cl:{EV}:C1", "印证"))
        self.assertTrue(fue["sidecar"]["kind"] == "llm_sidecar")
        con = by_id[f"con:{EV}:C1|{EV}:H0"]
        self.assertEqual(con["relation_type"], "传导")
        self.assertIn("polarity", con)
        kb.close()


class ProvenanceClosureTests(unittest.TestCase):
    """④溯源闭环:detail_ref 全命中;no_direct_evidence 显式标注。"""

    def test_closure_and_no_direct_evidence(self):
        led = _fixture()
        p = _web(led)
        self.assertEqual(validate_chain_web(p, strict=False), [])
        self.assertIn(f"{EV}:E1",
                      _node(p, f"cl:{EV}:C1")["provenance_ids"])
        c5 = _node(p, f"cl:{EV}:C5")
        self.assertTrue(c5.get("no_direct_evidence"))   # 派生命题无直接证据
        hy0 = _node(p, f"hy:{EV}:H0")
        self.assertIn(f"{EV}:E1", hy0["provenance_ids"])  # 结论←成员闭包
        for n in p["nodes"]:
            if n["detail_ref"]:
                self.assertIn(n["detail_ref"], p["details"], n["id"])


class SensitiveTests(unittest.TestCase):
    """⑤敏感压净:internal 只出定位符;details 不落内容键;融合不含敏感。"""

    def test_internal_suppressed_but_locatable(self):
        led = _fixture()
        p = _web(led)
        e7 = next(e for e in p["evidence"]
                  if e["evidence_id"] == f"{EV}:E9")   # E9=C10 敏感件(E7=种子)
        self.assertEqual(e7["sensitivity"], "internal")
        self.assertEqual(e7["title"], "〔internal〕材料")
        for k in ("excerpt", "source_ref", "source_url", "platform",
                  "published_at", "collected_at", "attachment_ref"):
            self.assertIsNone(e7[k], k)
        self.assertNotIn(f"evidence:{EV}:E9", p["details"])
        self.assertNotIn(f"assets:{EV}:E9", p["details"])
        # 存在性反查保留(定位符);节点 detail_ref 空=无内容可看
        self.assertIn(f"{EV}:E9",
                      _node(p, f"cl:{EV}:C10")["provenance_ids"])
        self.assertEqual(_node(p, f"ev:{EV}:E9")["detail_ref"], "")
        rd = p["details"][f"ruling:{EV}:C10|{EV}:E9"]
        self.assertTrue(rd["masked"])
        self.assertIsNone(rd["quote"])
        self.assertEqual(validate_chain_web(p, strict=False), [])


class DeterminismTests(unittest.TestCase):
    """⑥确定性:同输入两次装配逐字节一致(from_graph 与端到端两口径)。"""

    def test_from_graph_bytes_identical(self):
        led = _fixture()
        kbp = os.path.join(tempfile.mkdtemp(), "kb.db")
        _kb_with_fusion(kbp)
        kb = sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        graph = _graph(led, kb)
        b1 = cgr.payload_to_json(from_graph(
            graph, {EV: led}, kb, EV, generated_at=GENERATED_AT))
        b2 = cgr.payload_to_json(from_graph(
            graph, {EV: led}, kb, EV, generated_at=GENERATED_AT))
        self.assertEqual(b1, b2)
        kb.close()

    def test_end_to_end_bytes_identical(self):
        led = _fixture()
        j1 = cgr.payload_to_json(build_chain_web({EV: led}, None, EV,
                                                 auth_roots=_AUTH,
                                                 generated_at=GENERATED_AT))
        j2 = cgr.payload_to_json(build_chain_web({EV: led}, None, EV,
                                                 auth_roots=_AUTH,
                                                 generated_at=GENERATED_AT))
        self.assertEqual(j1, j2)


class NoMutationTests(unittest.TestCase):
    """⑦输入 graph 零 mutate(旧 graph JSON 不变负测试,§7)。"""

    def test_graph_untouched(self):
        led = _fixture()
        graph = _graph(led)
        snapshot = copy.deepcopy(graph)
        from_graph(graph, {EV: led}, None, EV)
        self.assertEqual(graph, snapshot)
        again = _graph(led)
        self.assertEqual(graph, again)


class LedgerZeroWriteTests(unittest.TestCase):
    """⑧台账零写入:七表 COUNT 装配前后相等。"""

    TABLES = ("claims", "claim_evidence", "decision_log", "claim_transitions",
              "events", "evidence", "entities")

    def test_counts_unchanged(self):
        led = _fixture()

        def counts():
            return {t: led.conn.execute(
                f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in self.TABLES}

        before = counts()
        _web(led)
        self.assertEqual(counts(), before)


class ValidationTests(unittest.TestCase):
    """⑨校验器:合法过;三类破坏各自抛/列问题。"""

    def _payload(self):
        led = _fixture()
        return _web(led)

    def test_valid_passes(self):
        self.assertEqual(validate_chain_web(self._payload(), strict=False), [])

    def test_missing_evidence_raises(self):
        p = self._payload()
        p["evidence"] = p["evidence"][1:]
        with self.assertRaises(ChainWebError):
            validate_chain_web(p, strict=True)

    def test_bad_schema_version_raises(self):
        p = self._payload()
        p["schema_version"] = "chain_web.v0"
        with self.assertRaises(ChainWebError):
            validate_chain_web(p, strict=True)

    def test_internal_leak_raises(self):
        p = self._payload()
        for e in p["evidence"]:
            if e["sensitivity"] == "internal":
                e["excerpt"] = "绝密内容"
        with self.assertRaises(ChainWebError):
            validate_chain_web(p, strict=True)
        issues = validate_chain_web(p, strict=False)
        self.assertTrue(any("压净" in x for x in issues))

    def test_gap_object_string_null_and_invalid_types(self):
        base = self._payload()
        target_id = base["claims"][0]["claim_id"]
        for gap in (None, "旧件文本缺口", {
                "type": "查证排除", "label": "待补证", "next": "换通道",
                "channel": "web", "next_channel": "registry", "tries": 2,
                "unadjudicated": 1, "channel_rationale": "优先查权威源"}):
            p = copy.deepcopy(base)
            p["claims"][0]["gap"] = gap
            self.assertEqual(validate_chain_web(p, strict=False), [], gap)
        for gap, needle in (([], "gap 必须"),
                            ({"tries": "2"}, "gap.tries"),
                            ({"label": 7}, "gap.label")):
            p = copy.deepcopy(base)
            p["claims"][0]["gap"] = gap
            issues = validate_chain_web(p, strict=False)
            self.assertTrue(any(needle in x and target_id in x
                                for x in issues), issues)
            with self.assertRaises(ChainWebError):
                validate_chain_web(p, strict=True)

    def test_conclusions_have_descriptions_and_current_state_projection(self):
        p = self._payload()
        status = {c["claim_id"]: c["status"] for c in p["claims"]}
        self.assertTrue(p["conclusions"])
        for h in p["conclusions"]:
            self.assertTrue(h["description"])
            self.assertTrue(h["summary"])
            self.assertTrue(h["source_ref"].endswith(":S3"))
            self.assertTrue(h["source_snapshot_version"])
            self.assertTrue(all(status[cid] == "已证实"
                                for cid in h["supporting_claim_ids"]))
            self.assertTrue(all(status[cid] == "待证实"
                                for cid in h["pending_claim_ids"]))

    def test_tender_summary_never_promotes_pending_category_match(self):
        claims = [
            {"claim_id": "evt:c2", "status": "已证实", "text": "截止时间已公告", "gap": None},
            {"claim_id": "evt:c18", "status": "待证实", "text": "采购品类与经营范围匹配", "gap": {"next": "补查"}},
            {"claim_id": "evt:c19", "status": "待证实", "text": "投标资质与交付资源已具备", "gap": {"next": "补查"}},
        ]
        graph = {"inferences": [{
            "fingerprint_verified": True,
            "target_hypothesis_ids": ["evt:h3"],
            "basis_claim_ids": ["evt:c2", "evt:c18", "evt:c19"],
            "inference_text": "项目与截止时间已有公开材料支撑；业务匹配、投标资格与交付资源仍待补证，因此目前只能得出条件性的潜在机会。",
            "condition_or_gap": "品类、资质与资源同时成立时才闭合。",
        }]}
        views = build_conclusion_views(
            graph, claims, [{"hypothesis_id": "evt:h3", "label": "可投标商机",
                             "description": "参与可行性", "state": "chosen",
                             "score": 1, "chosen": True,
                             "member_ids": ["evt:c18", "evt:c19"]}],
            source_section_id="evt:S3", snapshot_version="v1")
        h3 = views[0]
        self.assertIn("品类匹配", h3["current_judgment"])
        self.assertIn("仍待补证", h3["current_judgment"])
        self.assertIn("不能表述为可执行商机", h3["current_judgment"])
        self.assertNotIn("品类匹配已支持", h3["current_judgment"])
        self.assertEqual(h3["pending_claim_ids"], ["evt:c18", "evt:c19"])
        self.assertTrue(any("同类项目投标业绩" in x for x in h3["next_steps"]))
        self.assertTrue(any("标的物资详细清单" in x for x in h3["next_steps"]))
        self.assertTrue(any("投标资质与交付资源" in x for x in h3["next_steps"]))


class DriftTests(unittest.TestCase):
    """⑩旁车诚实:坏指纹旁车只进计数不误标;kb 缺席全链降级不伪装。"""

    def test_bad_sidecar_only_counted(self):
        led = _fixture()
        kbp = os.path.join(tempfile.mkdtemp(), "kb.db")
        conn = cg.open_insights_rw(kbp)
        conn.execute(
            "INSERT OR REPLACE INTO insights(insight_id,event_id,kind,"
            "claim_id,hypothesis_id,value,fingerprint) VALUES(?,?,?,?,?,?,?)",
            (f"polarity:{EV}:C1:{EV}:H0", EV, "polarity", f"{EV}:C1",
             f"{EV}:H0", "正向", "wrong-fingerprint"))
        conn.commit()
        conn.close()
        kb = sqlite3.connect(kbp)
        kb.row_factory = sqlite3.Row
        p = _web(led, kb)
        self.assertGreaterEqual(p["meta"]["sidecar_drifted"], 1)
        con = next(e for e in p["edges"] if e["id"] == f"con:{EV}:C1|{EV}:H0")
        self.assertIsNone(con["polarity"])   # 漂移旁车不上边不冒充
        self.assertTrue(any(o["key"] == "sidecar_drifted" and o["n"] >= 1
                            for o in p["omissions"]))
        kb.close()

    def test_kb_none_degrades(self):
        led = _fixture()
        p = _web(led, None)
        self.assertFalse(any(n["sub_type"] == "融合" for n in p["nodes"]))
        self.assertEqual(p["meta"]["n_fusion_cards"], 0)
        self.assertEqual(validate_chain_web(p, strict=False), [])


class DegradeTests(unittest.TestCase):
    """⑪降级不伪装:无命题空事件照常出件;无论证区 conclusions=[]。"""

    def test_empty_event_builds(self):
        led = make_ledger()
        led.register_event(rumor_envelope(EV), "", EV)
        p = _web(led)
        self.assertEqual(p["claims"], [])
        self.assertEqual(p["conclusions"], [])
        self.assertEqual(validate_chain_web(p, strict=False), [])
        self.assertTrue(any(o["key"] == "n_seed_hidden" for o in p["omissions"]))


class TimelineTests(unittest.TestCase):
    """⑫时间线:台账 transitions 现成读口;as_of_round 过滤;排序确定。"""

    def test_transitions_and_as_of_filter(self):
        led = _fixture()
        p_full = _web(led)
        self.assertTrue(p_full["timeline"])
        for x in p_full["timeline"]:
            self.assertIn(x["claim_id"], {c["claim_id"] for c in p_full["claims"]})
        p_r1 = _web(led, as_of_round=1)
        self.assertTrue(all((x["round"] or 0) <= 1 for x in p_r1["timeline"]))
        keys = [(x["ts"] or "9999", x["claim_id"]) for x in p_full["timeline"]]
        self.assertEqual(keys, sorted(keys))


class RulingBackrefTests(unittest.TestCase):
    """⑬裁定反查:意见引文两态(s6 归档有 quote/reason;旧账 legacy 注记)。"""

    def test_quote_and_legacy(self):
        led = _fixture()
        p = _web(led)
        hit = p["details"][f"ruling:{EV}:C1|{EV}:E1"]
        self.assertEqual(hit["quote"], "公告载明B公司中标")
        self.assertEqual(hit["reason"], "官方公告直载")
        legacy = p["details"][f"ruling:{EV}:C2|{EV}:E3"]
        self.assertIsNone(legacy["quote"])
        self.assertTrue(legacy["legacy"])
        vf2 = next(v for v in p["verification"]
                   if v["claim_id"] == f"{EV}:C2")
        self.assertEqual(vf2["n_independent_roots"], 1)
        self.assertEqual(vf2["unruled_nums"], [])   # 无关票不算未裁


class SemanticProjectionTests(unittest.TestCase):
    """语义分层增量:直接证据状态/规则归位/家族数值规范/推理桥。"""

    def test_evidence_state_uses_accepted_edges_not_provenance(self):
        led = _fixture()
        p = _web(led)
        self.assertEqual(_claim(p, "C1")["evidence_state"], "supported")
        self.assertEqual(_claim(p, "C3")["evidence_state"], "unadjudicated")
        self.assertEqual(_claim(p, "C4")["evidence_state"], "no_direct")
        self.assertEqual(_claim(p, "C9")["evidence_state"], "contested")
        # C7 有信封种子 provenance，但没有被承认的独立直接边。
        c7 = _claim(p, "C7")
        self.assertEqual(c7["evidence_state"], "no_direct")
        self.assertEqual(c7["interpretive"], "meta_info")
        self.assertEqual(sum(p["stats"]["evidence_state_dist"].values()),
                         sum(1 for c in p["claims"] if not c["interpretive"]))

    def test_budget_family_normalization_does_not_merge_anomaly_predicate(self):
        rows = [
            {"claim_id": "evt:c22", "parent_claim_id": None,
             "text": "本次采购的预算金额 999910.0 元与历史中位数相比无显著偏离。",
             "reborn_from": ""},
            {"claim_id": "evt:c22a", "parent_claim_id": "evt:c22",
             "text": "忻州职业技术学院项目的预算金额为999,910元。",
             "reborn_from": ""},
            {"claim_id": "evt:c22aa", "parent_claim_id": "evt:c22",
             "text": "本次采购的预算金额为999910.0元。",
             "reborn_from": ""},
        ]
        fams = cgr.lineage_families(rows)
        self.assertEqual(fams, [["evt:c22a", "evt:c22aa"]])

    def test_manual_structured_inference_has_basis_and_targets(self):
        led = _fixture()
        td = tempfile.mkdtemp()
        seeds = Path(td) / "seeds.json"
        seeds.write_text(json.dumps([{
            "event_id": EV, "label": "暴雷",
            "reasoning_bridges": [{
                "id": "risk_bridge", "label": "风险条件推理",
                "basis": ["C1", "C2"], "context": [], "routed_claims": [],
                "rule": "只有多项独立风险前提同时成立时才可加强结论。",
                "inference_text": "一项前提已支持，另一项仍待补证。",
                "condition_or_gap": "拖欠工资须经独立材料复核。",
                "targets": [{"label": "暴雷", "polarity": "条件",
                             "rationale": "仅作条件限制"}],
            }],
        }], ensure_ascii=False), encoding="utf-8")
        graph = _graph(led, seeds_path=seeds)
        p = from_graph(graph, {EV: led}, None, EV, config_dir=_CONFIG,
                       generated_at=GENERATED_AT)
        ir = _node(p, "ir:risk_bridge")
        self.assertEqual(ir["basis_claim_ids"], [f"{EV}:C1", f"{EV}:C2"])
        self.assertTrue(ir["fingerprint_verified"])
        incoming = [e for e in p["edges"] if e["target"] == ir["id"]]
        outgoing = [e for e in p["edges"] if e["source"] == ir["id"]]
        self.assertGreaterEqual(len(incoming), 2)
        self.assertEqual([e["target"] for e in outgoing], [f"hy:{EV}:H0"])
        self.assertEqual(validate_chain_web(p, strict=False), [])


class InjectionTests(unittest.TestCase):
    """注入防御(JSON 契约=数据保真透传,渲染层再转义;不为方便悄悄改写)。"""

    def test_script_tag_roundtrips_as_data(self):
        led = _fixture()
        p = _web(led)
        self.assertIn("<script>alert(1)</script>",
                      _claim(p, "C4")["text"])
        self.assertEqual(validate_chain_web(p, strict=False), [])


class CliWebSmokeTests(unittest.TestCase):
    """⑭CLI 冒烟:chain-graph --format web 出件 rc=0+可加载+校验过。"""

    def test_cli_writes_valid_payload(self):
        tmp = tempfile.mkdtemp()
        dbp = os.path.join(tmp, "w.db")
        led = _fixture(db_path=dbp)
        led.conn.close()
        out = os.path.join(tmp, "chain.web.json")
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "chain-graph",
             "--event", EV, "--db", dbp, "--format", "web",
             "--config-dir", str(_CONFIG), "--out", out],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", timeout=120,
            env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        payload = json.loads(Path(out).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(validate_chain_web(payload, strict=False), [])


if __name__ == "__main__":
    unittest.main()
