# -*- coding: utf-8 -*-
"""证据链形态图测试(2026-11-05 方案 §七-6)——graph 结构/缺口四型判据五态/
旁车消费三档(人工>旁车指纹有效>无)/漂移弃用/通道白名单机器闸/LLM 生成期
落库/不回流负测试/mermaid 注入防御/噪音纪律(无关票不进图)/两层图/加法式
挂键。纪律继承:确定性(渲染两次逐字节一致)/失败不伪装(旁车缺项=边属性
不画,不伪装人工标)/三态只属于 claim(图不产生新状态)。
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
from sense2act.credibility import anchor_name, load_rules as load_ess  # noqa: E402
from sense2act.kb_chain import chain_topic, open_kb_ro  # noqa: E402
from sense2act.kb_derive import derive_event, load_rules, write_kb  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

_CONFIG = ROOT / "data" / "config"
_AUTH = {"ccgp"}


def _db_file(led: LedgerRepository) -> str:
    return led.conn.execute("PRAGMA database_list").fetchone()[2]


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
    """缺口四型齐备的最小台账:C1 已证实(普通根+ccgp 权威根=双根,翻转条件
    在场)/C2 孤证(rootC+1 无关票=查证尝试注记)/C3 未触及/C4 查证排除
    (unreachable)/C6 谓词不足(ccgp 权威单根=s1∧a 候审)/C5 派生自 C1;
    H0 暴雷={C1,C2},H1 机会={C5}。"""
    led = make_ledger()
    led.register_event(rumor_envelope("evt_g"), "", "evt_g")
    led.install_blueprint("evt_g", _bp(), aliases=ALIASES)
    for hid, label, desc, cids in (
            ("H0", "暴雷", "资金链断裂", ("C1", "C2")),
            ("H1", "机会", "扩张信号", ("C5",))):
        led.conn.execute(
            "INSERT INTO hypotheses(hypothesis_id,event_id,label,description,"
            "registered_round,updated_round) VALUES(?,?,?,?,0,0)",
            (f"evt_g:{hid}", "evt_g", label, desc))
        for cid in cids:
            led.conn.execute(
                "INSERT OR IGNORE INTO hypothesis_claims(hypothesis_id,claim_id,op)"
                " VALUES(?,?,'AND')", (f"evt_g:{hid}", f"evt_g:{cid}"))
    # C1 已证实:rootA(普通)+ccgp(权威)=双根
    e1 = add_evidence(led, "evt_g", "rootA", "公告载明B公司中标", "C1")
    e2 = add_evidence(led, "evt_g", "ccgp", "政采公告:B公司中标", "C1", platform="ccgp")
    led.set_ruling("evt_g:C1", e1, "支持", 1)
    led.set_ruling("evt_g:C1", e2, "支持", 1)
    led.update_claim_status("evt_g:C1", "已证实", None, 1, e1)
    # C2 孤证:rootC 支持+1 无关票(无关=查证尝试,不进图只计数)
    e3 = add_evidence(led, "evt_g", "rootC", "论坛帖称B公司拖欠工资", "C2")
    led.set_ruling("evt_g:C2", e3, "支持", 1)
    e4 = add_evidence(led, "evt_g", "rootC", "与拖欠无关的财务通报", "C2")
    led.set_ruling("evt_g:C2", e4, "无关", 1)
    # C3 挂证未裁(宽进关联但 S6 未裁定=evt_t 真实形态;不进图,注记留痕)
    add_evidence(led, "evt_g", "rootD", "挂账未裁的报道内容", "C3")
    # C4 查证排除:unreachable(缺口标不可达)
    led.conn.execute(
        "UPDATE claims SET unreachable=1 WHERE claim_id='evt_g:C4'")
    # C6 谓词不足:ccgp 权威单根(s==1∧a=候审,待证实)
    e5 = add_evidence(led, "evt_g", "ccgp", "登记公告载明B公司信息", "C6",
                      platform="ccgp")
    led.set_ruling("evt_g:C6", e5, "支持", 1)
    # C5 派生自 C1(S5 派生口同款:add_claim parent)
    led.add_claim("evt_g", {"id": "C5", "text": "B公司中标XX项目且金额放大",
                            "category": "事实", "section_index": 2,
                            "priority": 4, "parent": "C1"}, 2)
    led.conn.commit()
    return led


def _build(led, kb=None, seeds_path=None):
    return cgr.build_chain_graph({"evt_g": led}, kb, "evt_g",
                                 auth_roots=_AUTH, seeds_path=seeds_path)


def _gap_map(graph):
    return {c["draft_id"]: (c["gap"] or {}).get("type")
            for c in graph["nodes"]["claims"]}


class _FakeLLM:
    """生成期假 provider(照 StructuredLLM 协议;按 stage 弹回执)。"""

    model_name = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)

    def generate_json(self, system_prompt, user_prompt, *, stage=""):
        if not self.replies:
            raise RuntimeError("回执耗尽")
        return self.replies.pop(0)


class GapTypeTests(unittest.TestCase):
    """缺口四型判据五态+与锚点表逐支对齐(判据与判决梯同源锁)。"""

    def test_five_states(self):
        self.assertEqual(cgr.gap_type_of("待证实", 0, False, False), "未触及")
        self.assertEqual(cgr.gap_type_of("待证实", 0, False, True), "查证排除")
        self.assertEqual(cgr.gap_type_of("待证实", 1, False, False), "孤证")
        self.assertEqual(cgr.gap_type_of("待证实", 1, True, False), "谓词不足")
        self.assertEqual(cgr.gap_type_of("待证实", 2, False, False), "谓词不足")

    def test_non_pending_no_gap(self):
        self.assertIsNone(cgr.gap_type_of("已证实", 0, False, False))
        self.assertIsNone(cgr.gap_type_of("矛盾", 2, False, True))

    def test_anchor_alignment(self):
        """判据↔锚点名逐支对齐:同输入下 gap 型与 anchor_name 同源
        (判决梯改此处必须同步——测试锁)。"""
        mapping = {"未触及": "未触及", "查证排除": "弱(查过没有)",
                   "孤证": "弱(孤证)", "谓词不足": "候审"}
        for a in (False, True):
            for tried in (False, True):
                for s in (0, 1, 2):
                    st = "待证实"
                    g = cgr.gap_type_of(st, s, a, tried)
                    an = anchor_name(st, s, a, False, tried)
                    self.assertEqual(mapping[g], an,
                                     f"s={s} a={a} tried={tried}")

    def test_labels_complete(self):
        for t in cgr.GAP_TYPES:
            self.assertTrue(cgr.GAP_LABELS.get(t))


class BuildGraphTests(unittest.TestCase):
    """graph JSON 结构:节点/边/聚类/meta(第一公民;加法式不改五投影)。"""

    def setUp(self):
        self.led = _fixture()
        self.g = _build(self.led)

    def test_meta_counts(self):
        m = self.g["meta"]
        self.assertEqual(m["event_id"], "evt_g")
        self.assertEqual(m["n_claims"], 6)          # C1-C6(C5 派生)
        self.assertEqual(m["n_pending"], 5)
        self.assertEqual(m["n_evidence_drawn"], 4)  # 无关票/未裁件不进图
        self.assertEqual(m["n_tried_hidden"], 1)    # 无关票=查证尝试注记
        self.assertEqual(m["n_unadjudicated"], 1)   # 挂账未裁=另一种留痕
        self.assertFalse(m["co_root_omitted"])

    def test_gap_nodes_four_types(self):
        gaps = _gap_map(self.g)
        self.assertEqual(gaps["C1"], None)          # 已证实无缺口
        self.assertEqual(gaps["C2"], "孤证")
        self.assertEqual(gaps["C3"], "未触及")
        self.assertEqual(gaps["C4"], "查证排除")
        self.assertEqual(gaps["C6"], "谓词不足")
        c2 = next(c for c in self.g["nodes"]["claims"] if c["draft_id"] == "C2")
        self.assertEqual(c2["gap"]["tries"], 1)     # 无关票=尝试注记
        self.assertEqual(c2["gap"]["channel"], "")  # 无旁车=无通道(失败不伪装)
        c3 = next(c for c in self.g["nodes"]["claims"] if c["draft_id"] == "C3")
        self.assertEqual(c3["gap"]["tries"], 0)
        self.assertEqual(c3["gap"]["unadjudicated"], 1)  # 挂证未裁≠已查

    def test_support_edges_attrs(self):
        sup = {(e["src"].rsplit(":", 1)[-1], e["dst"].rsplit(":", 1)[-1]): e
               for e in self.g["edges"]["supports"]}
        self.assertEqual(len(sup), 4)
        # 权威根标记(ccgp∈auth_roots)
        self.assertTrue(sup[("E2", "C1")]["authoritative"])
        self.assertFalse(sup[("E1", "C1")]["authoritative"])
        # 独立根编号(同 claim 内按根名序:ccgp < rootA)
        self.assertEqual(sup[("E1", "C1")]["root_no"], 2)
        self.assertEqual(sup[("E2", "C1")]["root_no"], 1)
        # 翻转条件标注(C1 已证实 s_eff=2;待证实/余根足=无标注)
        self.assertIn("仅剩 1 根", sup[("E1", "C1")]["flip"])
        self.assertIsNone(sup[("E6", "C6")]["flip"])
        self.assertEqual(cgr._flip_note("已证实", 1), "唯一根失效→回待证实")
        self.assertEqual(cgr._flip_note("已证实", 3), None)
        self.assertEqual(cgr._flip_note("待证实", 1), None)
        # 无关票无边
        self.assertNotIn(("E4", "C2"), sup)

    def test_derives_edges(self):
        self.assertEqual(self.g["edges"]["derives"],
                         [{"src": "evt_g:C1", "dst": "evt_g:C5"}])

    def test_conveys_default_neutral(self):
        """无 seeds 无旁车=传导边不带极性(失败不伪装,不伪装人工标)。"""
        for e in self.g["edges"]["conveys"]:
            self.assertIsNone(e["polarity"])
            self.assertFalse(e["manual"])

    def test_gist_label_priority(self):
        c1 = next(c for c in self.g["nodes"]["claims"] if c["draft_id"] == "C1")
        self.assertEqual(c1["label"], 'B公司中标"XX"项目')
        c2 = next(c for c in self.g["nodes"]["claims"] if c["draft_id"] == "C2")
        self.assertTrue(c2["label"])   # 无 gist=fallback 截断(照 gist 纪律)

    def test_deterministic_payload(self):
        self.assertEqual(cgr.payload_to_json(_build(self.led)),
                         cgr.payload_to_json(self.g))

    def test_missing_main_raises(self):
        with self.assertRaises(cgr.ChainGraphError):
            cgr.build_chain_graph({}, None, "evt_g")


class CoRootTests(unittest.TestCase):
    """共据边(冗余拓扑):共享支持根才连;派生对不重复画。"""

    def test_co_root_shared_support(self):
        led = make_ledger()
        led.register_event(rumor_envelope("evt_g"), "", "evt_g")
        led.install_blueprint("evt_g", _bp(), aliases=ALIASES)
        e = add_evidence(led, "evt_g", "rootC", "同源内容", "C2")
        led.set_ruling("evt_g:C2", e, "支持", 1)
        led.conn.execute(
            "UPDATE claims SET text='B公司海外诉讼缠身' WHERE claim_id='evt_g:C3'")
        led.associate("evt_g:C3", e, 1)
        led.set_ruling("evt_g:C3", e, "支持", 1)
        led.conn.commit()
        g = cgr.build_chain_graph({"evt_g": led}, None, "evt_g", auth_roots=_AUTH)
        self.assertEqual(g["edges"]["co_roots"],
                         [{"a": "evt_g:C2", "b": "evt_g:C3",
                           "roots": ["rootC"]}])
        self.assertEqual(g["meta"]["n_co_root_edges"], 1)


class ManualPolarityTests(unittest.TestCase):
    """seeds 人工 polarity 覆写口(加法式):人工优先,LLM 只判人工没标的。"""

    def test_manual_polarity_wins(self):
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "seeds.json"
            seeds.write_text(json.dumps([
                {"label": "暴雷", "description": "d",
                 "polarity": {"C1": "正向", "C2": "反向"}},
                {"label": "旧格式条目", "description": "d"},   # 无 polarity 键=兼容
                {"label": "坏值条目", "polarity": {"C3": "侧向"}},  # 值域外=忽略
            ], ensure_ascii=False), encoding="utf-8")
            g = _build(led, seeds_path=seeds)
        pol = {(e["src"].rsplit(":", 1)[-1], e["dst"].rsplit(":", 1)[-1]):
               (e["polarity"], e["manual"]) for e in g["edges"]["conveys"]}
        self.assertEqual(pol[("C1", "H0")], ("正向", True))
        self.assertEqual(pol[("C2", "H0")], ("反向", True))
        self.assertEqual(pol[("C5", "H1")], (None, False))  # 人工没标=无极性

    def test_missing_seeds_file_ok(self):
        g = _build(_fixture(), seeds_path="Z:/不存在/seeds.json")
        for e in g["edges"]["conveys"]:
            self.assertIsNone(e["polarity"])


class SidecarConsumeTests(unittest.TestCase):
    """旁车消费:polarity/channel 指纹校验;漂移弃用;白名单机器闸。"""

    def _with_sidecar(self, led):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(cg.SIDECAR_DDL)
        f_pol = cgr.polarity_fp("B公司拖欠工资", "待证实", "", "暴雷")
        conn.execute(
            "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
            "hypothesis_id,value,rationale,fingerprint,model,prompt_version,"
            "created_at) VALUES('polarity:evt_g:C2:evt_g:H0','evt_g','polarity',"
            "'evt_g:C2','evt_g:H0','反向','存续与暴雷语义相反',?,'m','v','t')",
            (f_pol,))
        f_ch = cgr.channel_fp("B公司拖欠工资", "待证实", "孤证")
        conn.execute(
            "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
            "hypothesis_id,value,rationale,fingerprint,model,prompt_version,"
            "created_at) VALUES('channel:evt_g:C2','evt_g','channel',"
            "'evt_g:C2','','ccgp','政采公告可查',?,'m','v','t')", (f_ch,))
        # 漂移行(指纹不符)+白名单外通道(微博不走 S3)各一条
        conn.execute(
            "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
            "hypothesis_id,value,rationale,fingerprint,model,prompt_version,"
            "created_at) VALUES('polarity:evt_g:C3:evt_g:H0','evt_g','polarity',"
            "'evt_g:C3','evt_g:H0','正向','x','deadbeef','m','v','t')")
        f_ch3 = cgr.channel_fp("B公司涉及海外诉讼", "待证实", "未触及")
        conn.execute(
            "INSERT INTO chain_sidecar(sidecar_id,event_id,kind,claim_id,"
            "hypothesis_id,value,rationale,fingerprint,model,prompt_version,"
            "created_at) VALUES('channel:evt_g:C3','evt_g','channel',"
            "'evt_g:C3','','微博','x',?,'m','v','t')", (f_ch3,))
        conn.commit()
        return conn

    def test_sidecar_consumed_and_drift_dropped(self):
        led = _fixture()
        kb = self._with_sidecar(led)
        g = _build(led, kb=kb)
        pol = {(e["src"].rsplit(":", 1)[-1], e["dst"].rsplit(":", 1)[-1]):
               (e["polarity"], e["rationale"], e["manual"])
               for e in g["edges"]["conveys"]}
        self.assertEqual(pol[("C2", "H0")][0], "反向")
        self.assertEqual(pol[("C2", "H0")][1], "存续与暴雷语义相反")
        self.assertFalse(pol[("C2", "H0")][2])
        c2 = next(c for c in g["nodes"]["claims"] if c["draft_id"] == "C2")
        self.assertEqual(c2["gap"]["channel"], "ccgp")
        self.assertEqual(c2["gap"]["channel_rationale"], "政采公告可查")
        c3 = next(c for c in g["nodes"]["claims"] if c["draft_id"] == "C3")
        self.assertEqual(c3["gap"]["channel"], "")   # 白名单外=丢弃不伪装
        self.assertEqual(g["meta"]["sidecar_drifted"], 1)
        self.assertEqual(g["meta"]["channels_dropped"], 1)

    def test_drift_on_status_change(self):
        """命题状态变=指纹失配=弃用(漂移即重生成;渲染期零 LLM)。"""
        led = _fixture()
        kb = self._with_sidecar(led)
        led.update_claim_status("evt_g:C2", "已证实", None, 2)
        g = _build(led, kb=kb)
        pol = {e["src"]: e["polarity"] for e in g["edges"]["conveys"]}
        self.assertIsNone(pol["evt_g:C2"])
        # 漂移=3:C2 极性行+C2 通道行(状态变=指纹失配)+C3 死指纹行
        self.assertEqual(g["meta"]["sidecar_drifted"], 3)

    def test_manual_overrides_sidecar(self):
        led = _fixture()
        kb = self._with_sidecar(led)
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "s.json"
            seeds.write_text(json.dumps(
                [{"label": "暴雷", "polarity": {"C2": "正向"}}],
                ensure_ascii=False), encoding="utf-8")
            g = _build(led, kb=kb, seeds_path=seeds)
        pol = {(e["src"].rsplit(":", 1)[-1]): (e["polarity"], e["manual"])
               for e in g["edges"]["conveys"]}
        self.assertEqual(pol["C2"], ("正向", True))   # 人工优先


class ChainGenTests(unittest.TestCase):
    """生成期(LLM 两处)→旁车落库:人工跳过/无效计数/白名单丢弃/失败不伪装。"""

    def test_run_writes_sidecar_and_graph_consumes(self):
        led = _fixture()
        kbp = Path(_db_file(led)).parent / "kb_gen_test.db"
        if kbp.exists():
            kbp.unlink()
        replies = [
            {"items": [
                {"claim_id": "evt_g:C2", "hypothesis_id": "evt_g:H0",
                 "polarity": "反向", "rationale": "存续与暴雷语义相反"},
                {"claim_id": "evt_g:C9", "hypothesis_id": "evt_g:H0",
                 "polarity": "正向", "rationale": "幻觉 id"},
                {"claim_id": "evt_g:C5", "hypothesis_id": "evt_g:H1",
                 "polarity": "侧向", "rationale": "值域外"},
            ]},
            {"items": [
                {"claim_id": "evt_g:C2", "channel": "ccgp", "rationale": "政采可查"},
                {"claim_id": "evt_g:C3", "channel": "微博", "rationale": "x"},
            ]},
        ]
        stats = cg.run_chain_gen({"evt_g": led}, kbp, "evt_g",
                                 provider=_FakeLLM(replies), auth_roots=_AUTH,
                                 config_dir=_CONFIG)
        self.assertEqual(stats["polarity_total"], 3)      # C1,C2→H0;C5→H1
        self.assertEqual(stats["polarity_written"], 1)    # C2(其余无效/幻觉)
        self.assertEqual(stats["polarity_invalid"], 2)
        self.assertEqual(stats["channel_total"], 5)       # C2,C3,C4,C5,C6 缺口
        self.assertEqual(stats["channel_written"], 1)
        self.assertEqual(stats["channel_dropped"], 1)     # 微博=白名单外
        kb = open_kb_ro(kbp)
        try:
            rows = [dict(r) for r in kb.execute("SELECT * FROM chain_sidecar")]
            self.assertEqual(len(rows), 2)
            prow = next(r for r in rows if r["kind"] == "polarity")
            self.assertEqual(prow["value"], "反向")
            self.assertEqual(prow["model"], "fake-model")
            self.assertEqual(prow["prompt_version"], cg.PROMPT_VERSION)
            # 指纹跨模块一致(写入与消费校验同一把尺)
            self.assertEqual(prow["fingerprint"],
                             cgr.polarity_fp("B公司拖欠工资",
                                             "待证实", "", "暴雷"))
            g = cgr.build_chain_graph({"evt_g": led}, kb, "evt_g",
                                      auth_roots=_AUTH)
        finally:
            kb.close()
            kbp.unlink()
        pol = {e["src"].rsplit(":", 1)[-1]: e["polarity"]
               for e in g["edges"]["conveys"]}
        self.assertEqual(pol["C2"], "反向")
        self.assertIsNone(pol["C1"])      # 无 seeds=无人工标记
        c2 = next(c for c in g["nodes"]["claims"] if c["draft_id"] == "C2")
        self.assertEqual(c2["gap"]["channel"], "ccgp")

    def test_manual_skipped_in_gen(self):
        """人工已标=生成期跳过(人工优先,LLM 只判人工没标的)。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            seeds = Path(td) / "s.json"
            seeds.write_text(json.dumps(
                [{"label": "暴雷", "polarity": {"C1": "正向"}}],
                ensure_ascii=False), encoding="utf-8")
            kbp = Path(td) / "kb.db"
            stats = cg.run_chain_gen(
                {"evt_g": led}, kbp, "evt_g",
                provider=_FakeLLM([{"items": []}, {"items": []}]),
                auth_roots=_AUTH, config_dir=_CONFIG, seeds_path=seeds)
        self.assertEqual(stats["polarity_total"], 3)
        self.assertEqual(stats["polarity_manual_skipped"], 1)

    def test_provider_failure_no_writes(self):
        """LLM 失败=旁车缺项=零落库(失败不伪装;stats 留错误串)。"""
        led = _fixture()
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"

            class _Boom:
                model_name = "boom"

                def generate_json(self, *a, **k):
                    raise RuntimeError("api down")

            stats = cg.run_chain_gen({"evt_g": led}, kbp, "evt_g",
                                     provider=_Boom(), auth_roots=_AUTH,
                                     config_dir=_CONFIG)
            self.assertIn("api down", stats["polarity_error"])
            self.assertIn("api down", stats["channel_error"])
            conn = sqlite3.connect(kbp)
            n = conn.execute("SELECT COUNT(*) FROM chain_sidecar").fetchone()[0]
            conn.close()
        self.assertEqual(n, 0)

    def test_sidecar_upsert_idempotent(self):
        """同 sidecar_id 重写=REPLACE 一行(重跑生成期=整体刷新)。"""
        with tempfile.TemporaryDirectory() as td:
            kbp = Path(td) / "kb.db"
            conn = cg.open_sidecar_rw(kbp)
            for _ in range(2):
                cg._put(conn, event_id="e", kind="polarity",
                        claim_id="e:C1", hypothesis_id="e:H0", value="正向",
                        rationale="r", fingerprint="f", model="m")
            n = conn.execute("SELECT COUNT(*) FROM chain_sidecar").fetchone()[0]
            conn.close()
        self.assertEqual(n, 1)


