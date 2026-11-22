# -*- coding: utf-8 -*-
"""K5 呈现挂载测试(渲染批次 2026-10-27)。

覆盖:
- 门控链(--kb on ∧ kb.db 存在 ∧ 有规则卡 ∧ 有自我卡)任一不满足=零痕迹
  (kb off/kb 缺/无规则卡/无自我卡→_kb_rows None,产物字节不变);
- 成功路径:report.md/brief.md 双 append(用户拍板)+kb_check.json 旁车;
  表格三态徽章与 kb_check 数据层口径一致(同源 _STATUS_BADGE);
- 结论句三行机械模板:合计/红线硬伤逐条点名(r004 载明不符)/待证实 OQ 指引;
- 敏感过滤:自我卡 attributes.sensitivity=internal→依据列显式值不出境
  (只出定位符;数据层 rows 与 kb_check.json 不动——过滤只在呈现);
- 降级:kb.db 损坏→"资质核对(K5)未执行"注记不炸交付;
- html K5 节:同源文本过白名单转换;None=零痕迹。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import make_domain  # noqa: E402
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.kb_present import render_appendix  # noqa: E402
from sense2act.kb_store import KbStore  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402
from test_kb_check import _Derived  # noqa: E402


def _summary(event_id="evt_t"):
    return {"event_id": event_id, "signal_type": "", "stop_reason": "x",
            "status_counts": {}, "standings": [], "relations": [],
            "transitions": [], "recommendations": [], "costs": {}}


def _builder(led, tmp, kb_path, kb_on=True):
    return ReportBuilder(
        led, make_domain(),
        InvestigationConfig(ess_enabled=False, kb_enabled=kb_on,
                            kb_path=str(kb_path)),
        writer=None, runs_dir=tmp)


def _deliver(builder, tmp, k5, event_id="evt_t"):
    """完整落盘序列(照 finalize 编排):三产物渲染+K5 呈现落盘。"""
    out_dir = Path(tmp) / event_id
    builder._deliver(out_dir, event_id, _summary(event_id), "t",
                     ess_result=None, k5=k5)
    builder._kb_finalize(out_dir, k5)
    return out_dir


def _set_internal(kb_path):
    """自我卡 attributes 注入 sensitivity=internal(kb_import 文档上传同形态)。"""
    store = KbStore.open(kb_path)
    try:
        row = store.conn.execute(
            "SELECT attributes_json FROM entity WHERE entity_id='ent_self'"
        ).fetchone()
        attrs = json.loads(row["attributes_json"])
        attrs["sensitivity"] = "internal"
        store.conn.execute(
            "UPDATE entity SET attributes_json=? WHERE entity_id='ent_self'",
            (json.dumps(attrs, ensure_ascii=False, sort_keys=True),))
        store.conn.commit()
    finally:
        store.conn.close()


class TestGating(unittest.TestCase):
    """门控链:任一不满足→None=零痕迹(用户拍板语义)。"""

    def test_kb_off_零痕迹(self):
        d = _Derived()
        kb = d.derive()
        tmp = Path(tempfile.mkdtemp())
        b = _builder(d.led, tmp, kb, kb_on=False)
        self.assertIsNone(b._kb_rows("evt_t"))
        out_dir = _deliver(b, tmp, None)          # None→渲染与落盘皆零痕迹
        self.assertEqual((out_dir / "report.md").read_text("utf-8")
                         .count("资质核对"), 0)

    def test_kb缺失_零痕迹(self):
        d = _Derived()
        tmp = Path(tempfile.mkdtemp())
        b = _builder(d.led, tmp, tmp / "nope.db")
        self.assertIsNone(b._kb_rows("evt_t"))

    def test_无规则卡_零痕迹(self):
        d = _Derived()
        kb = d.derive()
        store = KbStore.open(kb)
        try:
            store.conn.execute("DELETE FROM rule_card")
            store.conn.commit()
        finally:
            store.conn.close()
        tmp = Path(tempfile.mkdtemp())
        self.assertIsNone(_builder(d.led, tmp, kb)._kb_rows("evt_t"))

    def test_无自我卡_零痕迹(self):
        d = _Derived()
        kb = d.derive()
        store = KbStore.open(kb)
        try:
            store.conn.execute("DELETE FROM entity WHERE entity_id='ent_self'")
            store.conn.commit()
        finally:
            store.conn.close()
        tmp = Path(tempfile.mkdtemp())
        self.assertIsNone(_builder(d.led, tmp, kb)._kb_rows("evt_t"))


class TestMountAppendix(unittest.TestCase):
    """成功路径:双 append+kb_check.json 旁车+结论句模板+红线硬伤。"""

    def setUp(self):
        self.d = _Derived()
        self.kb = self.d.derive()
        self.tmp = Path(tempfile.mkdtemp())

    def test_双挂与旁车(self):
        b = _builder(self.d.led, self.tmp, self.kb)
        k5 = b._kb_rows("evt_t")
        self.assertIsNotNone(k5)
        self.assertEqual(len(k5["rows"]), 5)          # r001-r005
        self.assertFalse(k5["internal"])
        out_dir = _deliver(b, self.tmp, k5)
        self.assertTrue((out_dir / "kb_check.json").exists())
        for name in ("report.md", "brief.md"):
            text = (out_dir / name).read_text("utf-8")
            self.assertIn("### 附录·资质核对(K5 规则卡比对)", text)
            self.assertIn("| r001 | 准入 |", text)
            self.assertIn("登记信息:注册资本=800万元", text)   # 依据列中文化
            self.assertIn("✓ 已证实", text)

    def test_结论句三行与红线硬伤(self):
        b = _builder(self.d.led, self.tmp, self.kb)
        out = render_appendix(b._kb_rows("evt_t")["rows"])
        # 合计行(r001✓ r003✓;r002✗ r004✗;r005?)
        self.assertIn("> 资质核对结论:5 项要求 = 已证实 2 / 矛盾 2 "
                      "/ 待证实 1。", out)
        # 红线硬伤逐条点名(r004 holds 二类许可证,显式记录不含→矛盾)
        self.assertIn("⚠ 红线硬伤:「", out)
        self.assertIn("载明不符(条目 r004)", out)
        self.assertIn("红线未坐实按硬伤对待", out)
        # 待证实 OQ 指引(r005 exists 未载明)
        self.assertIn("待证实 1 项 = 查不到,不伪装满足,建议列入"
                      " Open Questions:「类似项目经验」(条目 r005)。", out)

    def test_红线全坐实改写_无红线省略(self):
        rows = [
            {"rule_id": "rul_a", "rule_name": "R", "rid": "r1", "kind": "红线",
             "field": "资质", "predicate": "exists", "value": "有",
             "requirement": "持证", "subject_id": "ent_self",
             "subject_name": "我方", "status": "已证实",
             "basis": "attributes:资质=有", "reason": ""},
            {"rule_id": "rul_a", "rule_name": "R", "rid": "r2", "kind": "准入",
             "field": "注册资本", "predicate": "gte", "value": "500万元",
             "requirement": "", "subject_id": "ent_self",
             "subject_name": "我方", "status": "待证实",
             "basis": "", "reason": ""},
        ]
        out = render_appendix(rows)
        self.assertIn("红线项 1 条全部坐实。", out)
        self.assertNotIn("红线硬伤", out)
        rows_nored = [r for r in rows if r["rid"] != "r1"]
        self.assertNotIn("红线", render_appendix(rows_nored))   # 无红线项=行省略


class TestSensitiveFilter(unittest.TestCase):
    """敏感过滤:internal 自我卡→依据列只出定位符;数据层不动。"""

    def test_internal值不出境(self):
        d = _Derived()
        kb = d.derive()
        _set_internal(kb)
        tmp = Path(tempfile.mkdtemp())
        b = _builder(d.led, tmp, kb)
        k5 = b._kb_rows("evt_t")
        self.assertTrue(k5["internal"])
        out = _deliver(b, tmp, k5)
        text = (out / "report.md").read_text("utf-8")
        # 显式值 800万元 不出现在呈现文本;定位符照旧(中文措辞)
        self.assertNotIn("800万元", text)
        self.assertIn("登记信息:注册资本 |", text)
        # kb_check.json(数据旁车)不受呈现过滤影响
        snap = json.loads((out / "kb_check.json").read_text("utf-8"))
        r1 = next(r for r in snap if r["rid"] == "r001")
        self.assertEqual(r1["basis"], "attributes:注册资本=800万元")

    def test_public照常出境(self):
        d = _Derived()
        kb = d.derive()
        tmp = Path(tempfile.mkdtemp())
        out = render_appendix(_builder(d.led, tmp, kb)._kb_rows("evt_t")["rows"])
        self.assertIn("登记信息:注册资本=800万元", out)


class TestDegraded(unittest.TestCase):
    """降级:kb.db 损坏→注记不炸交付(照 ESS"本报告未评"先例)。"""

    def test_坏库注记(self):
        d = _Derived()
        tmp = Path(tempfile.mkdtemp())
        bad = tmp / "bad.db"
        bad.write_text("not a sqlite db", encoding="utf-8")
        b = _builder(d.led, tmp, bad)
        k5 = b._kb_rows("evt_t")
        self.assertIsNotNone(k5)
        self.assertIn("error", k5)
        out_dir = _deliver(b, tmp, k5)
        text = (out_dir / "report.md").read_text("utf-8")
        self.assertIn("资质核对(K5)未执行", text)
        self.assertFalse((out_dir / "kb_check.json").exists())


class TestHtmlSection(unittest.TestCase):
    """html K5 节:同源文本过白名单转换;None=零痕迹。"""

    def test_html含k5节与零痕迹(self):
        d = _Derived()
        kb = d.derive()
        tmp = Path(tempfile.mkdtemp())
        b = _builder(d.led, tmp, kb)
        k5 = b._kb_rows("evt_t")
        out_dir = _deliver(b, tmp, k5)
        html = (out_dir / "report.html").read_text("utf-8")
        self.assertIn("资质核对(K5 规则卡比对)", html)
        self.assertIn("✓ 已证实", html)
        out_none = _deliver(b, Path(tempfile.mkdtemp()), None)
        self.assertNotIn("资质核对(K5",
                         (out_none / "report.html").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
