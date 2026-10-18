"""评测层单元:金标 <-> 系统 claim 对齐(draft_id 主对齐 + 文本相似度次对齐)。

对齐纪律:禁裸字符串相等当金标(历史教训:严格匹配 0-shot F1=0.00);
对不上的显式上报(unmatched_golden / system_extra),不静默丢弃。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.evaluate import AlignConfig, align, sim_ratio  # noqa: E402


def g(did: str, text: str, status: str = "待证实") -> dict:
    return {"draft_id": did, "text": text, "golden_status": status}


def s(did: str, text: str, status: str = "待证实") -> dict:
    return {"draft_id": did, "text": text, "category": "事实",
            "status": status, "unreachable": 0}


class TestAlign(unittest.TestCase):

    def test_draft_id_exact_alignment(self):
        """同 draft_id 且文本在下限之上 -> 配对(回放下确定性主通道);
        文本几乎无关的同 id 对由 floor 闸拆开(见 test_primary_text_floor_splits_id_collision)。"""
        al = align([g("C1", "甲公司在2026年9月发布了新款芯片产品")],
                   [s("C1", "甲公司在2026年9月公开发布了新款芯片")], AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["method"], "draft_id")
        self.assertEqual(al["pairs"][0]["sim"], 1.0)
        self.assertEqual(al["unmatched_golden"], [])
        self.assertEqual(al["system_extra"], [])

    def test_text_sim_fallback_above_threshold(self):
        """draft_id 不在时,相似文本走 text_sim 次对齐。"""
        al = align(
            [g("G1", "小红书在海外开源发布了大模型dots3-note")],
            [s("c5", "小红书在海外开源发布了大模型 dots3-note preview。")],
            AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["method"], "text_sim")
        self.assertGreaterEqual(al["pairs"][0]["sim"], 0.60)

    def test_text_sim_below_threshold_unmatched_golden(self):
        """低于阈值不硬配:金标进 unmatched,系统 claim 进 system_extra。"""
        al = align(
            [g("G1", "该院智慧医院三期采购金额显著高于历史水平")],
            [s("c9", "今日天气晴朗适合晾晒衣物")], AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["unmatched_golden"]), 1)
        self.assertEqual(len(al["system_extra"]), 1)

    def test_many_to_one_prevented(self):
        """两条金标近似同一条系统 claim:一对一,sim 高者胜,另一条落 unmatched。"""
        text = "B公司自2026年7月起拖欠员工工资"
        al = align([g("G1", text), g("G2", text)], [s("c1", text)],
                   AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["golden"]["draft_id"], "G1")
        self.assertEqual([x["draft_id"] for x in al["unmatched_golden"]], ["G2"])

    def test_system_extra_reported_not_paired(self):
        """系统多出的 claim(S5 派生等)进 system_extra 只报告,不参与配对。"""
        al = align([g("C1", "甲公司发布新款芯片")],
                   [s("C1", "甲公司发布新款芯片"),
                    s("c99", "S5 派生的一条完全不同的断言内容")], AlignConfig())
        self.assertEqual([x["draft_id"] for x in al["system_extra"]], ["c99"])

    def test_normalization(self):
        """NFKC 全半角 + casefold + 去空白后相同 => 相似度 1.0。

        注:。(U+3002)无 NFKC 兼容分解,不折算为".",故用"(U+FF0C)做全角标点样例。
        """
        self.assertEqual(sim_ratio("ＡＢＣ ｄｅｆ，", "abcdef,"), 1.0)
        self.assertEqual(sim_ratio("B公司拖欠工资", "b公司拖欠工资"), 1.0)
        self.assertGreaterEqual(sim_ratio("B公司 拖欠工资！", "b公司拖欠工资"), 0.9)

    def test_align_audit_fields(self):
        """每个配对携带 method 与 sim 审计字段。"""
        golden = [g("C1", "甲公司发布新款芯片"),
                  g("C2", "小红书在海外开源发布了大模型")]
        system = [s("C1", "甲公司发布新款芯片"),
                  s("c7", "小红书在海外开源发布了 大模型dots。")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"][0]["method"], "draft_id")
        self.assertEqual(al["pairs"][1]["method"], "text_sim")
        for p in al["pairs"]:
            self.assertIn(p["method"], ("draft_id", "text_sim"))
            self.assertIsInstance(p["sim"], float)


class TestPredicateTypeGate(unittest.TestCase):
    """谓词类型闸(2026-10-04):载明型与实质断言不互相对齐(修伪过度断言)。"""

    GOLDEN_RELATION = ("科大讯飞与阿里云同为中国科学院大连化学物理研究所"
                       "智能化工大模型3.0Pro 的联合研发方,二者构成同一阵营的协同方。")
    SYSTEM_REPORTING = ("线索 k3 与 k4 正文均载明:科大讯飞与阿里云同为中国科学院"
                        "大连化学物理研究所智能化工大模型3.0Pro 的联合研发方。")

    def test_cross_type_secondary_pairing_rejected(self):
        """载明型系统 claim x 实质型金标:高相似也不配 -> 金标计漏报、系统入 extra。"""
        golden = [g("c14", self.GOLDEN_RELATION, "待证实")]
        system = [s("c16", self.SYSTEM_REPORTING, "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["unmatched_golden"]), 1)
        self.assertEqual(len(al["system_extra"]), 1)
        self.assertEqual(len(al["type_rejected"]), 1)
        self.assertGreaterEqual(al["type_rejected"][0]["sim"], 0.60)

    def test_reporting_pairing_still_aligns(self):
        """载明型对载明型:类型相同,照常次对齐。"""
        golden = [g("c8", "线索 k1、k2、k3、k4 的标题均为'我国化工行业首个大模型发布新版本'")]
        system = [s("x1", "线索 k1、k2、k3、k4 的标题均为'我国化工行业首个大模型发布新版本'",
                    "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["type_rejected"], [])

    def test_substantive_pairing_still_aligns(self):
        """实质对实质:类型相同,照常次对齐。"""
        golden = [g("c11", "智能化工大模型3.0Pro 由大连化物所联合科大讯飞、阿里云共同研发")]
        system = [s("y1", "智能化工大模型3.0Pro 由中国科学院大连化物所联合科大讯飞与阿里云共同研发",
                    "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["type_rejected"], [])

    def test_system_corpus_bound_flag_takes_precedence(self):
        """系统侧 corpus_bound 旗标优先于标记词:旗标 True 即载明型(无标记词也拒配实质金标)。"""
        golden = [g("c14", self.GOLDEN_RELATION, "待证实")]
        system = [s("c16", "讯飞与阿里云同为化物所大模型的联合研发方并构成协同。",
                    "已证实")]
        system[0]["corpus_bound"] = 1
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["type_rejected"]), 1)

    def test_primary_text_floor_splits_id_collision(self):
        """主对齐文本下限闸:同 draft_id 但文本几乎无关 = 跨 run 撞号,拆开
        (sweep3d 实测:金标实质 c14 撞系统载明 c14 -> 伪过度断言)。"""
        golden = [g("c14", self.GOLDEN_RELATION, "待证实")]
        system = [s("c14", "北京市密云区中医医院采购 DR 数字化摄影机,预算 158 万元。",
                    "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["unmatched_golden"]), 1)
        self.assertEqual(len(al["system_extra"]), 1)
        self.assertEqual(len(al["id_split"]), 1)
        self.assertLess(al["id_split"][0]["sim"], 0.30)

    def test_primary_same_id_similar_text_kept(self):
        """主对齐:同 id 且文本相似度在下限之上 -> 保留(回放回归主通道不受影响)。"""
        golden = [g("c14", self.GOLDEN_RELATION, "待证实")]
        system = [s("c14", self.GOLDEN_RELATION, "待证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["method"], "draft_id")


    def test_primary_also_type_gated(self):
        """主对齐同过类型闸(2026-10-05 复发修复):同 draft_id 但一载明一实质
        = 编号巧合,不配对(sweep4 实测:金标实质 c14 撞系统载明 c14 ->
        伪过度断言;该闸曾只在次对齐,主对齐漏防)。"""
        golden = [g("c14", self.GOLDEN_RELATION, "待证实")]
        system = [s("c14", self.SYSTEM_REPORTING, "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["unmatched_golden"]), 1)
        self.assertEqual(len(al["system_extra"]), 1)
        self.assertEqual(len(al["type_rejected"]), 1)
        self.assertEqual(al["type_rejected"][0]["method"], "draft_id")


class TestFloorSplitPoolReturn(unittest.TestCase):
    """文本下限闸拆开后的回池行为(2026-10-04 分诊收尾):拆开不是终点,
    双方回池参与次对齐——修复"伪配对挤占真配对"效应(sweep3 中档 c19/M2
    被撞号伪配对挤出 unmatched 的实证场景)。"""

    def test_split_then_pool_return_rescues_true_pair(self):
        """金标 c1 与系统 X 同 id 但断言无关 -> floor 拆开回池;被挤占的
        金标 c19 与 X 文本高度相似 -> 次对齐配上(类型同向,均载明型)。"""
        golden = [
            g("c1", "解读记录了假设种子标签为真实经营动态与新方向扩张"),
            g("c19", "信封 tender 字段为 null,未提供采购方金额信息"),
        ]
        system = [
            s("c1", "信封 tender 字段为 null,本次信号无采购方金额信息", "待证实"),
        ]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["golden"]["draft_id"], "c19")
        self.assertEqual(al["pairs"][0]["method"], "text_sim")
        self.assertEqual([x["draft_id"] for x in al["unmatched_golden"]], ["c1"])
        self.assertEqual(len(al["id_split"]), 1)
        self.assertEqual(al["id_split"][0]["golden"], "c1")

    def test_floor_zero_disables_check(self):
        """draft_id_text_floor=0 关闭文本校验:id 配上即配对(旧行为兼容,
        回放回归场景金标与录制件同源,可显式关闸)。"""
        cfg = AlignConfig(draft_id_text_floor=0.0)
        al = align([g("C1", "金标表述甲")], [s("C1", "系统表述乙完全不同")], cfg)
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["method"], "draft_id")


if __name__ == "__main__":
    unittest.main()


class TestElementsChannel(unittest.TestCase):
    """要素匹配通道(方向 B,2026-10-06):金标声明跨 run 稳定实体词,
    系统 claim 全覆盖即配——治字面相似度的灰色带(同义改写/长文稀释)。"""

    def test_elements_rescue_grey_band(self):
        """字面相似度不足 0.60(灰色带)但要素全覆盖 → elements 通道配上。"""
        golden = [dict(g("c19", "本次信号未提供任何采购方、金额或招标信息,信封 tender 字段为 null"),
                       elements=["tender", "null"])]
        system = [s("c1", "信封 kind 为 rumor,eventId 为 evt_x,tender 字段为 null")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["method"], "elements")
        self.assertEqual(al["unmatched_golden"], [])

    def test_elements_partial_coverage_not_paired(self):
        """要素未全覆盖(缺一词)且字面也不过阈 → 双通道都不配,防宽配。"""
        golden = [dict(g("c12", "3.0Pro 于 2026-08-31 正式对外发布。"),
                       elements=["3.0Pro", "2026-08-31", "对外发布"])]
        system = [s("x1", "3.0Pro 对外发布")]   # 缺日期要素;字面 sim 也 <0.60
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["unmatched_golden"]), 1)

    def test_elements_respects_type_gate(self):
        """要素通道同过谓词类型闸:金标实质 x 系统载明不配。"""
        golden = [dict(g("c14", "科大讯飞与阿里云构成同一阵营的协同方。"),
                       elements=["讯飞", "阿里云", "协同"])]
        system = [s("x1", "线索 k3 载明:讯飞与阿里云共同研发,构成协同。", "已证实")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"], [])
        self.assertEqual(len(al["type_rejected"]), 1)

    def test_elements_richer_claim_wins(self):
        """多条金标命中同一条系统 claim:要素多者(更具体)先占。"""
        golden = [dict(g("c10", "k3/k4 发布日早于 signalTime"), elements=["2026-08-31"]),
                  dict(g("c18", "k3/k4 时点早于 k1/k2 时点"), elements=["2026-08-31", "16:16"])]
        system = [s("c6", "发布时间对比:2026-08-31(16:16 与 16:00)早于 2026-09-03")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(len(al["pairs"]), 1)
        self.assertEqual(al["pairs"][0]["golden"]["draft_id"], "c18")
        self.assertEqual([x["draft_id"] for x in al["unmatched_golden"]], ["c10"])

    def test_no_elements_untouched(self):
        """无 elements 的金标条目走旧通道,行为不变(渐进迁移)。"""
        golden = [g("G1", "小红书在海外开源发布了大模型dots3-note")]
        system = [s("c5", "小红书在海外开源发布了大模型 dots3-note preview。")]
        al = align(golden, system, AlignConfig())
        self.assertEqual(al["pairs"][0]["method"], "text_sim")
