# -*- coding: utf-8 -*-
"""Delta 呈现层(渲染批次 2026-10-27):修正条件句机械模板+头部挂载+锚接线。

覆盖:
- render_md 纯函数:总览行/逐条修正条件句(旧态→新态+成因中文+依据短标+
  轮次+来源)/新增条(派生谱系)/触及假设行/零变化形态/证据缺省行;
- render_brief_line:有修正/零变化两形态;
- 挂载(_deliver):首跑无锚=三产物零痕迹且锚已落盘;次跑零变化形态出节;
  锚后状态变化出逐条修正条件句;html 修正卡;None=零痕迹。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (  # noqa: E402
    add_evidence, install_simple, make_ledger, make_domain)
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.delta_present import render_brief_line, render_md  # noqa: E402
from sense2act.delta_report import (  # noqa: E402
    ANCHOR_FILE, compute_delta, read_anchor, write_anchor)
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402

# 时刻轴(与 test_delta_report 同款):锚 2027-01-01 < 锚后迁移 2030-01-xx
ANCHOR = "2027-01-01T00:00:00+00:00"
POST_FLIP = "2030-01-01T00:00:00+00:00"


def _set_ts(led, claim_id, round_no, ts):
    led.conn.execute(
        "UPDATE claim_transitions SET ts=? WHERE claim_id=? AND round=?",
        (ts, claim_id, round_no))
    led.conn.commit()


def _builder(led, tmp):
    return ReportBuilder(led, make_domain(),
                         InvestigationConfig(ess_enabled=False),
                         writer=None, runs_dir=tmp)


def _summary(event_id="evt_t"):
    return {"event_id": event_id, "signal_type": "", "stop_reason": "x",
            "status_counts": {}, "standings": [], "relations": [],
            "transitions": [], "recommendations": [], "costs": {}}


def _deliver(builder, led, tmp, event_id="evt_t"):
    """一次结算循环:delta 计算→三产物交付→锚落盘(照 finalize 编排)。"""
    out_dir = Path(tmp) / event_id
    delta = builder._delta_rows(event_id, out_dir)
    builder._deliver(out_dir, event_id, _summary(event_id), "t",
                     ess_result=None, delta=delta)
    builder._delta_anchor(out_dir)
    return out_dir, delta


def _flip_scenario(led):
    """C1 锚前翻正、锚后翻转(已证实→矛盾,严重度最高;平台 ccgp)。"""
    add_evidence(led, "evt_t", "ccgp.gov.cn", "公告正文", "C1", platform="ccgp")
    led.update_claim_status("evt_t:C1", "已证实", None, 1)
    _set_ts(led, "evt_t:C1", 1, "2025-12-01T00:00:00+00:00")
    led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 2, "evt_t:E1")
    _set_ts(led, "evt_t:C1", 2, POST_FLIP)


class TestRenderMd(unittest.TestCase):
    def _scenario_delta(self):
        led = make_ledger()
        install_simple(led)
        _flip_scenario(led)
        return compute_delta(led, "evt_t", ANCHOR)

    def test_修正条件句模板(self):
        out = render_md(self._scenario_delta())
        self.assertIn("### 较上次报告的修正(上次结算 2027-01-01 00:00 UTC)",
                      out)
        self.assertIn("自上次结算:修正 1 条 / 新增 0 条 / 不变 2 条。", out)
        # 条件句:旧态→新态(成因中文,依据短标,轮次;来源平台;条目类别词)
        self.assertIn("- 「B公司拖欠工资」(条目 C1):已证实 → 矛盾"
                      "(新矛盾证据,依据 证据E1,轮次2;来源 ccgp)", out)
        self.assertIn("触及假设:H0(关联条目 C1)(假设名见记分板)。", out)

    def test_零变化形态(self):
        led = make_ledger()
        install_simple(led)
        out = render_md(compute_delta(led, "evt_t", ANCHOR))
        self.assertIn("自上次结算:修正 0 条 / 新增 0 条 / 不变 3 条"
                      "——无状态变化。", out)
        self.assertNotIn("- 「", out)

    def test_新增条带派生谱系(self):
        led = make_ledger()
        install_simple(led)
        write_anchor(tempfile.mkdtemp(), ANCHOR)      # no-op 保 import 对称
        from sense2act.models.base import ClaimCategory
        led.add_claim("evt_t", {
            "id": "C1", "text": "B公司拖欠工资且涉及仲裁",
            "category": ClaimCategory.FACT.value, "section_index": 2}, 3)
        led.conn.execute(
            "UPDATE claims SET created_at=? WHERE claim_id='evt_t:C1d'",
            (POST_FLIP,))
        led.conn.commit()
        out = render_md(compute_delta(led, "evt_t", ANCHOR))
        self.assertIn("- 新增条目 C1d「B公司拖欠工资且涉及仲裁」:待证实"
                      ",派生自 条目C1", out)

    def test_证据缺省只写成因(self):
        out = render_md({
            "anchor_ts": ANCHOR, "total": 1, "modified": [{
                "claim_id": "evt_t:C1", "draft_id": "C1", "text": "甲",
                "old_status": "待证实", "new_status": "已证实",
                "cause": "状态刷新", "evidence_id": "", "round": 2,
                "n_transitions": 1, "evidence": None}],
            "added": [], "unchanged_count": 0, "hypotheses_touched": {}})
        self.assertIn("(状态刷新)", out)
        self.assertNotIn("依据", out)


class TestBriefLine(unittest.TestCase):
    def test_两形态(self):
        self.assertEqual(render_brief_line({"modified": [1, 2], "added": [3]}),
                         "较上次:修正 2 条 / 新增 1 条(细节见 report.md)。")
        self.assertEqual(render_brief_line({"modified": [], "added": []}),
                         "较上次结算:无状态变化。")


class TestMountDelta(unittest.TestCase):
    """挂载:首跑无锚零痕迹+锚落盘;次跑出节;锚后变化出条件句。"""

    def test_首跑无锚零痕迹_锚已落盘(self):
        led = make_ledger()
        install_simple(led)
        tmp = Path(tempfile.mkdtemp())
        out_dir, delta = _deliver(_builder(led, tmp), led, tmp)
        self.assertIsNone(delta)                      # 首跑无锚
        self.assertTrue((out_dir / ANCHOR_FILE).exists())   # 锚已写
        for name in ("report.md", "brief.md", "report.html"):
            self.assertNotIn("较上次", (out_dir / name).read_text(
                encoding="utf-8"), name)

    def test_次跑零变化形态出节(self):
        led = make_ledger()
        install_simple(led)
        tmp = Path(tempfile.mkdtemp())
        _deliver(_builder(led, tmp), led, tmp)        # 首跑:写锚
        out_dir, delta = _deliver(_builder(led, tmp), led, tmp)   # 次跑
        self.assertIsNotNone(delta)
        md = (out_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("### 较上次报告的修正", md)
        self.assertIn("无状态变化", md)
        brief = (out_dir / "brief.md").read_text(encoding="utf-8")
        self.assertIn("- 较上次结算:无状态变化。", brief)
        html = (out_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn("较上次报告的修正(上次结算", html)

    def test_锚后变化出逐条条件句(self):
        led = make_ledger()
        install_simple(led)
        _flip_scenario(led)
        tmp = Path(tempfile.mkdtemp())
        (tmp / "evt_t").mkdir(parents=True)
        write_anchor(tmp / "evt_t", ANCHOR)           # 手工置锚(在翻转前)
        builder = _builder(led, tmp)
        out_dir = tmp / "evt_t"
        delta = builder._delta_rows("evt_t", out_dir)
        self.assertEqual(len(delta["modified"]), 1)
        builder._deliver(out_dir, "evt_t", _summary(), "t",
                         ess_result=None, delta=delta)
        md = (out_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("(条目 C1):已证实 → 矛盾", md)
        self.assertIn("新矛盾证据,依据 证据E1,轮次2;来源 ccgp", md)
        brief = (out_dir / "brief.md").read_text(encoding="utf-8")
        self.assertIn("较上次:修正 1 条 / 新增 0 条(细节见 report.md)。",
                      brief)
        html = (out_dir / "report.html").read_text(encoding="utf-8")
        self.assertIn("已证实 → 矛盾", html)

    def test_锚roundtrip_读侧容错(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_anchor(tmp))
            write_anchor(tmp, ANCHOR)
            self.assertEqual(read_anchor(tmp), ANCHOR)

    def test_锚写入秒级粒度(self):
        # 台账 _now()=timespec=seconds;锚若带微秒,秒级 transitions 字符串
        # 比较恒 ≤ 锚('+'<'.'),锚后同秒修正被漏(demo 实证)——锚必须秒级
        led = make_ledger()
        install_simple(led)
        with tempfile.TemporaryDirectory() as tmp:
            _builder(led, tmp)._delta_anchor(Path(tmp) / "evt_t")
            anchor = read_anchor(Path(tmp) / "evt_t")
            self.assertIsNotNone(anchor)
            self.assertNotIn(".", anchor.split("T")[-1])   # 无微秒段


if __name__ == "__main__":
    unittest.main()
