"""证据图谱单元(定稿版):三态边 / 复合聚合 / 检测器配对 / 清洗 / 判别语义 /
零截断 / 解读排除 / 占位围栏 / 确定性 / payload 复用锁。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_evidence, make_ledger, rumor_envelope  # noqa: E402

from sense2act.evidence_graph import (  # noqa: E402
    MAX_CLAIMS,
    build_graph_payload,
    payload_to_json,
    render_b1,
    render_b2,
    render_fact_details,
    render_relations,
    render_section,
)
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    HypothesisSeed,
)

EID = "evt_graph"


def _bp(claims, hypotheses):
    return AnalysisBlueprint(claims=claims, hypotheses=hypotheses, gaps=[])


def _fact(cid, text, **kw):
    return ClaimDraft(id=cid, text=text, category=ClaimCategory.FACT,
                      section_index=2, **kw)


def _rich(leader_score: int):
    """h0/h1 共享 C1(已证实,+1 各);h2 独占 C2(恒已证实,+1)
    加 leader_score 条互异独占事实(已证实)。leader_score=1 → h2 唯一最高分。"""
    led = make_ledger()
    extra_texts = ["产能扩张计划已获批运行",
                   "新客户签约公告已张贴",
                   "新产线验收报告已归档"]
    extra = [_fact(f"C{10 + i}", extra_texts[i]) for i in range(leader_score)]
    bp = _bp(
        [_fact("C1", "共享查实事实"),
         _fact("C2", "专属查实事实:合同与公告互证"),
         *extra],
        [HypothesisSeed(id="H0", label="假设甲", claim_ids=["C1"]),
         HypothesisSeed(id="H1", label="假设乙", claim_ids=["C1"]),
         HypothesisSeed(id="H2", label="假设丙",
                        claim_ids=["C2"] + [c.id for c in extra])])
    led.register_event(rumor_envelope(EID), "", EID)
    led.install_blueprint(EID, bp, aliases={})
    led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
    led.update_claim_status(f"{EID}:C2", "已证实", None, 1)
    for i in range(leader_score):
        led.update_claim_status(f"{EID}:C{10 + i}", "已证实", None, 1)
    return led


class TestRelationEdgeStates(unittest.TestCase):
    """图 A:边带三态(实线/虚线/红)。"""

    def _relation_ledger(self, status):
        led = make_ledger()
        bp = _bp(
            [ClaimDraft(id="C1", text="A公司投资了乙公司",
                        category=ClaimCategory.RELATION, section_index=2,
                        relation_type="持股", entity_names=["A公司", "乙公司"])],
            [HypothesisSeed(id="H0", label="扩张", claim_ids=["C1"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        if status:
            led.update_claim_status(f"{EID}:C1", status, None, 1)
        return led

    def test_pending_relation_is_dashed(self):
        text = render_relations(build_graph_payload(self._relation_ledger(None), EID))
        self.assertIn("-.->", text)
        self.assertIn("? 持股", text)

    def test_verified_relation_is_solid(self):
        text = render_relations(
            build_graph_payload(self._relation_ledger("已证实"), EID))
        self.assertIn('---|"持股"|', text)

    def test_conflict_relation_is_red(self):
        text = render_relations(
            build_graph_payload(self._relation_ledger("矛盾"), EID))
        self.assertIn("linkStyle", text)
        self.assertIn("stroke:#c9302c", text)

    def test_no_edges_yields_plain_text_placeholder(self):
        led = make_ledger()
        bp = _bp([_fact("C1", "普通事实")],
                 [HypothesisSeed(id="H0", label="X", claim_ids=["C1"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        text = render_relations(build_graph_payload(led, EID))
        self.assertIn("图 A 省略", text)
        self.assertFalse(text.lstrip().startswith("flowchart"))


class TestCompoundFacts(unittest.TestCase):
    """复合聚合:按主语实体;解读类排除;清洗;计数行。"""

    def test_grouping_by_subject_and_exclusion(self):
        led = make_ledger()
        bp = _bp(
            [_fact("C1", "B公司拖欠工资", entity_names=["B公司"]),
             _fact("C2", "B公司账户冻结", entity_names=["B公司"]),
             _fact("C3", "A公司未公告", entity_names=["A公司"]),
             _fact("C9", "解读将本信号定性为「商机」")],
            [HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1", "C3"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
        p = build_graph_payload(led, EID)
        self.assertEqual(p["excluded_interpretation"], 1)
        self.assertFalse(any(c["draft_id"] == "C9" for c in p["claims"]))
        text = render_b1(p)
        self.assertIn("关于B公司", text)                 # 节点短标题
        self.assertNotIn("定性为", text)                 # 解读类不进图

    def test_compound_title_has_count(self):
        led = make_ledger()
        bp = _bp([_fact("C1", "B公司欠薪", entity_names=["B公司"])],
                 [HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
        text = render_b1(build_graph_payload(led, EID))
        self.assertIn("1 项查实", text)

    def test_pending_claims_excluded_from_compound_groups(self):
        """2026-10-16 修复(中山大学跑实战):待证实条经 subjects 兜底混进
        "其他事实 · N 项查实"冒充查实——复合聚合只收已证实条。"""
        led = make_ledger()
        bp = _bp(
            [_fact("C1", "B公司欠薪已查明", entity_names=["B公司"]),
             _fact("C2", "B公司境外藏有隐匿账户且数额巨大")],
            [HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1", "C2"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
        p = build_graph_payload(led, EID)
        b1 = render_b1(p)
        self.assertIn("B公司欠薪已查明", b1)
        self.assertNotIn("隐匿账户", b1)             # 待证实条不进复合组
        self.assertNotIn("其他事实", b1)             # 无无主已证实条时该组不存在
        self.assertNotIn("隐匿账户", render_b2(p))
        self.assertNotIn("隐匿账户", render_fact_details(p))

    def test_hypothesis_edges_deduplicated(self):
        """同一(复合事实组,假设)只出一条支撑边——原 excl/share 两表可对
        同一对各产一条,渲染重复边(2026-10-16 修复)。"""
        led = make_ledger()
        bp = _bp(
            [_fact("C1", "B公司欠薪已查明", entity_names=["B公司"]),
             _fact("C2", "B公司账户冻结", entity_names=["B公司"]),
             _fact("C3", "A公司未公告", entity_names=["A公司"])],
            [HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1", "C3"]),
             HypothesisSeed(id="H1", label="回暖", claim_ids=["C3"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        for cid in ("C1", "C2", "C3"):
            led.update_claim_status(f"{EID}:{cid}", "已证实", None, 1)
        b1 = render_b1(build_graph_payload(led, EID))
        edge_lines = [ln.strip() for ln in b1.splitlines() if "支撑|" in ln]
        self.assertTrue(edge_lines)
        self.assertEqual(len(edge_lines), len(set(edge_lines)))

    def test_quoted_text_folds_for_mermaid(self):
        """直引号折单引号(2026-10-16 中山大学跑实战):上游项目编号自带
        "..." 引号会提前终止 mermaid "..." 标签 -> Syntax error(11.4.1 实测)。"""
        led = make_ledger()
        bp = _bp([_fact("C1", '项目编号为"中大招（货）[2026]209号"的公告',
                        entity_names=["B公司"])],
                 [HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        led.update_claim_status(f"{EID}:C1", "已证实", None, 1)
        b1 = render_b1(build_graph_payload(led, EID))
        self.assertNotIn('为"中大招', b1)
        self.assertIn("为'中大招", b1)


class TestCleanFact(unittest.TestCase):
    def setUp(self):
        from sense2act.evidence_graph import _clean_fact
        self.fn = _clean_fact

    def test_envelope_meta_stripped(self):
        self.assertEqual(self.fn("信封载明项目编号为 X。"), "项目编号为 X")
        # 2026-10-16:前缀剥除后残留冒号("· :本次…"形态)一并清掉
        self.assertEqual(self.fn("信封载明:本次招标项目编号为 X。"),
                         "本次招标项目编号为 X")
        self.assertEqual(self.fn("信封自述:正文未抓取。"), "正文未抓取")

    def test_interpretation_prefix_and_reason_tail(self):
        self.assertEqual(
            self.fn("解读将本信号定性为「商机」，理由为信封 kind 为 tender。"),
            "本信号定性为「商机」")                       # 内容引号保留(标点不漏)

    def test_url_replaced(self):
        out = self.fn("公告链接 https://example.com/a?b=1 已归档")
        self.assertNotIn("http", out)
        self.assertIn("链接见附录 A", out)

    def test_yuan_friendly(self):
        self.assertEqual(self.fn("本项目金额为 6270000.0 元"), "本项目金额为 627 万元")

    def test_list_quotes_preserved(self):
        out = self.fn("假设种子标签为「正常大额采购」「真实经营动态」")
        self.assertEqual(out.count("「"), 2)             # 列表型引号不剥

    def test_single_quote_pair_stripped(self):
        self.assertEqual(self.fn("「项目名称」"), "项目名称")


class TestWrap(unittest.TestCase):
    def setUp(self):
        from sense2act.evidence_graph import _wrap
        self.fn = _wrap

    def test_no_truncation(self):
        out = self.fn("长" * 80, 26)
        self.assertNotIn("…", out)
        self.assertEqual(out.replace("<br/>", ""), "长" * 80)

    def test_ascii_word_protected(self):
        out = self.fn("得分 0.800 且 basisSufficient 为 true", 22)
        self.assertIn("basisSufficient", out)            # 单词不被腰斩
        self.assertNotIn("Suffic<br/>", out)


class TestJudgementSemantics(unittest.TestCase):
    """判别语义:实线=结论(唯一最高分),虚线=候选(含并列领先)。"""

    def test_unique_leader_is_solid_conclusion(self):
        led = _rich(leader_score=2)                      # h2 = C2+C10+C11 = +3 唯一最高
        text = render_b1(build_graph_payload(led, EID))
        self.assertIn("(结论 · 得分 3)🏆", text)
        self.assertIn("-->", text)                       # 指向结论的实线
        self.assertIn(":::hypConc", text)

    def test_tie_is_dashed_candidates(self):
        led = _rich(leader_score=0)                      # 全 0 分:并列/候选
        text = render_b1(build_graph_payload(led, EID))
        self.assertNotIn("🏆", text)
        self.assertEqual(text.count(":::hypCand"), 3)    # 全部候选
        self.assertIn("并列领先", text)
        self.assertIn("-.->", text)


class TestZeroTruncation(unittest.TestCase):
    def test_fact_lines_have_no_ellipsis(self):
        led = _rich(leader_score=0)
        p = build_graph_payload(led, EID)
        text = render_b1(p)
        self.assertNotIn("…", text)                     # 明细全文零截断


class TestDeterminismAndPayload(unittest.TestCase):
    def test_render_deterministic(self):
        led = _rich(leader_score=1)
        p = build_graph_payload(led, EID)
        self.assertEqual(render_section(p), render_section(p))

    def test_payload_json_roundtrip(self):
        led = _rich(leader_score=1)
        p = build_graph_payload(led, EID)
        rt = json.loads(payload_to_json(p))
        self.assertEqual(rt["event_id"], EID)
        self.assertEqual(len(rt["claims"]), len(p["claims"]))


class TestBudget(unittest.TestCase):
    def test_over_budget_trimmed_with_note(self):
        led = make_ledger()
        bp = _bp([_fact(f"C{i:02d}", f"事实{i}") for i in range(MAX_CLAIMS + 5)],
                 [HypothesisSeed(id="H0", label="X", claim_ids=[])])
        led.register_event(rumor_envelope(EID), "", EID)
        led.install_blueprint(EID, bp, aliases={})
        p = build_graph_payload(led, EID)
        self.assertEqual(len(p["claims"]), MAX_CLAIMS)
        self.assertEqual(p["omitted"], 5)
        self.assertIn("省略 5 条", render_section(p))


if __name__ == "__main__":
    unittest.main()
