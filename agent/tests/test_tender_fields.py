"""商机字段 claim 机械生成:tender_field_claims 六字段 + 世界断言纪律。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.models.envelope import TenderSignal  # noqa: E402
from sense2act.investigation import _extract_project_no  # noqa: E402
from sense2act.stages.s1_signal import tender_field_claims  # noqa: E402


def make_tender(**kw) -> TenderSignal:
    base = dict(project_id="PRJ-SHGP-2026-A536", project_name="某医院病理系统采购",
                purchaser="某医院", amount_yuan=6270000, published_date="2026-10-03",
                project_summary="采购数智化病理系统")
    base.update(kw)
    return TenderSignal(**base)


class TestTenderFieldClaims(unittest.TestCase):
    def test_six_fields_generated(self):
        claims = tender_field_claims(make_tender())
        self.assertEqual(len(claims), 6)
        texts = [c.text for c in claims]
        self.assertTrue(any("预算金额为 6270000 元" in t for t in texts))
        self.assertTrue(any("投标截止时间为 见官方公告" in t for t in texts))
        self.assertTrue(any("采购代理机构为 见官方公告" in t for t in texts))
        self.assertTrue(any("采购内容为 采购数智化病理系统" in t for t in texts))

    def test_world_assertion_not_corpus_bound(self):
        """商机字段是外部世界断言(预算真的这么多),非信封自证 -> corpus_bound=False,
        走 ccgp 权威根单源例外而非自证例外。"""
        claims = tender_field_claims(make_tender())
        self.assertTrue(all(not c.corpus_bound for c in claims))

    def test_entity_names_anchor_purchaser(self):
        """entity_names=采购人,供 S5 实体交集挂账(详情证据 entity_ids 同采购人)。"""
        claims = tender_field_claims(make_tender())
        self.assertTrue(all(c.entity_names == ["某医院"] for c in claims))

    def test_section_and_priority(self):
        claims = tender_field_claims(make_tender())
        self.assertTrue(all(c.section_index == 2 for c in claims))   # 核心事实节
        self.assertTrue(all(c.priority == 4 for c in claims))

    def test_blank_purchaser_falls_back(self):
        """无采购人时 entity_names 为空(不锚定),不报错。"""
        claims = tender_field_claims(make_tender(purchaser=""))
        self.assertTrue(all(c.entity_names == [] for c in claims))

    def test_none_returns_empty(self):
        self.assertEqual(tender_field_claims(None), [])


class TestExtractProjectNo(unittest.TestCase):
    def test_title_regex_priority(self):
        """标题括号内编号正则优先(project_id 可能是 R-哈希,不可靠)。"""
        t = make_tender(project_id="R-c67b890aeb583592",
                        project_name="某医院数智化病理系统采购项目 (项目编号SHGP-2026-A536)公开招标公告")
        self.assertEqual(_extract_project_no(t), "SHGP-2026-A536")

    def test_project_id_fallback(self):
        """标题无编号 → 回退 project_id 剥 PRJ- 前缀。"""
        t = make_tender(project_id="PRJ-SHGP-2026-A536", project_name="某医院采购项目")
        self.assertEqual(_extract_project_no(t), "SHGP-2026-A536")

    def test_r_hash_fallback_as_is(self):
        """标题无编号且 project_id 是 R-哈希 → 回退原样(不硬造,交由下游判断)。"""
        t = make_tender(project_id="R-c67b890aeb583592", project_name="某医院采购项目")
        self.assertEqual(_extract_project_no(t), "R-c67b890aeb583592")

    def test_empty_when_neither(self):
        t = make_tender(project_id="", project_name="某医院采购项目")
        self.assertEqual(_extract_project_no(t), "")


if __name__ == "__main__":
    unittest.main()