class RenderTests(unittest.TestCase):
    """mermaid 渲染:注入防御/两层图/确定性/围栏/图例。"""

    def setUp(self):
        self.led = _fixture()
        self.g = _build(self.led)

    def test_quote_injection_defense(self):
        """gist 含直引号/换行=_mq 折单引号+换行剥除(mermaid 11.4.1 实弹形态)。"""
        text = cgr.render_graph_full(self.g)
        self.assertNotIn('"XX"', text)          # 折成单引号
        self.assertIn("'XX'", text)
        for line in text.splitlines():
            self.assertNotIn("\n", line)

    def test_full_graph_four_layers(self):
        text = cgr.render_graph_full(self.g)
        self.assertTrue(text.startswith("flowchart TD"))
        self.assertIn("hypDom", text)            # 研判层
        self.assertIn("clVerified", text)        # 结论层(实心)
        self.assertIn("gapNode", text)           # 缺口层
        self.assertIn("subgraph SG", text)       # 证据层(同根聚类框)
        self.assertIn("载明·根", text)
        self.assertIn("派生", text)
        self.assertIn("stroke-width:3px", text)  # 权威根加粗
        self.assertIn("挂证 1 件未裁", text)      # 挂账未裁注记(不冒充已查)
        self.assertNotIn("E4", text)             # 无关票不进图(噪音纪律)
        self.assertNotIn("[E5]", text)           # 未裁件同样不进图

    def test_claim_closure_subgraph(self):
        text = cgr.render_graph_claim(self.g, "C5")     # draft_id 便捷形态
        self.assertIn("CL", text)
        self.assertIn("派生", text)                      # 祖先 C1 在场
        self.assertNotIn("E4", text)
        with self.assertRaises(cgr.ChainGraphError):
            cgr.render_graph_claim(self.g, "不存在")

    def test_deterministic_render(self):
        self.assertEqual(cgr.render_graph_full(self.g),
                         cgr.render_graph_full(_build(self.led)))
        self.assertEqual(cgr.render_graph_claim(self.g, "C5"),
                         cgr.render_graph_claim(_build(self.led), "C5"))

    def test_section_md(self):
        md = cgr.graph_section_md(self.g, "C5")
        self.assertIn("图例", md)
        self.assertIn("```mermaid", md)
        self.assertIn("单命题前提闭包图", md)

    def test_co_root_omitted_note(self):
        g = _build(self.led)
        g["meta"]["co_root_omitted"] = True
        self.assertIn("超限省略", cgr.graph_section_md(g))


