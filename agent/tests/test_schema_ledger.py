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


if __name__ == "__main__":
    unittest.main()
