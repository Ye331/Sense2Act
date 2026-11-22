"""动态报告:时钟复查链(到期选择器/纯时钟降级/事件重开/报告版本化/演变时间轴)。

认识论护栏(锁死):纯时钟操作只降级(终态->待证实,成因=状态刷新),不得凭空
翻成矛盾/已证实;翻转终态必须挂证据(三态纪律)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_ledger, install_simple, add_evidence  # noqa: E402
from sense2act.models.envelope import InvestigationEnvelope  # noqa: E402
from sense2act.stages.s7_report import ReportBuilder  # noqa: E402


def _tender_envelope(event_id: str, project_id: str = "PRJ-X-1") -> InvestigationEnvelope:
    return InvestigationEnvelope.model_validate({
        "kind": "tender", "event_id": event_id,
        "tender": {"projectId": project_id, "projectName": f"某项目{project_id}",
                   "purchaser": "某采购人", "publishedDate": "2026-10-01"},
    })


class TestDueSelector(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _set_due(self, draft: str, days: int, category: str = "时效",
                 col: str = "valid_window_days"):
        self.led.conn.execute(
            f"UPDATE claims SET {col}=?, category=? WHERE claim_id=?",
            (days, category, f"evt_t:{draft}"))
        self.led.conn.commit()

    def test_due_by_valid_window(self):
        """时效类:末次确认+窗口 <= 今天 -> 到期(选进复查清单)。"""
        self._set_due("C1", 1)
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)  # ts=今天
        # 末次确认今天+窗口1天 -> 明天到期,今天不算
        self.assertEqual(len(self.led.claims_due_for_recheck("evt_t")), 0)
        # 把确认时间拨回 3 天前(transitions ts 手工回填)= 过窗
        self.led.conn.execute(
            "UPDATE claim_transitions SET ts='2026-01-01T00:00:00+00:00'"
            " WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        due = [r["draft_id"] for r in self.led.claims_due_for_recheck("evt_t")]
        self.assertIn("C1", due)

    def test_only_terminal_and_clocked_claims(self):
        """待证实不选(无复查语义);无窗口/周期的终态 claim 不选。"""
        self._set_due("C1", 1)
        self.led.conn.execute(
            "UPDATE claims SET status='待证实' WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        self.assertEqual(self.led.claims_due_for_recheck("evt_t"), [])
        # C2 无 valid_window/refresh_cycle 字段(时效类才给窗口)
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        due = [r["draft_id"] for r in self.led.claims_due_for_recheck("evt_t")]
        self.assertNotIn("C2", due)


class TestClockDowngrade(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_untouched_due_downgraded_state_refresh(self):
        """到期且本会话无新证据 -> 状态刷新降回待证实(护栏:只降级)。"""
        self.led.conn.execute(
            "UPDATE claims SET valid_window_days=1, category='时效'"
            " WHERE claim_id='evt_t:C1'")
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.conn.execute(
            "UPDATE claim_transitions SET ts='2026-01-01T00:00:00+00:00'"
            " WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        down = self.led.downgrade_untouched_due("evt_t", "2099-01-01", 5)
        self.assertEqual(down, ["C1"])
        row = self.led.claim_row("evt_t:C1")
        self.assertEqual(row["status"], "待证实")
        tr = self.led.transitions("evt_t")[-1]
        self.assertEqual((tr["old_status"], tr["new_status"], tr["cause"]),
                         ("已证实", "待证实", "状态刷新"))

    def test_touched_this_session_not_downgraded(self):
        """本会话有新证据触及的到期 claim 不降级(新证据已重新确认/推翻它)。"""
        self.led.conn.execute(
            "UPDATE claims SET valid_window_days=1, category='时效'"
            " WHERE claim_id='evt_t:C1'")
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        self.led.conn.execute(
            "UPDATE claim_transitions SET ts='2026-01-01T00:00:00+00:00'"
            " WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        eid_new = add_evidence(self.led, "evt_t", "gsxt", "复查确认仍成立", "C1")
        # fetched_at=_now() >= session_start(会话开始早于现在)
        down = self.led.downgrade_untouched_due("evt_t", "2000-01-01", 5)
        self.assertEqual(down, [])
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")


class TestReopenAndResume(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()

    def test_register_event_reopens_closed(self):
        """同 event_id 已存在(含 closed)→ 重开续查,不撞主键。"""
        env = _tender_envelope("evt_r")
        eid, continued = self.led.register_event(env, "", "evt_r")
        self.assertEqual((eid, continued), ("evt_r", False))
        self.led.close_event("evt_r")
        eid2, continued2 = self.led.register_event(env, "", "evt_r")
        self.assertEqual((eid2, continued2), ("evt_r", True))
        row = self.led.conn.execute(
            "SELECT status FROM events WHERE event_id='evt_r'").fetchone()
        self.assertEqual(row["status"], "open")

    def test_event_envelope_json_roundtrip(self):
        env = _tender_envelope("evt_r2", "PRJ-Y-9")
        self.led.register_event(env, "", "evt_r2")
        raw = self.led.event_envelope_json("evt_r2")
        self.assertIsNotNone(raw)
        back = InvestigationEnvelope.model_validate(json.loads(raw))
        self.assertEqual(back.tender.project_id, "PRJ-Y-9")
        self.assertIsNone(self.led.event_envelope_json("evt_nope"))


class TestReportVersioning(unittest.TestCase):
    def test_archive_previous_by_anchor(self):
        """二次交付:旧三件(md/html/brief)同档进 report_history/(锚时命名);首跑零动作。"""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "report.md").write_text("v1", encoding="utf-8")
        (tmp / "report.html").write_text("<html>v1</html>", encoding="utf-8")
        (tmp / "brief.md").write_text("简报v1", encoding="utf-8")
        (tmp / "last_finalize.json").write_text(
            json.dumps({"last_finalize_ts": "2026-10-30T12:00:00+00:00"}),
            encoding="utf-8")
        ReportBuilder._archive_previous(tmp)
        hist = sorted(p.name for p in (tmp / "report_history").iterdir())
        # stamp=锚时秒级(20261030T120000):三件同档、扩展名正确
        self.assertEqual(hist, ["brief_20261030T120000.md",
                                "report_20261030T120000.html",
                                "report_20261030T120000.md"])
        self.assertFalse((tmp / "report.md").exists())   # 已被 rename 走
        # 首跑(无旧文件)零动作不炸
        ReportBuilder._archive_previous(tmp / "fresh_dir")


class TestTimelineRender(unittest.TestCase):
    def test_markdown_timeline_grouped_by_claim(self):
        """附录 C 追加"结论演变时间轴":逐条目轨迹+日期+会话分隔+现值。"""
        led = make_ledger()
        install_simple(led)
        led.update_claim_status("evt_t:C1", "已证实", None, 1)
        led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 2)
        # 手工回填跨日 ts(模拟复查:两天两次会话,轮次重号 R1)
        led.conn.execute(
            "UPDATE claim_transitions SET ts='2026-10-21T08:00:00+00:00'"
            " WHERE claim_id='evt_t:C1' AND new_status='已证实'")
        led.conn.execute(
            "UPDATE claim_transitions SET ts='2026-10-30T09:00:00+00:00'"
            " WHERE claim_id='evt_t:C1' AND new_status='矛盾'")
        led.conn.commit()
        summary = {"transitions": [
            {"claim": "C1", "old": "待证实", "new": "已证实",
             "cause": None, "round": 1, "ts": "2026-10-21T08:00:00+00:00"},
            {"claim": "C1", "old": "已证实", "new": "矛盾",
             "cause": "矛盾翻转", "round": 1, "ts": "2026-10-30T09:00:00+00:00"},
        ], "standings": [], "event_id": "evt_t",
            "signal_type": "商机", "status_counts": {"矛盾": 1},
            "stop_reason": "测试", "costs": {}, "relations": [],
            "recommendations": []}
        builder = ReportBuilder.__new__(ReportBuilder)
        builder._ledger = led
        text = builder._render_markdown(
            [], [], {}, summary, "测试", rec_items=[], assets={}, delta=None)
        self.assertIn("结论演变时间轴", text)
        self.assertIn("条目 C1", text)
        self.assertIn("待证实 → 已证实 → 矛盾", text)
        self.assertIn("现值 矛盾", text)
        # 日期化轨迹 + 跨日会话分隔(复查重号轮次以日期区分)
        self.assertIn("[2026-10-21]R1·已证实", text)
        self.assertIn("[2026-10-30]R1·矛盾", text)
        self.assertIn("复查会话:2026-10-21 → 2026-10-30", text)

    def test_html_timeline_card(self):
        """html 同步:结论变更带日期 + 逐条目时间轴卡。"""
        led = make_ledger()
        install_simple(led)
        led.update_claim_status("evt_t:C1", "已证实", None, 1)
        summary = {"transitions": [
            {"claim": "C1", "old": "待证实", "new": "已证实",
             "cause": None, "round": 1, "ts": "2026-10-21T08:00:00+00:00"},
        ], "standings": [], "event_id": "evt_t",
            "signal_type": "商机", "status_counts": {"已证实": 3},
            "stop_reason": "测试", "costs": {}, "relations": [],
            "recommendations": [], "oq": []}
        builder = ReportBuilder.__new__(ReportBuilder)
        builder._ledger = led
        html = builder._render_html(
            [], [], {}, summary, "测试", assets={}, ess_result=None,
            delta=None, k5=None)
        self.assertIn("结论演变时间轴", html)
        self.assertIn("2026-10-21", html)
        self.assertIn("现值", html)


if __name__ == "__main__":
    unittest.main()
