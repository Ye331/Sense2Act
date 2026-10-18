"""近重复去重:simhash 标记 + 结构化近重复(项目编号|阶段)。

定标背景(2026-10-05):全文 simhash 被招标公告模板文字淹没(不同公告距离
min=2);标题 simhash "中标vs废标"距离仅 2 与转载重叠。结论:结构化键做
硬去重(天然避开矛盾证据),simhash 只做阈值 ≤1 的软标记(不拒绝入账)。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import install_simple, make_ledger  # noqa: E402
from sense2act.store.ledger import (  # noqa: E402
    simhash,
    simhash_distance,
)


class TestSimhashFunction(unittest.TestCase):
    def test_deterministic_and_distance(self):
        a = simhash("某医院设备采购项目公开招标公告 预算203万元")
        b = simhash("某医院设备采购项目公开招标公告 预算203万元")
        self.assertEqual(a, b)                       # 确定性
        self.assertEqual(simhash_distance(a, b), 0)  # 同文距离 0
        self.assertEqual(simhash(""), "")            # 空文本空指纹
        c = simhash("完全不同的另一段话,关于新能源电池产能扩张")
        self.assertGreater(simhash_distance(a, c), 1)

    def test_near_vs_far(self):
        # 真实长度文本(约 1000 字,定标实测):转载式微调距离 ≤1,不同文 >10
        body = ("吉林市政府采购中心受吉林市城市照明建设管理中心委托,"
                "就2026年主城区市管有路无灯街路新建路灯灯杆及配套设施项目进行公开招标。"
                "项目预算203万元人民币。投标人须具备城市及道路照明工程专业承包资质。"
                "本项目不接受联合体投标。获取招标文件时间自公告发布之日起至开标前。") * 8
        base = simhash(body)
        for tweak in (body + " 来源:中国政府采购网",
                      body + " 本文转载自网络不代表本站观点如有问题请联系删除",
                      body[: int(len(body) * 0.9)]):
            self.assertLessEqual(simhash_distance(base, simhash(tweak)), 1)
        far = simhash("完全不同的另一份公告关于医院医疗设备采购预算900万元公开招标")
        self.assertGreater(simhash_distance(base, far), 10)
        # 短文本边界(定标实测):20 字加 7 字距离已 >1——simhash 对短文本不稳定,
        # 故 simhash 只做软标记(漏标可接受),不做硬去重(误标危险)
        s_base = simhash("某医院采购AI诊断系统,预算480万元")
        s_tweak = simhash("某医院采购AI诊断系统,预算480万元 来源:本站")
        self.assertGreater(simhash_distance(s_base, s_tweak), 1)


class TestStructuredDedup(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _item(self, **kw):
        base = {"platform": "ccgp", "source": "ccgp.gov.cn", "title": "t",
                "content": "某公告", "project_no": "", "stage": ""}
        base.update(kw)
        return base

    def test_same_proj_stage_rejected(self):
        """同项目编号+同阶段(转载) -> 第二条拒绝入账。"""
        e1 = self.led.insert_evidence("evt_t", self._item(
            content="某项目公开招标公告 | 阶段:招标采购 | 项目编号:X1",
            project_no="X1", stage="招标采购"), 1)
        self.assertTrue(e1)
        e2 = self.led.insert_evidence("evt_t", self._item(
            title="t2",
            content="某项目公开招标公告 | 阶段:招标采购 | 项目编号:X1",
            project_no="X1", stage="招标采购"), 2)
        self.assertIsNone(e2)                        # 撞 proj_stage_key 唯一索引

    def test_contradiction_preserved(self):
        """同项目编号但阶段不同(中标 vs 废标) -> 都不拒绝(矛盾证据保留)。"""
        self.led.insert_evidence("evt_t", self._item(
            content="某项目中标公告 | 阶段:中标成交 | 项目编号:X1",
            project_no="X1", stage="中标成交"), 1)
        e2 = self.led.insert_evidence("evt_t", self._item(
            title="废标",
            content="某项目废标公告 | 阶段:终止废标 | 项目编号:X1",
            project_no="X1", stage="终止废标"), 2)
        self.assertTrue(e2)                          # 不同 stage 不误去重

    def test_no_project_no_falls_back_to_exact(self):
        """无项目编号 -> 不参与结构化去重,退回精确去重。"""
        e1 = self.led.insert_evidence("evt_t", self._item(
            content="同文同标题", project_no="", stage=""), 1)
        e2 = self.led.insert_evidence("evt_t", self._item(
            content="同文同标题", project_no="", stage=""), 2)
        self.assertIsNone(e2)                        # 精确 dedup_key 命中
        e3 = self.led.insert_evidence("evt_t", self._item(
            content="不同内容", project_no="", stage=""), 2)
        self.assertTrue(e3)


class TestSimhashMark(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_near_dup_marked_not_rejected(self):
        """无项目编号的转载:simhash 距离≤1 只标记 near_dup_of,照常入账。"""
        body = "某企业发布重大产品,市场反响热烈,股价上涨,专家解读积极。"
        e1 = self.led.insert_evidence("evt_t", {
            "platform": "news", "source": "a.com", "title": "某企业发布重大产品",
            "content": body}, 1)
        e2 = self.led.insert_evidence("evt_t", {
            "platform": "news", "source": "b.com", "title": "某企业发布重大产品(转载)",
            "content": body}, 2)                     # 标题微调,正文同 -> simhash 距离 0
        self.assertTrue(e2)                          # 不拒绝
        row = self.led.conn.execute(
            "SELECT near_dup_of FROM evidence WHERE evidence_id=?", (e2,)).fetchone()
        self.assertEqual(row["near_dup_of"], e1)     # 标记指向首条
        first = self.led.conn.execute(
            "SELECT near_dup_of FROM evidence WHERE evidence_id=?", (e1,)).fetchone()
        self.assertEqual(first["near_dup_of"], "")   # 首条无标记

    def test_distinct_content_not_marked(self):
        self.led.insert_evidence("evt_t", {
            "platform": "news", "source": "a.com", "title": "甲",
            "content": "关于新能源电池产能扩张的深度报道"}, 1)
        e2 = self.led.insert_evidence("evt_t", {
            "platform": "news", "source": "b.com", "title": "乙",
            "content": "关于医院信息化建设的另一篇报道"}, 2)
        row = self.led.conn.execute(
            "SELECT near_dup_of FROM evidence WHERE evidence_id=?", (e2,)).fetchone()
        self.assertEqual(row["near_dup_of"], "")     # 距离远,不标记


class TestS4PassesStageProjectNo(unittest.TestCase):
    def test_extract_from_content_suffix(self):
        from sense2act.config import load_domain_config
        from sense2act.models.observation import ObservationPackage, EvidenceItemLite
        from sense2act.stages.s4_observer import Observer
        led = make_ledger()
        obs = ObservationPackage(round=1, main_target="C1", items=[
            EvidenceItemLite(platform="ccgp", source="ccgp.gov.cn",
                             title="某医院项目公开招标公告",
                             content="某医院项目 | 阶段:招标采购 | 项目编号:XCSD-2026-C-293",
                             published_at="", author_type="官方公告",
                             dedup_key="ccgp:1")])
        processed = Observer(led, load_domain_config(
            Path(__file__).resolve().parent.parent / "data" / "config")).process(
                "evt_t", 1, obs)
        self.assertEqual(processed[0]["stage"], "招标采购")
        self.assertEqual(processed[0]["project_no"], "XCSD-2026-C-293")


if __name__ == "__main__":
    unittest.main()
