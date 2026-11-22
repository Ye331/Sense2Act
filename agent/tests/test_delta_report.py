"""Delta report 数据层(方案二):ts 切分时点 diff 纯函数 + 结算锚旁车。

测试策略:账本状态直接构造(install_simple + update_claim_status 真实迁移
+ 手工校准 transitions.ts 到可控时刻),compute_delta 只读不写,断言
净变化口径/严重度排序/原因四元组/新增条派生谱系/假设触及。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import add_evidence, install_simple, make_ledger  # noqa: E402

from sense2act.delta_report import (  # noqa: E402
    compute_delta, read_anchor, write_anchor)

# 时刻轴:锚前迁移(2025-12)< 首跑 claims created_at(=现在 2026-10)<
# anchor(2027-01-01,模拟上次结算)< 锚后迁移/锚后入账(2030-01-xx)
ANCHOR = "2027-01-01T00:00:00+00:00"
POST_FLIP = "2030-01-01T00:00:00+00:00"
POST_UP = "2030-01-02T00:00:00+00:00"


def _set_ts(led, claim_id, round_no, ts):
    led.conn.execute(
        "UPDATE claim_transitions SET ts=? WHERE claim_id=? AND round=?",
        (ts, claim_id, round_no))
    led.conn.commit()


class TestComputeDelta(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def test_full_scenario(self):
        # C1:锚前翻正,锚后翻转(已证实->矛盾,严重度最高)
        add_evidence(self.led, "evt_t", "ccgp.gov.cn", "公告正文",
                     "C1", platform="ccgp")
        self.led.update_claim_status("evt_t:C1", "已证实", None, 1)
        _set_ts(self.led, "evt_t:C1", 1, "2025-12-01T00:00:00+00:00")
        self.led.update_claim_status("evt_t:C1", "矛盾", "矛盾翻转", 2, "evt_t:E1")
        _set_ts(self.led, "evt_t:C1", 2, POST_FLIP)
        # C2:锚后翻正(待证实->已证实)
        add_evidence(self.led, "evt_t", "official.cn", "官网说明", "C2")
        self.led.update_claim_status("evt_t:C2", "已证实", None, 2, "evt_t:E2")
        _set_ts(self.led, "evt_t:C2", 2, POST_UP)
        # C3:锚后折腾又回原态 -> 净不变(不计修正)
        self.led.update_claim_status("evt_t:C3", "已证实", None, 2)
        _set_ts(self.led, "evt_t:C3", 2, POST_UP)
        self.led.update_claim_status("evt_t:C3", "待证实", "状态刷新", 3)
        _set_ts(self.led, "evt_t:C3", 3, POST_UP)
        # 新增条:P2 派生(C1 异文重述 -> C1d,parent 自动指 C1);
        # created_at 校准到锚后入账(与 _set_ts 同一手法,模拟续查时刻)
        from sense2act.models.base import ClaimCategory
        self.led.add_claim("evt_t", {
            "id": "C1", "text": "B公司拖欠工资且涉及仲裁",
            "category": ClaimCategory.FACT.value, "section_index": 2}, 3)
        self.led.conn.execute(
            "UPDATE claims SET created_at=? WHERE claim_id='evt_t:C1d'",
            ("2030-01-03T00:00:00+00:00",))
        self.led.conn.commit()

        d = compute_delta(self.led, "evt_t", ANCHOR)

        self.assertEqual(d["total"], 4)
        # 修正两条:翻转(C1)排在翻正(C2)前(严重度序)
        self.assertEqual([m["claim_id"] for m in d["modified"]],
                         ["evt_t:C1", "evt_t:C2"])
        flip = d["modified"][0]
        self.assertEqual((flip["old_status"], flip["new_status"]),
                         ("已证实", "矛盾"))
        self.assertEqual(flip["cause"], "矛盾翻转")
        self.assertEqual(flip["evidence_id"], "evt_t:E1")
        # 修正原因带证据标量(谱系根/平台)——呈现层免二次 join
        self.assertEqual(flip["evidence"]["lineage_root"], "ccgp.gov.cn")
        self.assertEqual(flip["evidence"]["platform"], "ccgp")
        up = d["modified"][1]
        self.assertEqual((up["old_status"], up["new_status"]),
                         ("待证实", "已证实"))
        # 新增条:派生条带谱系
        self.assertEqual([a["claim_id"] for a in d["added"]], ["evt_t:C1d"])
        self.assertEqual(d["added"][0]["derived_from"], "evt_t:C1")
        # C3 净不变;不变计数 = total - modified - added
        self.assertEqual(d["unchanged_count"], 1)
        # 假设触及:H0 挂 C1(install_simple);C1d 为裸派生未挂假设,
        # 触及清单按归属实际记录(经 blueprint remap 的归属转移在
        # test_schema_ledger 验证)
        self.assertEqual(d["hypotheses_touched"],
                         {"evt_t:H0": ["evt_t:C1"]})

    def test_no_activity_after_anchor(self):
        d = compute_delta(self.led, "evt_t", ANCHOR)
        self.assertEqual(d["modified"], [])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["unchanged_count"], 3)
        self.assertEqual(d["hypotheses_touched"], {})

    def test_anchor_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_anchor(tmp))            # 首次运行无锚
            write_anchor(tmp, ANCHOR)
            self.assertEqual(read_anchor(tmp), ANCHOR)
            # 损坏文件按缺失处理(不抛)
            Path(tmp, "last_finalize.json").write_text("{bad", encoding="utf-8")
            self.assertIsNone(read_anchor(tmp))


if __name__ == "__main__":
    unittest.main()
