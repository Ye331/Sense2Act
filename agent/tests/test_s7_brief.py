"""S7 报告双形态(2026-10-10):brief.md 企业简报 + 主导假设快照 + 内部待办分流。

纪律:双形态=交付层多视图(六节固定结构与架构决策不动);简报零 LLM、
零台账写入;复述型已证实(corpus_bound ∧ 纯 signal_layer 根)压缩进卡片、
不进"已核实事实"正文;运营者事项按确定性标记词分流进「内部待办」。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_evidence, install_simple, make_domain, make_ledger  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.envelope import (  # noqa: E402
    InvestigationEnvelope, RumorSignal, TenderSignal)
from sense2act.models.intel import Recommendation  # noqa: E402
from sense2act.stages.d_hooks import TemplateSectionWriter  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402


def _tender_envelope() -> InvestigationEnvelope:
    return InvestigationEnvelope(kind="tender", tender=TenderSignal(
        project_id="PRJ-TEST-001",
        project_name="测试市第一医院数智化系统采购项目公开招标公告",
        purchaser="测试市第一医院",
        amount_yuan=6270000.0,
        published_date="2026-10-03",
        project_summary="测试市第一医院数智化系统采购项目公开招标公告;正文缺失。",
        source_url="https://example.org/announcement/R-test",
        content_source_note="未抓取官方正文",
    ))


class _StubProvider:
    def __init__(self, items):
        self._items = items

    def recommend(self, context):
        return self._items


class TestBrief(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        self.domain = make_domain()
        install_simple(self.led)
        self.tmp = tempfile.mkdtemp()

    def _builder(self, rec_items=None):
        builder = ReportBuilder(
            self.led, self.domain, InvestigationConfig(),
            TemplateSectionWriter(), provider=None, runs_dir=self.tmp)
        if rec_items is not None:
            builder._provider = _StubProvider(rec_items)
        return builder

    def _finalize(self, envelope=None, rec_items=None):
        return self._builder(rec_items=rec_items).finalize(
            "evt_t", 2, "轮数上限", costs={"llm_calls": 5},
            envelope_label="测试事件", signal_type="商机", envelope=envelope)

    # ------------------------------------------------------------- 工具

    def _add_verified_claim(self, draft_id: str, text: str, *, corpus_bound=0,
                            root: str = "a.com"):
        """手装一条 claim(+证据+判决行),绕过 S6(渲染层测试只关心台账投影)。"""
        self.led.add_claim("evt_t", {
            "id": draft_id, "text": text, "category": ClaimCategory.FACT,
            "section_index": 2, "priority": 4, "entity_names": [],
        }, 1)
        eid = add_evidence(self.led, "evt_t", root, "证据正文", draft_id)
        if corpus_bound:
            self.led.conn.execute(
                "UPDATE claims SET corpus_bound=1 WHERE draft_id=?", (draft_id,))
            self.led.conn.commit()
        self.led.update_claim_status(f"evt_t:{draft_id}", "已证实", None, 1)
        self.led.log("evt_t", 1, "判决", {
            "claim_id": f"evt_t:{draft_id}", "final": "已证实",
            "support_roots": [root], "oppose_roots": [],
            "overridden": False, "skipped": False,
            "note": "测试注入"})
        return eid

    # ------------------------------------------------------------- 用例

    def test_brief_artifact_and_card(self):
        """brief.md 交付;卡片字段带状态角标;缺失字段如实标"信封未提供"。"""
        self._add_verified_claim(
            "C90", "信封载明采购方为测试市第一医院。")
        self._finalize(envelope=_tender_envelope())
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("# 企业情报简报", brief)
        for head in ("一句话研判", "商机卡片", "红旗与信号", "已核实事实与研判状态",
                     "建议下一步", "Open Questions", "引用证据"):
            self.assertIn(head, brief)
        # 已证实字段:值命中已证实 claim -> 带该 claim 引用
        self.assertRegex(brief, r"\| 采购人 \| 测试市第一医院 \| 已证实 \[E\d+\] \|")
        # 信封载明字段:无 claim 提及 -> 锚 [E1]
        self.assertIn("| 公告发布日 | 2026-10-03 | 信封载明 [E1] |", brief)
        # 金额格式化 + 无 claim 提及 -> 信封载明
        self.assertIn("| 预算金额 | 627 万元 | 信封载明 [E1] |", brief)
        # 缺失字段如实标注,不编造
        self.assertIn("| 投标截止时间 | — | 信封未提供 |", brief)
        self.assertIn("| 获取招标文件方式 | — | 信封未提供 |", brief)
        self.assertIn("| 代理机构 / 联系人 | — | 信封未提供 |", brief)
        # 正文质量红旗来自 content_source_note
        self.assertIn("正文质量", brief)
        self.assertIn("未抓取官方正文", brief)

    def test_brief_world_verified_and_restatement_compression(self):
        """世界断言(非复述+外部根)进"已核实事实";复述型(corpus_bound ∧
        signal_layer 根)压缩不进正文——篇幅重分配规则。"""
        self._add_verified_claim(
            "C90", "测试市第一医院与对照项目建设存在延续关系。",
            corpus_bound=0, root="a.com")
        self._add_verified_claim(
            "C91", "信封载明金额异常得分为 0.8。", corpus_bound=1, root="signal_layer")
        self._finalize(envelope=_tender_envelope())
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        world_head = brief.index("## 已核实事实与研判状态")
        world_tail = brief.index("## 建议下一步")
        world = brief[world_head:world_tail]
        self.assertIn("C90", world)                      # 世界断言逐条保留
        self.assertIn("独立根:a.com", world)
        self.assertNotIn("C91", world)                   # 复述型已压缩
        self.assertNotIn("signal_layer", world)

    def test_brief_receives_envelope_and_renders(self):
        """finalize 未传信封(旧调用方/老测试)时简报降级不崩。"""
        summary = self._finalize(envelope=None)
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("(信封上下文不可用,仅台账视图)", brief)
        self.assertEqual(summary["status_counts"]["待证实"], 3)

    def test_brief_rumor_variant(self):
        """rumor 信号渲染主体卡片;线索行附谱系根折算读数。"""
        env = InvestigationEnvelope(kind="rumor", rumor=RumorSignal(
            subject_name="B公司", summary="B公司欠薪传闻", source="weibo.com",
            signal_time="2026-09-22", watch_focus="供应链风险", clues=[]))
        self._finalize(envelope=env)
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("## 主体卡片", brief)
        self.assertIn("| 监测主体 | B公司 |", brief)
        self.assertIn("无线索卡", brief)

    def test_internal_recommendation_split(self):
        """建议受众分流:命中内部标记词的进「内部待办」,不进企业简报正文。"""
        recs = [
            Recommendation(
                action="优先补齐该采购单位真实历史采购基线:替换 8 条模拟样本",
                rationale="现有基线含模拟样本,不代表真实历史证据充分。",
                basis_claim_ids=["C2"], hypothesis_label="H0"),
            Recommendation(
                action="获取公告原文并核验金额口径",
                rationale="正文缺失,采购内容与金额口径无法核实。",
                basis_claim_ids=["C3"], hypothesis_label=""),
        ]
        self._finalize(envelope=_tender_envelope(), rec_items=recs)
        brief = (Path(self.tmp) / "evt_t" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("获取公告原文并核验金额口径", brief)
        self.assertNotIn("替换 8 条模拟样本", brief)
        self.assertIn("1 条系统内部事项", brief)
        md = (Path(self.tmp) / "evt_t" / "report.md").read_text(encoding="utf-8")
        self.assertIn("## 内部待办(运营者事项,企业简报不含)", md)
        self.assertIn("替换 8 条模拟样本", md)

    def test_dominant_snapshot_line(self):
        """report.md 头部主导假设快照行(模板生成,修统计滞后 artifact)。"""
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        summary = self._finalize(envelope=_tender_envelope())
        md = (Path(self.tmp) / "evt_t" / "report.md").read_text(encoding="utf-8")
        self.assertIn("主导假设(交付时快照)", md)
        doms = [s["label"] for s in summary["standings"] if s.get("dominant")]
        if doms:
            self.assertIn(doms[0], md.split("主导假设(交付时快照)")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
