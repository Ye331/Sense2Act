"""S5 记账(派生校验/回溯挂账/调查圈)+ D 钩子 + S7 finalize。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import ALIASES, add_evidence, install_simple, make_domain, make_ledger  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.llm import ReplayLLM  # noqa: E402
from sense2act.models.base import ClaimStatus  # noqa: E402
from sense2act.stages.d_hooks import DraftHooks, TemplateSectionWriter  # noqa: E402
from sense2act.stages.s5_ledger import LLMDeriver, LedgerWriter  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402


class TestS5Derive(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)

    def _writer(self, responses):
        return LedgerWriter(self.led, self.domain,
                            LLMDeriver(ReplayLLM(responses),
                                       self.domain.relation_vocab))

    def test_derive_hard_validation(self):
        """派生硬校验:id 冲突跳过/关系类须命中词表且双实体/坏类目跳过;合法项入账。"""
        e = add_evidence(self.led, "evt_t", "weibo.com", "矩阵账号行为", "C1")
        writer = self._writer([{"stage": "s5_derive", "response": {
            "derivedClaims": [
                {"id": "C1", "text": "id 冲突项", "category": "事实",
                 "sectionIndex": 2},
                {"id": "C9", "text": "关系词表未命中", "category": "关系",
                 "sectionIndex": 1, "relationType": "不存在的类型",
                 "entityNames": ["甲", "乙"]},
                {"id": "C10", "text": "合法派生断言", "category": "事实",
                 "sectionIndex": 2, "priority": 4, "hypothesisLinks": ["H0"]},
            ],
            "cohorts": [{"name": "矩阵账号", "criteria": {"d": 1},
                         "members": ["产业内幕君2026"]}],
        }}])
        report = writer.commit("evt_t", 1, [])
        # 无新证据时派生不触发——先验证这一点
        self.assertEqual(report["derived_claims"], [])
        # 重新入账一条新证据触发派生
        add_evidence(self.led, "evt_t", "weibo.com", "第二条不同证据", "C1")
        report = writer.commit("evt_t", 2, [{
            "platform": "weibo", "source": "weibo.com", "title": "新",
            "content": "矩阵账号行为补充", "lineage_root": "weibo.com",
            "entity_ids": []}])
        self.assertEqual(report["derived_claims"], ["C10"])
        self.assertEqual(report["cohorts"], ["矩阵账号"])
        self.assertEqual(
            self.led.claim_row("evt_t:C10")["status"], "待证实")
        self.assertEqual(len(self.led.cohorts_of("evt_t")), 1)

    def test_associate_backlog(self):
        """轮 0 证据在 claim 落库后按实体交集补挂。"""
        item = {"platform": "weibo", "source": "weibo.com", "title": "t",
                "content": "B公司 拖欠工资爆料", "lineage_root": "weibo.com",
                "entity_ids": []}
        # 先入账(无 claim),再装蓝图,再回溯
        writer = LedgerWriter(self.led, self.domain, deriver=None)
        eid = self.led.insert_evidence("evt_t", item, 0)
        linked = writer.associate_backlog("evt_t", 0)
        self.assertEqual(linked, [])  # 此时 claim_entities 已在蓝图装库时建好
        self.led.conn.execute("DELETE FROM claim_evidence")
        self.led.conn.commit()
        linked2 = writer.associate_backlog("evt_t", 0)
        # E1 无实体 id(未过 S4),回溯以 entity_ids 列为准
        self.assertEqual(linked2, [])


class TestDHooks(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)
        self.hooks = DraftHooks(self.led, TemplateSectionWriter())

    def test_hook1_stable_means_terminal(self):
        """稳定即起草:矛盾也是终态——查清了哪怕结论是假的,立刻变现。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        report = self.hooks.run_round_end("evt_t", 1)
        self.assertIn("核心事实", report["drafted"])
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "STABLE_DRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "矛盾"})
        self.assertIn("[矛盾]", sec["draft_text"])

    def test_hook2_drift_redraft(self):
        """翻转即重写:快照漂移 -> STALE -> check/edit -> REDRAFTED,快照刷新。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        self.hooks.run_round_end("evt_t", 1)
        # 世界变化:刷新翻转
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        self.assertEqual(
            [s["title"] for s in self.led.stale_sections("evt_t")], ["核心事实"])
        report = self.hooks.run_round_end("evt_t", 2)
        self.assertIn("核心事实", report["redrafted"])
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "REDRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "已证实"})
        self.assertIn("[已证实]", sec["draft_text"])

    def test_oq_and_recommendation_excluded(self):
        """Open Questions / 行动建议节排除在钩子外(永不稳定,finalize 生成)。"""
        for cid in ("C1", "C2", "C3"):
            self.led.update_claim_status(
                f"evt_t:{cid}", "已证实", None, 1)
        titles = [s["title"] for s in self.led.stable_undrafted_sections("evt_t")]
        self.assertIn("核心事实", titles)
        self.assertIn("动态时间线与关联方", titles)
        self.assertNotIn("Open Questions", titles)
        self.assertNotIn("行动建议", titles)


class TestS7Finalize(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, deployment="report_only"):
        return ReportBuilder(
            self.led, self.domain,
            InvestigationConfig(deployment_mode=deployment),
            TemplateSectionWriter(), provider=None, runs_dir=self.tmp)

    def test_finalize_full_artifacts(self):
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        # C3 保持待证实 -> Open Questions
        summary = self._builder().finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="风险")
        out = Path(self.tmp) / "evt_t"
        for name in ("report.md", "report.html", "result.json", "audit.json"):
            self.assertTrue((out / name).exists(), name)
        md = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("### Open Questions", md)
        self.assertIn("C3", md)                     # 未决项如实呈现
        self.assertIn("判决核对证书", md)
        self.assertIn("结论变更日志", md)
        self.assertIn("假设记分板", md)
        self.assertEqual(summary["status_counts"]["待证实"], 1)
        self.assertTrue(summary["recommendations"])  # 模板兜底建议(挂依据链)

    def test_action_facing_blocks_stale(self):
        """action_facing:STALE 节在核对通过前禁止对外交付。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        DraftHooks(self.led, TemplateSectionWriter()).run_round_end("evt_t", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        with self.assertRaises(RuntimeError):
            self._builder(deployment="action_facing").finalize(
                "evt_t", 2, "停", envelope_label="x", signal_type="风险")

    def test_report_only_repairs_stale_then_asserts(self):
        """report_only:残留 STALE 节被定向重写修复,终稿核对通过。"""
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        DraftHooks(self.led, TemplateSectionWriter()).run_round_end("evt_t", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        self._builder().finalize("evt_t", 2, "停", envelope_label="x",
                                 signal_type="风险")
        sec = [s for s in self.led.get_sections("evt_t")
               if s["title"] == "核心事实"][0]
        self.assertEqual(sec["state"], "REDRAFTED")
        self.assertEqual(json.loads(sec["snapshot_json"]), {"C1": "已证实"})


if __name__ == "__main__":
    unittest.main()
