"""持久层:约束 / 查重 / 独立根 / relations 视图 / 快照漂移。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_evidence, install_simple, make_ledger  # noqa: E402


class TestSchemaConstraints(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_claim_status_check(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute(
                "INSERT INTO claims(claim_id,event_id,draft_id,text,category,status,"
                "section_id) VALUES('x','evt_t','Cx','t','事实','不存在的状态','evt_t:S2')")

    def test_transition_cause_check(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.led.conn.execute(
                "INSERT INTO claim_transitions(claim_id,old_status,new_status,cause,"
                "round,ts) VALUES('evt_t:C1','待证实','已证实','不存在的成因',1,'t')")

    def test_dedup_double_key(self):
        """dedupKey 双键:同文重抓返回 None;文本不同则入账。"""
        self.led.insert_evidence("evt_t", {
            "platform": "weibo", "source": "weibo.com", "title": "t",
            "content": "B公司 拖欠工资爆料", "lineage_root": "weibo.com"}, 1)
        dup = self.led.insert_evidence("evt_t", {
            "platform": "weibo", "source": "weibo.com", "title": "t",
            "content": "B公司 拖欠工资爆料", "lineage_root": "weibo.com"}, 2)
        self.assertIsNone(dup)
        fresh = self.led.insert_evidence("evt_t", {
            "platform": "weibo", "source": "weibo.com", "title": "t2",
            "content": "另一条不同内容", "lineage_root": "weibo.com"}, 2)
        self.assertTrue(fresh.endswith(":E2"))  # E1=首条, 重复不入账, 新内容=E2


class TestLineageAndRelations(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_relations_view_grows_and_shrinks(self):
        """关系类 claim 已证实 -> 视图长边;翻转矛盾 -> 边消失(零维护)。"""
        e1 = add_evidence(self.led, "evt_t", "gsxt", "A公司持有乙公司股权",
                          "C2", source="qichacha.com")   # 谱系折叠: 企查查->gsxt
        e2 = add_evidence(self.led, "evt_t", "exchange", "年报披露持股",
                          "C2")
        self.assertEqual(self.led.independent_roots("evt_t:C2")["支持"], [])
        self.led.set_ruling("evt_t:C2", e1, "支持", 1)
        self.led.set_ruling("evt_t:C2", e2, "支持", 1)
        self.assertEqual(sorted(self.led.independent_roots("evt_t:C2")["支持"]),
                         ["exchange", "gsxt"])  # 折叠后 gsxt 只算一根
        self.led.update_claim_status("evt_t:C2", "已证实", None, 1)
        rows = self.led.relations_view("evt_t")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["head_name"], "A汽车股份有限公司")
        self.assertEqual(rows[0]["relation_type"], "持股")
        # 状态刷新 -> 矛盾 -> 边自动消失
        self.led.update_claim_status("evt_t:C2", "矛盾", "状态刷新", 2)
        self.assertEqual(self.led.relations_view("evt_t"), [])

    def test_entity_anchor_and_fork(self):
        eid, canon, status = self.led.ensure_entity(
            "B公司", "company", {"b公司": "B新能源科技有限公司"}, "evt_t")
        self.assertEqual((canon, status), ("B新能源科技有限公司", "已锚"))
        self.led.mark_entity_forked(eid)
        row = self.led.conn.execute(
            "SELECT disambiguation FROM entities WHERE entity_id=?", (eid,)).fetchone()
        self.assertEqual(row["disambiguation"], "分叉")


class TestDriftAndSnapshots(unittest.TestCase):
    def test_flip_marks_drafted_section_stale(self):
        led = make_ledger()
        install_simple(led)
        # 核心事实节(C1)起草:全部终态 -> 稳定
        led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        hooks = led.stable_undrafted_sections("evt_t")  # 含 C1 的 S2
        s2 = [s for s in hooks if s["title"] == "核心事实"][0]
        led.set_draft(s2["section_id"], "文本", {"C1": "矛盾"}, 1)
        # 世界变化: C1 再翻转 -> 快照漂移 -> STALE
        led.update_claim_status("evt_t:C1", "已证实", "状态刷新", 2)
        self.assertEqual(
            [s["title"] for s in led.stale_sections("evt_t")], ["核心事实"])

    def test_hook1_query_excludes_pending(self):
        led = make_ledger()
        install_simple(led)
        led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 1)
        titles = [s["title"] for s in led.stable_undrafted_sections("evt_t")]
        self.assertIn("核心事实", titles)
        self.assertNotIn("主体与事件背景", titles)   # 无 claim 的节不自动起草
        self.assertNotIn("动态时间线与关联方", titles)  # C2/C3 仍待证实

    def test_oq_ordering(self):
        led = make_ledger()
        install_simple(led)
        ids = [r["draft_id"] for r in led.oq_claims("evt_t")]
        # 优先级 DESC(C1=5) -> 同级按草案 id ASC(C2, C3)
        self.assertEqual(ids, ["C1", "C2", "C3"])


class TestCrossRunClaimId(unittest.TestCase):
    """P2 跨 run 撞号三分支(2026-10-22):不撞直插/同文幂等/异文派生。

    draft_id 是轮内顺序号非内容指纹,续查注回原事件必然重编——"id 同"
    不构成"同断言"的证据(评测层双闸教训前置兑现到台账层)。
    """

    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _install_bp(self, *claims, hyps=()):
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import (AnalysisBlueprint, ClaimDraft,
                                            HypothesisSeed)
        drafts = [ClaimDraft(id=cid, text=text, category=ClaimCategory.FACT,
                             section_index=2, entity_names=[])
                  for cid, text in claims]
        hyp_seeds = [HypothesisSeed(id="H0", label="续查假设", claim_ids=list(hyps))]
        bp = AnalysisBlueprint(claims=drafts, hypotheses=hyp_seeds, gaps=[])
        self.led.install_blueprint("evt_t", bp)

    def _count(self):
        return self.led.conn.execute(
            "SELECT COUNT(*) AS n FROM claims WHERE event_id='evt_t'").fetchone()["n"]

    def test_rerun_same_text_idempotent(self):
        """同 id 同文=同一断言:幂等跳过不重复入账,审计留痕。"""
        self._install_bp(("C1", "B公司拖欠工资"))   # 与 install_simple 的 C1 同文
        self.assertEqual(self._count(), 3)          # 不新增
        row = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE event_id='evt_t'"
            " AND kind='入账' ORDER BY id DESC LIMIT 1").fetchone()
        import json
        self.assertEqual(json.loads(row["payload_json"])["cross_run_dup"], ["C1"])

    def test_rerun_diff_text_derives(self):
        """同 id 异文=新知识:派生 C1d,parent 指旧条,假设归属转给新条。"""
        # hyps 模拟真实续查:新 run 的 S1b 同样输出假设归属(H0 <- C1),
        # 经 remap 落到派生条 C1d 名下
        self._install_bp(("C1", "B公司涉嫌拖欠工资且涉及外包劳务纠纷"),
                         hyps=["C1"])
        row = self.led.conn.execute(
            "SELECT text, parent_claim_id, created_at FROM claims"
            " WHERE claim_id='evt_t:C1d'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["parent_claim_id"], "evt_t:C1")
        self.assertTrue(row["created_at"])          # P2 起新条写入账时刻
        # 旧条原封不动(append-only)
        old = self.led.conn.execute(
            "SELECT text FROM claims WHERE claim_id='evt_t:C1'").fetchone()
        self.assertEqual(old["text"], "B公司拖欠工资")
        # 假设归属经 remap 转给派生新条(install_simple 的 H0 挂 C1)
        linked = {r["claim_id"] for r in self.led.conn.execute(
            "SELECT claim_id FROM hypothesis_claims WHERE hypothesis_id='evt_t:H0'")}
        self.assertIn("evt_t:C1d", linked)

    def test_derived_collision_progresses(self):
        """C1d 已占且再撞异文:序数递进派生 C1d2(确定性可重放)。"""
        from sense2act.models.base import ClaimCategory
        from sense2act.models.intel import AnalysisBlueprint, ClaimDraft
        self._install_bp(("C1", "异文一"))
        bp = AnalysisBlueprint(
            claims=[ClaimDraft(id="C1", text="异文二", category=ClaimCategory.FACT,
                               section_index=2, entity_names=[])],
            hypotheses=[], gaps=[])
        self.led.install_blueprint("evt_t", bp)
        ids = {r["claim_id"] for r in self.led.conn.execute(
            "SELECT claim_id FROM claims WHERE event_id='evt_t'")}
        self.assertIn("evt_t:C1d", ids)
        self.assertIn("evt_t:C1d2", ids)

    def test_add_claim_branches(self):
        """add_claim 三入口统一语义:同文返回原 id 不入账;异文返回派生 id。"""
        from sense2act.models.base import ClaimCategory
        cat = ClaimCategory.FACT.value
        base = {"category": cat, "section_index": 2, "priority": 3}
        # 同文幂等
        same = dict(base, id="C1", text="B公司拖欠工资")
        self.assertEqual(self.led.add_claim("evt_t", same, 2), "evt_t:C1")
        self.assertEqual(self._count(), 3)
        # 异文派生:返回实际新 id,parent 自动指被撞原条
        diff = dict(base, id="C1", text="B公司被曝拖欠工资且涉仲裁")
        got = self.led.add_claim("evt_t", diff, 2)
        self.assertEqual(got, "evt_t:C1d")
        row = self.led.conn.execute(
            "SELECT parent_claim_id FROM claims WHERE claim_id='evt_t:C1d'").fetchone()
        self.assertEqual(row["parent_claim_id"], "evt_t:C1")
        # 不撞直插行为不变
        fresh = dict(base, id="C9", text="全新断言")
        self.assertEqual(self.led.add_claim("evt_t", fresh, 2), "evt_t:C9")


class TestMaxRound(unittest.TestCase):
    """D7 round 全局续接的起点查询(2026-10-23):decision_log max(round)。"""

    def test_empty_event_zero(self):
        led = make_ledger()
        install_simple(led)   # 轮 0 入账有 log(round=0)
        self.assertEqual(led.max_round("evt_t2"), 0)

    def test_tracks_max_round(self):
        led = make_ledger()
        install_simple(led)
        led.log("evt_t", 3, "其他", {"note": "x"})
        led.log("evt_t", 5, "其他", {"note": "y"})
        self.assertEqual(led.max_round("evt_t"), 5)
        led.log("evt_t", 4, "其他", {"note": "z"})
        self.assertEqual(led.max_round("evt_t"), 5)   # 非单调写入不影响上界


if __name__ == "__main__":
    unittest.main()
