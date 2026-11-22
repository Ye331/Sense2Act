# -*- coding: utf-8 -*-
"""T1 根独立性查检测试(手术包件 5,2026-10-22)。

覆盖:distinct 单元(逐字转载折并/微差异折并/不同事实不折/无文本根保守
单列/输入序确定性)/判决侧(两非权威根同文转载:off 双根已证实=T1 的病,
on 折一孤证待证实)/ESS 口径一致(on 下 collect_factors 折后 support_roots
==判决 payload)/off 全链零影响。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import (ALIASES, add_evidence, make_domain,  # noqa: E402
                     make_ledger, rumor_envelope)
from sense2act.config import InvestigationConfig  # noqa: E402
from sense2act.credibility import collect_factors, load_rules  # noqa: E402
from sense2act.models.base import ClaimCategory, Direction, ClaimStatus  # noqa: E402
from sense2act.models.intel import (AnalysisBlueprint,  # noqa: E402
                                    ClaimDraft)
from sense2act.models.verdict import EvidenceJudgment, Opinion  # noqa: E402
from sense2act.stages.s6_verdict import VerdictEngine  # noqa: E402
from sense2act.store.ledger import (SIMHASH_NEAR_DUP_DIST,  # noqa: E402
                                    simhash, simhash_distance)
from sense2act.t1_independence import distinct_support_roots  # noqa: E402

AUTH = make_domain().authoritative_roots
RULES = load_rules(Path(__file__).resolve().parent.parent
                   / "data" / "config" / "ess_rules.json")

转载A = ("小红书在海外发布了自己的大模型 dots3-note preview,采用 MoE 架构,"
         "由内部团队历时一年研发。")
# 完全同文(不同域名的原样转载=T1 目标病态;实测:短文本 2 字改动的 simhash
# 距离已达 4>阈值 1——改写级折并需更精细手段,登记为演进项,本版只折逐字同文)
转载B = 转载A
另一事实 = ("小红书旗下电商业务 2025 年 GMV 突破三千亿元,增长迅速。")


class ScriptedOpinions:
    def __init__(self, opinions):
        self._queue = list(opinions)

    def opinion(self, claim, evidence, feedback=""):
        return self._queue.pop(0)


def opinion(claim_id, judgments, verdict=ClaimStatus.PENDING):
    return Opinion(claim_id=claim_id,
                   evidence_judgments=[EvidenceJudgment(
                       evidence_id=eid, direction=Direction(d), quote=q,
                       reason="") for eid, d, q in judgments],
                   verdict=verdict, reason="")


class TestDistinct(unittest.TestCase):
    def test_完全同文转载折并(self):
        out = distinct_support_roots(
            ["siteB", "siteA"], lambda r: {"siteA": 转载A, "siteB": 转载B}[r])
        self.assertEqual(out, ["siteA"])     # 排序首个为组代表

    def test_不同事实不折(self):
        out = distinct_support_roots(
            ["siteA", "siteB"],
            lambda r: {"siteA": 转载A, "siteB": 另一事实}[r])
        self.assertEqual(out, ["siteA", "siteB"])

    def test_无文本根保守单列(self):
        out = distinct_support_roots(
            ["siteA", "siteB"], lambda r: 转载A if r == "siteA" else "")
        self.assertEqual(out, ["siteA", "siteB"])   # 缺文本永不折(失败不伪装)

    def test_输入序无关_确定性(self):
        f = lambda roots: distinct_support_roots(
            roots, lambda r: {"siteA": 转载A, "siteB": 转载B}[r])
        self.assertEqual(f(["siteB", "siteA"]), f(["siteA", "siteB"]))

    def test_边界登记_短文本改写距离超阈值不折(self):
        """实测:约 40 字文本 2 字改动 simhash 距离 4>阈值 1——改写级折并
        不在本版能力内(保守正确:宁漏折不可误折);演进项=更精细手段。"""
        t2 = 转载A[:-2]                       # 删尾部 2 字,距离 3
        self.assertGreater(simhash_distance(simhash(转载A), simhash(t2)),
                           SIMHASH_NEAR_DUP_DIST)
        out = distinct_support_roots(
            ["siteA", "siteB"], lambda r: {"siteA": 转载A, "siteB": t2}[r])
        self.assertEqual(len(out), 2)

    def test_阈值语义_语义反转不折(self):
        # "发布"vs"撤回"级语义反转距离 8——不得误折(2026-10-05 定标语义)
        t1 = 转载A.replace("发布", "撤回")
        out = distinct_support_roots(
            ["siteA", "siteB"], lambda r: {"siteA": 转载A, "siteB": t1}[r],
            threshold=SIMHASH_NEAR_DUP_DIST)
        self.assertEqual(len(out), 2)


class TestVerdictConsume(unittest.TestCase):
    """T1 的价值案例:两非权威根同文转载——off 双根已证实(病),on 折一待证实。"""

    def _install_and_verify(self, t1_dist):
        led = make_ledger()
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C9", text="小红书在海外发布了大模型 dots3-note preview",
                       category=ClaimCategory.FACT, section_index=2)],
            hypotheses=[], gaps=[])
        led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        led.install_blueprint("evt_t", bp, aliases=ALIASES)
        add_evidence(led, "evt_t", "mediaA.com", 转载A, "C9")
        add_evidence(led, "evt_t", "mediaB.com", 转载B, "C9")
        eng = VerdictEngine(led, AUTH,
                            ScriptedOpinions([opinion("evt_t:C9", [
                                ("evt_t:E1", "支持", "dots3-note preview"),
                                ("evt_t:E2", "支持", "dots3-note preview")])]),
                            retry=0, ss_blocklist=[], t1_near_dup_dist=t1_dist)
        return eng.verify("evt_t:C9", 1)

    def test_off_双根已证实_现行为(self):
        r = self._install_and_verify(None)
        self.assertEqual(r.final_status, ClaimStatus.VERIFIED)
        self.assertEqual(len(r.support_roots), 2)

    def test_on_折一孤证待证实(self):
        r = self._install_and_verify(SIMHASH_NEAR_DUP_DIST)
        self.assertEqual(r.final_status, ClaimStatus.PENDING)
        self.assertEqual(r.support_roots, ["mediaA.com"])
        self.assertEqual(len(r.oppose_roots), 0)


class TestEssConsistency(unittest.TestCase):
    """口径一致:on 下 ESS 侧折后 support_roots == 判决 payload 折后。

    前置:先跑一次判决(裁定 ruling 落 claim_evidence,ESS 因子采集读取)。
    """

    def test_collect_factors折根与判决一致(self):
        led = make_ledger()
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C9", text="小红书在海外发布了大模型 dots3-note preview",
                       category=ClaimCategory.FACT, section_index=2)],
            hypotheses=[], gaps=[])
        led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        led.install_blueprint("evt_t", bp, aliases=ALIASES)
        add_evidence(led, "evt_t", "mediaA.com", 转载A, "C9")
        add_evidence(led, "evt_t", "mediaB.com", 转载B, "C9")
        eng = VerdictEngine(led, AUTH,
                            ScriptedOpinions([opinion("evt_t:C9", [
                                ("evt_t:E1", "支持", "dots3-note preview"),
                                ("evt_t:E2", "支持", "dots3-note preview")])]),
                            retry=0, ss_blocklist=[],
                            t1_near_dup_dist=SIMHASH_NEAR_DUP_DIST)
        result = eng.verify("evt_t:C9", 1)
        factors = collect_factors(led, "evt_t",
                                  near_dup_dist=SIMHASH_NEAR_DUP_DIST)
        ess_roots = next(f["support_roots"] for f in factors
                         if f["claim_id"] == "evt_t:C9")
        self.assertEqual(ess_roots, sorted(result.support_roots))
        self.assertEqual(len(ess_roots), 1)

    def test_off时ESS不折_零影响(self):
        led = make_ledger()
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C9", text="x", category=ClaimCategory.FACT,
                       section_index=2)],
            hypotheses=[], gaps=[])
        led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        led.install_blueprint("evt_t", bp, aliases=ALIASES)
        add_evidence(led, "evt_t", "mediaA.com", 转载A, "C9")
        add_evidence(led, "evt_t", "mediaB.com", 转载B, "C9")
        # 先判决落 ruling(off 判决侧也不折=双根);再采 ESS 因子(默认不折)
        eng = VerdictEngine(led, AUTH,
                            ScriptedOpinions([opinion("evt_t:C9", [
                                ("evt_t:E1", "支持", "dots3-note preview"),
                                ("evt_t:E2", "支持", "dots3-note preview")])]),
                            retry=0, ss_blocklist=[], t1_near_dup_dist=None)
        eng.verify("evt_t:C9", 1)
        factors = collect_factors(led, "evt_t")     # 默认 None=不折
        roots = next(f["support_roots"] for f in factors)
        self.assertEqual(len(roots), 2)


class TestSwitchChain(unittest.TestCase):
    def test_config默认off(self):
        self.assertFalse(InvestigationConfig().t1_independence_enabled)

    def test_meta_keys含开关(self):
        from sense2act.run_meta import META_KEYS
        self.assertIn("t1_independence", META_KEYS)


if __name__ == "__main__":
    unittest.main()