class NoBackflowTests(unittest.TestCase):
    """不回流负测试(照 ESS 不回流先例):判决/评分层不 import 图层与生成期。"""

    def test_judgment_layers_pure(self):
        for name in ("stages/s6_verdict.py", "credibility.py",
                     "predicate_check.py", "stages/s2_planner.py"):
            src = (ROOT / "sense2act" / name).read_text(encoding="utf-8")
            self.assertNotIn("chain_graph", src, name)
            self.assertNotIn("chain_gen", src, name)


class AdditiveMountTests(unittest.TestCase):
    """五投影挂 graph 键(加法式):默认零变化,with_graph=True 才附键。"""

    def setUp(self):
        self.led = _fixture()
        rules = load_rules(_CONFIG / "kb_rules.json", _CONFIG)
        res = derive_event(self.led.conn, "evt_g", rules, {})
        self.kb_path = Path(_db_file(self.led)).parent / "kb_mount_test.db"
        write_kb(self.kb_path, [res], [], rules["version"])
        self.kb = open_kb_ro(self.kb_path)
        self.mains = {"evt_g": self.led}
        ent = self.kb.execute(
            "SELECT entity_id FROM entity WHERE kind='实体' LIMIT 1").fetchone()
        self.ent_id = ent["entity_id"]

    def tearDown(self):
        self.kb.close()
        self.kb_path.unlink(missing_ok=True)

    def test_default_no_graph_key(self):
        out = chain_topic(self.mains, self.kb, self.ent_id)
        self.assertNotIn("graph", out)

    def test_with_graph_adds_key(self):
        out = chain_topic(self.mains, self.kb, self.ent_id, with_graph=True)
        self.assertIn("graph", out)
        g = out["graph"]["evt_g"]
        self.assertEqual(g["meta"]["event_id"], "evt_g")
        self.assertEqual(len(g["nodes"]["claims"]), 6)


class CliSmokeTests(unittest.TestCase):
    """chain-graph CLI:json=graph JSON 第一公民(机器可解析;md=投影)。"""

    def test_chain_graph_json(self):
        import os
        import subprocess
        led = _fixture()
        dbp = _db_file(led)
        led.conn.close()
        env = dict(os.environ, PYTHONUTF8="1")
        r = subprocess.run(
            [sys.executable, "-m", "sense2act", "chain-graph",
             "--event", "evt_g", "--db", dbp, "--kb", "data/kb.db",
             "--format", "json", "--claim", "C5"],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(ROOT), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        graph = json.loads(r.stdout)
        self.assertEqual(graph["meta"]["event_id"], "evt_g")
        self.assertEqual(len(graph["edges"]["derives"]), 1)


if __name__ == "__main__":
    unittest.main()
