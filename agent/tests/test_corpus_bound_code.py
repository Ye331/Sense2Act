# -*- coding: utf-8 -*-
"""corpus_bound 代码判定化测试(手术包件 6;v2 语义 2026-10-23)。

v1(2026-10-22)=纯子串覆写;统一重录波 smoke(54 run)实证其在 live 分布
把主流复述型 claim 整体错杀(audit cb_code_audit changed=8/n=15,自证例外
全波失灵)→ v2 改为**校验语义**:加法提升(confirm)+硬证据否决(数字
伪造)+转述类尊重模型。两条 v1 断言随语义变更反转,此处注明(改了要说明):
- 「框架词前缀不命中落 world」→ 反转:框架词剥离后应命中;
- 「概括复述被覆写为 False」→ 反转:无硬冲突时尊重模型标注。

覆盖:confirm 分段判定(命中/框架词/标签胶水/单位后缀/数字精确/概括复述
不确认)/hard_contradiction(数字伪造否决/转述不否决/小数不误切)/
envelope_corpus(嵌套收集+换行分隔防数字粘连)/reconcile 终态与审计/
开关链(config/cli/META_KEYS)/PRJ- 门保护/live 回归 fixture(重录波
smoke 真实 s1b claims,69 条零错杀契约)。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sense2act.corpus_bound import (confirm, envelope_corpus,  # noqa: E402
                                    hard_contradiction, judge,
                                    norm, reconcile)
from sense2act.models.intel import ClaimDraft  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "corpus_bound_live.json"


class _FakeEnvelope:
    """最小信封桩(model_dump(by_alias=True) 形态)。"""

    def __init__(self, d: dict):
        self._d = d

    def model_dump(self, by_alias: bool = False) -> dict:
        return self._d


ENV = _FakeEnvelope({
    "kind": "tender",
    "summary": "天津市肿瘤医院空港医院数智化病理系统采购项目,金额 6270000.0 元",
    "amountAnomaly": {"explanation": "本项目金额 627.0 万元,为历史中位数的 28 倍",
                      "score": 0.99},
    "rumor": None,
})

CORPUS = envelope_corpus(ENV)


class TestNorm(unittest.TestCase):
    def test_空白标点折叠_数字保留(self):
        self.assertEqual(norm("金额 627.0 万元!"), norm("金额627.0万元"))
        self.assertIn("627", norm("金额 627.0 万元"))

    def test_英文小写(self):
        self.assertEqual(norm("SourceURL"), norm("sourceurl"))


class TestConfirm(unittest.TestCase):
    def test_忠实复述命中_字段值逐字(self):
        self.assertTrue(confirm("天津市肿瘤医院空港医院数智化病理系统采购项目",
                                CORPUS))

    def test_标点空白差异仍命中(self):
        self.assertTrue(confirm("本项目金额627.0万元,为历史中位数的28倍", CORPUS))

    def test_框架词剥离后命中_v2反转v1断言(self):
        """v1 曾断言『信封载明 X』落 world 慢路;live 数据实证=错杀主流条目。
        v2 框架词剥离+分段匹配,忠实复述必须命中。"""
        self.assertTrue(confirm("信封载明金额为6270000.0元", CORPUS))

    def test_段首标签胶水剥离(self):
        self.assertTrue(confirm("项目编号R-c67b890aeb583592,采购方天津市肿瘤医院"
                                "空港医院,金额6270000元,发布日期2026-09-09",
                                "R-c67b890aeb583592\n天津市肿瘤医院空港医院"
                                "\n6270000.0\n2026-09-09"))

    def test_概括复述不确认_落保守(self):
        self.assertFalse(confirm("该院采购金额显著异常", CORPUS))

    def test_数字不一致不确认(self):
        self.assertFalse(confirm("本项目金额 158.0 万元", CORPUS))

    def test_数字须精确命中_子串不算(self):
        """198 是 1980000 的子串,但数字 token 不等→不得命中(防伪造量级)。"""
        self.assertFalse(confirm("预算金额198元", "预算金额1980000.0元"))

    def test_小数不误切(self):
        """ASCII 点号是数字一部分,不得作段界(3227000.0 须整段判定)。"""
        self.assertTrue(confirm("金额3227000.0元", "amountYuan:3227000.0\n发布2026"))

    def test_跨字段拼接复合复述命中(self):
        """重录波 topkg 实锤形态:一条 claim 拼多字段+框架词——v1 全灭的主因。"""
        claim = ("信封载明:项目编号 R-c67b890aeb583592,项目名称『天津市肿瘤医院"
                 "空港医院数智化病理系统采购项目(项目编号SHGP-2026-A536)公开招标"
                 "公告』,采购方天津市肿瘤医院空港医院,金额 6270000 元,发布日期 "
                 "2026-09-09。")
        env2 = _FakeEnvelope({
            "projectId": "R-c67b890aeb583592",
            "title": "天津市肿瘤医院空港医院数智化病理系统采购项目"
                     "(项目编号SHGP-2026-A536)公开招标公告",
            "buyer": "天津市肿瘤医院空港医院",
            "amountYuan": 6270000.0,
            "publishedDate": "2026-09-09",
        })
        self.assertTrue(confirm(claim, envelope_corpus(env2)))

    def test_复合复述一栏伪造即不确认(self):
        claim = ("信封载明:项目编号 R-c67b890aeb583592,金额 9999999 元。")
        env2 = _FakeEnvelope({"projectId": "R-c67b890aeb583592",
                              "amountYuan": 6270000.0})
        self.assertFalse(confirm(claim, envelope_corpus(env2)))

    def test_空文本False(self):
        self.assertFalse(confirm("", CORPUS))

    def test_检测器解释片段命中_也算语料(self):
        self.assertTrue(confirm("为历史中位数的28倍", CORPUS))

    def test_judge兼容入口等于confirm(self):
        self.assertEqual(judge("天津市肿瘤医院空港医院数智化病理系统采购项目",
                               CORPUS),
                         confirm("天津市肿瘤医院空港医院数智化病理系统采购项目",
                                 CORPUS))


class TestHardContradiction(unittest.TestCase):
    def test_数字伪造否决(self):
        """模型标 True 但金额与语料冲突=客观伪造,硬否决。"""
        self.assertTrue(hard_contradiction("本项目金额 158.0 万元", CORPUS))

    def test_转述类无数字冲突不否决(self):
        """综合/转述 claim 子串不可判,但无硬证据→False(尊重模型)。"""
        self.assertFalse(hard_contradiction("解读将本信号定性为商机", CORPUS))

    def test_无数字段不触发否决(self):
        self.assertFalse(hard_contradiction("该院采购策略激进", CORPUS))


class TestEnvelopeCorpus(unittest.TestCase):
    def test_嵌套结构全收集_含数字与None安全(self):
        self.assertIn("数智化病理系统", CORPUS)
        self.assertIn("28", CORPUS)
        self.assertIn("tender", CORPUS)      # kind 枚举值也入语料

    def test_值间换行分隔_数字不跨字段粘连(self):
        """重录波实锤:无缝拼接使 3227000.0+2026 融成 3227000.02026。"""
        env = _FakeEnvelope({"amountYuan": 3227000.0,
                             "publishedDate": "2026-10-22"})
        toks = None
        from sense2act.corpus_bound import _num_tokens
        toks = _num_tokens(envelope_corpus(env))
        self.assertIn("3227000", toks)
        self.assertIn("2026", toks)
        self.assertNotIn("3227000.02026", toks)


class TestReconcileV2(unittest.TestCase):
    def _claims(self):
        return [ClaimDraft(id="C1", text="数智化病理系统采购项目",
                           category="事实", section_index=0, corpus_bound=False),
                ClaimDraft(id="C2", text="该院采购策略激进",
                           category="事实", section_index=2, corpus_bound=True)]

    def test_加法提升与转述保留_v2反转v1覆写断言(self):
        """v1 曾断言 C2(概括复述)被覆写为 False;v2 转述不否决——尊重模型。"""
        claims, audit = reconcile(self._claims(), ENV)
        self.assertTrue(claims[0].corpus_bound)    # False→True(confirm 补漏标)
        self.assertTrue(claims[1].corpus_bound)    # True 保留(无硬冲突)
        self.assertEqual(audit["n"], 2)
        self.assertEqual(audit["upgraded"], 1)
        self.assertEqual(audit["vetoed"], 0)
        self.assertEqual(audit["agreed"], 1)

    def test_数字伪造被否决(self):
        claims = [ClaimDraft(id="C1", text="本项目金额 158.0 万元",
                             category="事实", section_index=0,
                             corpus_bound=True)]
        claims, audit = reconcile(claims, ENV)
        self.assertFalse(claims[0].corpus_bound)
        self.assertEqual(audit["vetoed"], 1)

    def test_一致时零修正(self):
        claims = [ClaimDraft(id="C1", text="数智化病理系统采购项目",
                             category="事实", section_index=0, corpus_bound=True)]
        claims, audit = reconcile(claims, ENV)
        self.assertTrue(claims[0].corpus_bound)
        self.assertEqual(audit["upgraded"], 0)
        self.assertEqual(audit["vetoed"], 0)
        self.assertEqual(audit["agreed"], 1)


class TestLiveRegression(unittest.TestCase):
    """重录波 smoke(2026-10-22,54 run)真实 s1b claims 回归契约。

    fixture=4 案 69 条(信封+s1b 原文+模型标注,取自缺陷臂录制件)。
    契约:reconcile v2 终态与模型标注 100% 一致(零错杀)——v1 在同一数据上
    把自证翻正全波杀死(cb_code_audit changed=8/n=15)。fixture 扩量时本契约
    随之收紧。
    """

    def test_live69条零错杀(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        total = 0
        for case in data:
            env = _FakeEnvelope(case["envelope"])
            claims = [ClaimDraft(id=f"c{i}", text=c["text"], category="事实",
                                 section_index=0, corpus_bound=c["model_cb"])
                      for i, c in enumerate(case["claims"])]
            claims, _ = reconcile(claims, env)
            for c, src in zip(claims, case["claims"]):
                total += 1
                self.assertEqual(c.corpus_bound, src["model_cb"],
                                 f"{case['case']} 条目被错杀/误升: "
                                 f"{src['text'][:60]}")

    def test_复合载明条分段确认命中(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        case = next(c for c in data
                    if c["case"] == "tender_nayong_streetlamp")
        env = _FakeEnvelope(case["envelope"])
        corpus = envelope_corpus(env)
        composites = [c for c in case["claims"]
                      if c["text"].startswith("信封载明") and c["model_cb"]]
        self.assertTrue(composites, "fixture 须含载明型复合条")
        for c in composites:
            self.assertTrue(confirm(c["text"], corpus),
                            f"复合载明条应分段确认命中: {c['text'][:60]}")


class TestSwitchChain(unittest.TestCase):
    def test_config默认off(self):
        from sense2act.config import InvestigationConfig
        self.assertFalse(InvestigationConfig().corpus_bound_code_enabled)

    def test_meta_keys含开关(self):
        from sense2act.run_meta import META_KEYS
        self.assertIn("corpus_bound_code", META_KEYS)

    def test_cli开关解析(self):
        import argparse
        from sense2act import cli
        parser = cli.build_parser() if hasattr(cli, "build_parser") else None
        if parser is None:
            self.skipTest("cli 无独立 build_parser(装配在 main 内)")
            return
        # 2026-11-09 bridge 适配:build_parser 恢复为子命令形态(investigate
        # 参数面经 _add_investigate_parser 单源),旧平铺无子命令解析形态随
        # 语义演进退役;信封与模式两组必选参数一并补全(开关解析意图不变)。
        args = parser.parse_args(["investigate", "--envelope", "e.json",
                                  "--replay", "r.json",
                                  "--corpus-bound-code", "on"])
        self.assertEqual(args.corpus_bound_code, "on")

    def test_挂载顺序保护PRJ门_源码结构断言(self):
        """investigation 中 reconcile 调用必须在 tender_field_claims 追加之前。"""
        src = (ROOT / "sense2act" / "investigation.py").read_text(encoding="utf-8")
        i_recon = src.find("_cb_reconcile")
        i_tender = src.find("tender_field_claims(envelope.tender)")
        self.assertGreater(i_recon, 0)
        self.assertGreater(i_tender, 0)
        self.assertLess(i_recon, i_tender,
                        "reconcile 必须在机械字段追加之前(PRJ- 门语义保护)")


if __name__ == "__main__":
    unittest.main()
