"""S6 五步裁定:分权(模型意见/代码判决)/ 验票协议 / 定级规则 / 成因分类。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (ALIASES, add_evidence, install_simple, make_domain,  # noqa: E402
                     make_ledger, rumor_envelope)
from sense2act.models.base import ClaimCategory, ClaimStatus, Direction  # noqa: E402
from sense2act.models.intel import AnalysisBlueprint, ClaimDraft, HypothesisSeed  # noqa: E402
from sense2act.models.verdict import (EvidenceJudgment, Opinion,  # noqa: E402
                                      SubclaimProposal)
from sense2act.stages.s6_verdict import VerdictEngine  # noqa: E402

AUTH = make_domain().authoritative_roots


class ScriptedOpinions:
    """脚本化意见提供者(顺序出票;可注入坏票)。"""

    def __init__(self, opinions: list[Opinion]):
        self._queue = list(opinions)

    def opinion(self, claim, evidence, feedback=""):
        if not self._queue:
            raise AssertionError("意见队列耗尽")
        return self._queue.pop(0)


def opinion(claim_id, judgments, verdict=ClaimStatus.PENDING, reason=""):
    return Opinion(claim_id=claim_id,
                   evidence_judgments=[EvidenceJudgment(
                       evidence_id=eid, direction=Direction(d), quote=q, reason=r)
                       for eid, d, q, r in judgments],
                   verdict=verdict, reason=reason)


class TestVerdictRules(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _engine(self, provider):
        return VerdictEngine(self.led, AUTH, provider, retry=2)

    def test_authoritative_opposition_contradicts(self):
        """权威登记源反证 -> 矛盾(矛盾翻转)。"""
        e1 = add_evidence(self.led, "evt_t", "zxgk", "无失信记录", "C1")
        e2 = add_evidence(self.led, "evt_t", "wenshu", "文书已履行", "C1")
        e3 = add_evidence(self.led, "evt_t", "rsj", "欠薪名单无记录", "C1")
        prov = ScriptedOpinions([opinion("evt_t:C1", [
            ("evt_t:E1", "矛盾", "无失信记录", ""), ("evt_t:E2", "矛盾", "文书已履行", ""),
            ("evt_t:E3", "矛盾", "欠薪名单无记录", ""),
        ], verdict=ClaimStatus.CONTRADICTED)])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.CONTRADICTED)
        self.assertEqual(
            self.led.claim_row("evt_t:C1")["status"], "矛盾")
        t = self.led.transitions("evt_t")[0]
        self.assertEqual(t["cause"], "矛盾翻转")

    def test_two_independent_roots_verify(self):
        """独立谱系根 >=2 -> 已证实(裁决记 NULL 成因:是澄清非翻转)。"""
        e1 = add_evidence(self.led, "evt_t", "exchange", "年报披露持股", "C2")
        e2 = add_evidence(self.led, "evt_t", "ggzy", "交易公示", "C2")
        prov = ScriptedOpinions([opinion("evt_t:C2", [
            ("evt_t:E1", "支持", "年报披露持股", ""), ("evt_t:E2", "支持", "交易公示", ""),
        ], verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertEqual(sorted(result.support_roots), ["exchange", "ggzy"])
        self.assertEqual(self.led.transitions("evt_t")[0]["cause"], None)

    def test_single_authoritative_exception(self):
        """权威登记源单源例外 -> 已证实。"""
        e1 = add_evidence(self.led, "evt_t", "gsxt", "登记状态存续", "C1")
        prov = ScriptedOpinions([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "登记状态存续", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_single_non_authoritative_overridden(self):
        """孤证非权威:LLM 意见被代码否决,维持待证实。"""
        e1 = add_evidence(self.led, "evt_t", "weibo.com", "爆料称欠薪", "C1")
        prov = ScriptedOpinions([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "爆料称欠薪", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)
        self.assertTrue(result.overridden)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")

    def test_registry_conflict_is_contradiction(self):
        """支持方与反方均为权威登记源 -> 矛盾(双口径),不得压成已证实。"""
        e1 = add_evidence(self.led, "evt_t", "exchange", "年报披露持股", "C2")
        e2 = add_evidence(self.led, "evt_t", "gsxt", "工商变更已退出", "C2")
        prov = ScriptedOpinions([opinion("evt_t:C2", [
            ("evt_t:E1", "支持", "年报披露持股", ""),
            ("evt_t:E2", "矛盾", "工商变更已退出", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.CONTRADICTED)

    def test_temporal_flip_cause(self):
        """时效类 claim 的翻转为时效翻转。"""
        e1 = add_evidence(self.led, "evt_t", "exchange", "已发布公告", "C3")
        prov = ScriptedOpinions([opinion("evt_t:C3", [
            ("evt_t:E1", "矛盾", "已发布公告", "")], verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C3", 1)
        self.assertEqual(self.led.transitions("evt_t")[0]["cause"], "时效翻转")


class TestBallotChecks(unittest.TestCase):
    """(c) 验票协议:防幻觉 id / 引文回写 / 覆盖完整 / 失败维持原态。"""

    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _engine(self, provider):
        return VerdictEngine(self.led, AUTH, provider, retry=1)

    def test_fabricated_evidence_id_rejected_then_keep_status(self):
        add_evidence(self.led, "evt_t", "zxgk", "真实证据内容", "C1")
        prov = ScriptedOpinions([
            opinion("evt_t:C1", [("evt_t:E99", "支持", "x", "")]),   # 幻觉 id
            opinion("evt_t:C1", [("evt_t:E99", "支持", "x", "")]),   # 重试仍坏
        ])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertTrue(result.skipped)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)

    def test_quote_must_be_contiguous_excerpt(self):
        """quote 非原文连续片段 -> 验票拒绝;修正后通过。"""
        add_evidence(self.led, "evt_t", "gsxt", "登记状态为存续且无异常", "C1")
        prov = ScriptedOpinions([
            opinion("evt_t:C1", [("evt_t:E1", "支持", "存续且无异常呀(改写)", "")]),
            opinion("evt_t:C1", [("evt_t:E1", "支持", "存续且无异常", "")],
                    verdict=ClaimStatus.VERIFIED),
        ])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertFalse(result.skipped)

    def test_incomplete_coverage_rejected(self):
        """意见必须覆盖卷宗内全部证据(漏判 = 无效票)。"""
        add_evidence(self.led, "evt_t", "gsxt", "证据甲", "C1")
        add_evidence(self.led, "evt_t", "wenshu", "证据乙", "C1")
        prov = ScriptedOpinions([
            opinion("evt_t:C1", [("evt_t:E1", "支持", "证据甲", "")]),  # 漏 E2
            opinion("evt_t:C1", [("evt_t:E1", "支持", "证据甲", ""),
                                 ("evt_t:E2", "支持", "证据乙", "")],
                    verdict=ClaimStatus.VERIFIED),
        ])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_no_evidence_keeps_pending(self):
        prov = ScriptedOpinions([])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)
        self.assertIn("无证据", result.note)


class TestSingleSourcePredicateGate(unittest.TestCase):
    """单源例外谓词门(发现四,2026-10-16):含信源条件谓词的 claim 不适用
    权威登记源单源例外,须走独立根>=2 慢路;无谓词登记式照常。"""

    BLOCKLIST = ["独立于", "独立信源", "为主", "中位", "基线", "趋势",
                 "相比", "既定", "延续性", "不存在", "未曾"]

    def setUp(self):
        self.led = make_ledger()
        self.led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        bp = AnalysisBlueprint(
            claims=[
                # 登记式(无谓词):单源例外照常适用
                ClaimDraft(id="C1", text="B公司在公示系统登记状态为存续",
                           category=ClaimCategory.FACT, section_index=2,
                           priority=5, entity_names=["B公司"]),
                # 比较谓词:单源例外应被拦
                ClaimDraft(id="C2", text="B公司拖欠工资的金额与既往相比显著偏高",
                           category=ClaimCategory.FACT, section_index=2,
                           priority=5, entity_names=["B公司"]),
            ],
            hypotheses=[HypothesisSeed(id="H0", label="暴雷", claim_ids=["C1"])],
            gaps=[])
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)

    def _engine(self, provider, blocklist=None):
        return VerdictEngine(self.led, AUTH, provider, retry=2,
                             ss_blocklist=(self.BLOCKLIST if blocklist is None
                                           else blocklist))

    def test_registration_claim_still_fires_exception(self):
        """c7 型回归:无谓词登记式 claim,单权威根照常翻正(梯子没坏)。"""
        add_evidence(self.led, "evt_t", "gsxt", "登记状态存续", "C1")
        prov = ScriptedOpinions([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "登记状态存续", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertIn("单源例外", result.note)

    def test_predicate_claim_blocks_single_source(self):
        """c13/c8 型:含比较谓词的 claim,单权威根不再翻正。"""
        add_evidence(self.led, "evt_t", "gsxt", "工资流水记录", "C2")
        prov = ScriptedOpinions([opinion("evt_t:C2", [
            ("evt_t:E1", "支持", "工资流水记录", "")],
            verdict=ClaimStatus.PENDING)])
        result = self._engine(prov).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)
        self.assertIn("单源例外不适用", result.note)

    def test_predicate_claim_verifies_with_two_roots(self):
        """慢路通:被谓词拦的 claim,两个独立根照样翻正。"""
        add_evidence(self.led, "evt_t", "gsxt", "工资流水记录", "C2")
        add_evidence(self.led, "evt_t", "wenshu", "判决书载明欠薪", "C2")
        prov = ScriptedOpinions([opinion("evt_t:C2", [
            ("evt_t:E1", "支持", "工资流水记录", ""),
            ("evt_t:E2", "支持", "判决书载明欠薪", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)
        self.assertIn(">=2", result.note)

    def test_empty_blocklist_keeps_old_behavior(self):
        """向后兼容:清单为空=现行为(谓词不拦,旧配置零迁移)。"""
        add_evidence(self.led, "evt_t", "gsxt", "工资流水记录", "C2")
        prov = ScriptedOpinions([opinion("evt_t:C2", [
            ("evt_t:E1", "支持", "工资流水记录", "")],
            verdict=ClaimStatus.VERIFIED)])
        result = self._engine(prov, blocklist=[]).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_domain_config_exposes_blocklist(self):
        """配置贯通:data/config 的负清单能到达引擎(发现四全链)。"""
        self.assertTrue(make_domain().ss_exception_blocklist)


class TestPartialDerive(unittest.TestCase):
    """部分支撑派生(发现四第二道,拆 claim 不拆态):复合 claim 部分被撑时,
    模型提案收窄断言,代码闸门全过才派生子 claim(C1R 模式,原条不动)。"""

    def setUp(self):
        self.led = make_ledger()
        self.led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        bp = AnalysisBlueprint(
            claims=[
                ClaimDraft(id="C2", text="B公司拖欠工资的金额与既往相比显著偏高",
                           category=ClaimCategory.FACT, section_index=2,
                           priority=5, entity_names=["B公司"]),
            ],
            hypotheses=[HypothesisSeed(id="H0", label="暴雷", claim_ids=["C2"])],
            gaps=[])
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)

    def _opinion(self, subclaim=None, direction="支持"):
        return Opinion(
            claim_id="evt_t:C2",
            evidence_judgments=[EvidenceJudgment(
                evidence_id="evt_t:E1", direction=Direction(direction),
                quote="工资流水记录", reason="")],
            verdict=ClaimStatus.PENDING,
            subclaim=subclaim)

    def _engine(self, prov):
        return VerdictEngine(self.led, AUTH, prov, retry=2,
                             ss_blocklist=["相比"])

    def test_partial_support_derives_child_claim(self):
        """提案合法 -> 派生子 claim(收窄+挂引用证据),父条原态不动。"""
        add_evidence(self.led, "evt_t", "some_web", "工资流水记录", "C2")
        sub = SubclaimProposal(text="B公司拖欠工资",
                               evidence_ids=["evt_t:E1"])
        result = self._engine(ScriptedOpinions(
            [self._opinion(sub)])).verify("evt_t:C2", 1)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)
        child = self.led.claim_row("evt_t:C2a")
        self.assertIsNotNone(child)
        self.assertEqual(child["status"], "待证实")
        self.assertEqual(child["parent_claim_id"], "evt_t:C2")
        self.assertEqual(child["text"], "B公司拖欠工资")
        self.assertEqual(self.led.claim_row("evt_t:C2")["status"], "待证实")
        linked = self.led.conn.execute(
            "SELECT evidence_id FROM claim_evidence WHERE claim_id=?",
            ("evt_t:C2a",)).fetchall()
        self.assertEqual([r[0] for r in linked], ["evt_t:E1"])
        payloads = [json.loads(r[0]) for r in self.led.conn.execute(
            "SELECT payload_json FROM decision_log "
            "WHERE event_id='evt_t' AND kind='其他'").fetchall()]
        self.assertTrue(any("partial_derive" in p for p in payloads))

    def test_atomic_parent_blocks_derivation(self):
        """父条无信源条件谓词(原子条) -> 不派生,审计记原因。"""
        add_evidence(self.led, "evt_t", "some_web", "工资流水记录", "C2")
        sub = SubclaimProposal(text="B公司拖欠工资",
                               evidence_ids=["evt_t:E1"])
        eng = VerdictEngine(self.led, AUTH, ScriptedOpinions(
            [self._opinion(sub)]), retry=2, ss_blocklist=[])
        eng.verify("evt_t:C2", 1)
        self.assertIsNone(self.led.claim_row("evt_t:C2a"))

    def test_non_support_citation_blocks_derivation(self):
        """引用了非"支持"方向的证据(幻觉引用) -> 不派生。"""
        add_evidence(self.led, "evt_t", "some_web", "工资流水记录", "C2")
        sub = SubclaimProposal(text="B公司拖欠工资",
                               evidence_ids=["evt_t:E9"])   # 不存在的证据
        self._engine(ScriptedOpinions(
            [self._opinion(sub)])).verify("evt_t:C2", 1)
        self.assertIsNone(self.led.claim_row("evt_t:C2a"))

    def test_unnarrowed_text_blocks_derivation(self):
        """提案未收窄(同父/更长) -> 不派生。"""
        add_evidence(self.led, "evt_t", "some_web", "工资流水记录", "C2")
        sub = SubclaimProposal(
            text="B公司拖欠工资的金额与既往相比显著偏高且影响恶劣",
            evidence_ids=["evt_t:E1"])
        self._engine(ScriptedOpinions(
            [self._opinion(sub)])).verify("evt_t:C2", 1)
        self.assertIsNone(self.led.claim_row("evt_t:C2a"))

    def test_no_subclaim_is_noop(self):
        """无提案(旧意见形态) -> 零影响(回放零迁移)。"""
        add_evidence(self.led, "evt_t", "some_web", "工资流水记录", "C2")
        self._engine(ScriptedOpinions(
            [self._opinion(None)])).verify("evt_t:C2", 1)
        self.assertIsNone(self.led.claim_row("evt_t:C2a"))


if __name__ == "__main__":
    unittest.main()
