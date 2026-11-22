"""Red-team 反驳模式(方案三):选靶形态筛选/闭环两走例/预算/降级。

测试策略:真台账+fake 执行组件(executor/observer/writer5/verdicts);
LLM 生成用 mock.patch 替换模块内 call_structured(不依赖 llm 内部接口)。
核心语义断言:空手=留痕不改判;反证=复验改变状态才计 found;预算封顶;
生成失败降级不阻塞。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_evidence, install_simple, make_ledger  # noqa: E402

from sense2act.models.observation import ObservationPackage  # noqa: E402
from sense2act.stages.red_team import (  # noqa: E402
    RedTeam, RedTeamPlan, RebuttalHypothesis, select_targets)


def _log_verdict(led, claim_id, final, roots):
    led.log("evt_t", 1, "判决", {
        "claim_id": claim_id, "final": final,
        "support_roots": roots, "oppose_roots": [],
        "overridden": False, "skipped": False, "note": ""})


def _verified_target(led, draft_id="C1", root="r1.example"):
    """造一个已证实单根 world 断言(标准靶)。"""
    add_evidence(led, "evt_t", root, "公告正文", draft_id)
    led.update_claim_status(f"evt_t:{draft_id}", "已证实", None, 1)
    _log_verdict(led, f"evt_t:{draft_id}", "已证实", [root])


class _FakeExec:
    def __init__(self):
        self.sheets = []

    def execute(self, sheet, round_no, claim_text, **kw):
        self.sheets.append(sheet)
        return ObservationPackage(round=round_no, main_target=sheet.main_target,
                                  action_brief="fake", items=[],
                                  source_reports=[]), "ok"


class _FakeObs:
    def __init__(self):
        self.n = 0

    def process(self, eid, round_no, obs):
        self.n += 1
        return []


class _FakeWriter:
    def __init__(self):
        self.n = 0

    def commit(self, eid, round_no, processed):
        self.n += 1


class _FakeVerdicts:
    """fake S6:默认维持原判(空手);flip_to 非空=复验翻转(反证生效)。"""

    def __init__(self, led, flip_to=None):
        self.led = led
        self.flip_to = flip_to
        self.verified = []

    def verify(self, claim_id, round_no):
        self.verified.append(claim_id)
        if self.flip_to:
            self.led.conn.execute(
                "UPDATE claims SET status=? WHERE claim_id=?",
                (self.flip_to, claim_id))
            self.led.conn.commit()


def _plan(n=2):
    return RedTeamPlan(hypotheses=[
        RebuttalHypothesis(reason=f"反例{i}", query=f"判别词{i}")
        for i in range(n)])


class TestSelectTargets(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_shape_filter(self):
        _verified_target(self.led)                       # C1:已证实单根 -> 靶
        # C2:已证实单根但 corpus_bound=1(自证) -> 排除
        add_evidence(self.led, "evt_t", "r2.example", "语料", "C2")
        self.led.conn.execute(
            "UPDATE claims SET corpus_bound=1 WHERE claim_id='evt_t:C2'")
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        _log_verdict(self.led, "evt_t:C2", "已证实", ["r2.example"])
        # C3:待证实(无判决) -> 排除
        # C9:已证实双根 -> 排除
        self.led.add_claim("evt_t", {
            "id": "C9", "text": "双根断言", "category": "事实",
            "section_index": 2}, 1)
        add_evidence(self.led, "evt_t", "a.example", "一", "C9")
        add_evidence(self.led, "evt_t", "b.example", "二", "C9")
        self.led.update_claim_status("evt_t:C9", "已证实", None, 1)
        _log_verdict(self.led, "evt_t:C9", "已证实", ["a.example", "b.example"])

        targets = select_targets(self.led, "evt_t")
        self.assertEqual([t["claim_id"] for t in targets], ["evt_t:C1"])
        self.assertEqual(targets[0]["support_root"], "r1.example")

    def test_latest_verdict_wins(self):
        """同 claim 多次判决取最新(首跑单根、续查后仍单根以末次为准)。"""
        _verified_target(self.led)
        _log_verdict(self.led, "evt_t:C1", "已证实", ["r1.example", "r9.example"])
        self.assertEqual(select_targets(self.led, "evt_t"), [])   # 末次双根


class TestRedTeamRun(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)
        _verified_target(self.led)
        self.exec_ = _FakeExec()
        self.obs = _FakeObs()
        self.writer = _FakeWriter()

    def _run(self, verdicts, plan_n=2, budget=2):
        rt = RedTeam(self.led, llm=object(), verdicts=verdicts,
                     budget_per_claim=budget)
        with mock.patch("sense2act.stages.red_team.call_structured",
                        return_value=_plan(plan_n)):
            return rt.run("evt_t", 9, self.exec_, self.obs, self.writer)

    def test_no_hit_leaves_audit_only(self):
        """空手:复验维持原判 -> found=False,判决与状态不动,仅留痕。"""
        v = _FakeVerdicts(self.led)
        summary = self._run(v)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(summary["found"], 0)
        row = self.led.claim_row("evt_t:C1")
        self.assertEqual(row["status"], "已证实")            # 判决未被扰动
        log = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='其他'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        rt = json.loads(log["payload_json"])["red_team"]
        self.assertFalse(rt["found"])
        self.assertEqual(rt["status_before"], rt["status_after"])
        self.assertFalse(rt["degraded"])
        self.assertEqual(len(self.exec_.sheets), 2)          # 预算 2 -> 2 次查询

    def test_hit_goes_through_recheck(self):
        """反证生效:复验改变状态 -> found=True(判决归 S6 通道,本模块不加工)。"""
        v = _FakeVerdicts(self.led, flip_to="待证实")
        summary = self._run(v)
        self.assertEqual(summary["found"], 1)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")
        self.assertEqual(v.verified, ["evt_t:C1"])           # 走复验入口

    def test_budget_caps_queries(self):
        v = _FakeVerdicts(self.led)
        self._run(v, plan_n=3, budget=1)
        self.assertEqual(len(self.exec_.sheets), 1)

    def test_plan_degraded_still_audits(self):
        """生成失败降级:零查询、不抛、degraded 留痕。"""
        v = _FakeVerdicts(self.led)
        rt = RedTeam(self.led, llm=object(), verdicts=v, budget_per_claim=2)
        with mock.patch("sense2act.stages.red_team.call_structured",
                        side_effect=RuntimeError("llm down")):
            summary = rt.run("evt_t", 9, self.exec_, self.obs, self.writer)
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(len(self.exec_.sheets), 0)
        log = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='其他'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(json.loads(log["payload_json"])["red_team"]["degraded"])

    def test_no_targets_zero_footprint(self):
        """无靶(全 corpus_bound/无已证实):零查询零日志,off 之外的空转保护。"""
        self.led.conn.execute(
            "UPDATE claims SET corpus_bound=1 WHERE claim_id='evt_t:C1'")
        v = _FakeVerdicts(self.led)
        rt = RedTeam(self.led, llm=object(), verdicts=v)
        with mock.patch("sense2act.stages.red_team.call_structured") as m:
            summary = rt.run("evt_t", 9, self.exec_, self.obs, self.writer)
        self.assertEqual(summary["attempted"], 0)
        m.assert_not_called()
        n = self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE kind='其他'"
            " AND payload_json LIKE '%red_team%'").fetchone()["n"]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
