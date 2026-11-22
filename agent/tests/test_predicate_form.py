# -*- coding: utf-8 -*-
"""谓词形态标注测试(手术包件 4,2026-10-22;E2 三判据已过闸后施工)。

覆盖:
- validate 验票全分支(合法/枚举外/布尔畸形/幻觉引文/空引文/beyond=true
  无引文);
- merge 加法保守合并真值表(LLM 只能加拦不能解除);
- ClaimDraft 可选字段缺省兼容(旧 fixture 零迁移);
- S1b 开关:off 时 prompt 字节不变;on 时指令注入+畸形标注验票清洗;
- s6 消费:注入后单源例外被 LLM 标注拦截(form.beyond=true 拦 blocklist
  拦不住的条目)+note 来源区分+payload 留痕+corpus_bound 自证路径不受
  谓词门影响(仲裁);
- 改写集回归:annotations.json × case_set 预注册预期一致。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import (ALIASES, make_domain, make_ledger,  # noqa: E402
                     rumor_envelope)
from sense2act.models.base import ClaimCategory, ClaimStatus, Direction  # noqa: E402
from sense2act.models.intel import (AnalysisBlueprint, ClaimDraft,  # noqa: E402
                                    HypothesisSeed)
from sense2act.models.verdict import EvidenceJudgment, Opinion  # noqa: E402
from sense2act.predicate_form import merge, validate  # noqa: E402
from sense2act.stages.s6_verdict import VerdictEngine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ANNOT = ROOT / "eval" / "predicate_e2" / "annotations.json"
CASE_SET = ROOT / "eval" / "predicate_e2" / "case_set.json"
AUTH = make_domain().authoritative_roots

OK = {"beyond_single_doc": True, "predicate_type": "comparison",
      "grounding_quotes": ["显著高于其历史采购水平"]}
TEXT = "该院本次采购金额显著高于其历史采购水平(约历史中位数的4.2倍)"


class ScriptedOpinions:
    def __init__(self, opinions: list[Opinion]):
        self._queue = list(opinions)

    def opinion(self, claim, evidence, feedback=""):
        if not self._queue:
            raise AssertionError("意见队列耗尽")
        return self._queue.pop(0)


def opinion(claim_id, judgments, verdict=ClaimStatus.PENDING):
    return Opinion(claim_id=claim_id,
                   evidence_judgments=[EvidenceJudgment(
                       evidence_id=eid, direction=Direction(d), quote=q,
                       reason="") for eid, d, q in judgments],
                   verdict=verdict, reason="")


class TestValidate(unittest.TestCase):
    def test_合法标注_引文归一化(self):
        out = validate(dict(OK), TEXT)
        self.assertIsNotNone(out)
        self.assertTrue(out["beyond_single_doc"])
        self.assertEqual(out["predicate_type"], "comparison")

    def test_引文含换行空白归一化后过闸(self):
        raw = {**OK, "grounding_quotes": ["显著高于其\n  历史采购水平"]}
        self.assertIsNotNone(validate(raw, TEXT))

    def test_幻觉引文_非子串_作废(self):
        self.assertIsNone(validate({**OK, "grounding_quotes": ["信封上没这句话"]},
                                   TEXT))

    def test_beyond_true_无引文_作废(self):
        self.assertIsNone(validate({**OK, "grounding_quotes": []}, TEXT))

    def test_beyond_false_无引文_合法(self):
        out = validate({"beyond_single_doc": False,
                        "predicate_type": "self_contained",
                        "grounding_quotes": []}, "任意文本")
        self.assertIsNotNone(out)
        self.assertFalse(out["beyond_single_doc"])

    def test_各类畸形_作废(self):
        self.assertIsNone(validate({**OK, "predicate_type": "mystery"}, TEXT))
        self.assertIsNone(validate({**OK, "beyond_single_doc": "yes"}, TEXT))
        self.assertIsNone(validate(None, TEXT))
        self.assertIsNone(validate("true", TEXT))
        self.assertIsNone(validate({**OK, "grounding_quotes": "不是列表"}, TEXT))


class TestMerge(unittest.TestCase):
    def test_真值表_加法保守(self):
        f_true = dict(OK)
        f_false = {"beyond_single_doc": False, "predicate_type": "",
                   "grounding_quotes": []}
        cases = [
            (False, None, False, "无标注无命中=放行(现行为)"),
            (True, None, True, "blocklist 兜底命中"),
            (False, f_true, True, "LLM 加拦"),
            (True, f_true, True, "双命中"),
            (False, f_false, False, "LLM 判 false 不加拦"),
            (True, f_false, True, "blocklist 命中不被 LLM 解除(零回归)"),
        ]
        for bl, form, want, name in cases:
            self.assertEqual(merge(bl, form), want, name)


class TestClaimDraftCompat(unittest.TestCase):
    def test_缺字段默认None_旧fixture零迁移(self):
        c = ClaimDraft(id="C1", text="x", category="事实", section_index=0)
        self.assertIsNone(c.predicate_form)

    def test_畸形dict透传不炸_验票在后(self):
        c = ClaimDraft(id="C1", text="x", category="事实", section_index=0,
                       predicate_form={"beyond_single_doc": "yes"})
        self.assertEqual(c.predicate_form, {"beyond_single_doc": "yes"})
        self.assertIsNone(validate(c.predicate_form, "x"))


class _FakeLLM:
    def __init__(self, blueprint: dict):
        self._bp = blueprint

    def generate_json(self, system_prompt, user_prompt, *, stage=""):
        return self._bp


class TestS1bSwitch(unittest.TestCase):
    BP = {
        "claims": [
            {"id": "C1", "text": "信封载明金额为一百万元", "category": "事实",
             "sectionIndex": 0, "corpusBound": True,
             "predicateForm": {"beyond_single_doc": False,
                               "predicate_type": "self_contained",
                               "grounding_quotes": ["信封载明金额为一百万元"]}},
            {"id": "C2", "text": "该院本次采购金额显著高于其历史采购水平",
             "category": "事实", "sectionIndex": 2, "corpusBound": False,
             "predicateForm": {"beyond_single_doc": "yes",
                               "predicate_type": "nope"}},
        ],
        "hypotheses": [], "gaps": [],
    }

    def _analyze(self, enabled):
        from sense2act.models.envelope import InvestigationEnvelope
        from sense2act.models.intel import QualificationResult
        from sense2act.stages.s1_signal import LLMSignalAnalyzer
        a = LLMSignalAnalyzer(_FakeLLM(self.BP), retry=0, variant="中",
                              predicate_form_enabled=enabled)
        qual = QualificationResult(signal_type="竞情", reason="",
                                   selected_seed_labels=[], basis_gaps=[])
        return a, InvestigationEnvelope.model_construct(kind="rumor"), qual

    def test_off_时prompt字节不变且不清洗(self):
        from sense2act.prompts import s1b_prompt
        a, env, qual = self._analyze(False)
        self.assertEqual(a._s1b_system, s1b_prompt("中"))
        bp = a.analyze_deep(env, qual, [])
        self.assertEqual(bp.claims[1].predicate_form,
                         {"beyond_single_doc": "yes",
                          "predicate_type": "nope"})   # 原样透传不清洗

    def test_on_时注入指令且畸形清洗为None(self):
        a, env, qual = self._analyze(True)
        self.assertIn("谓词形态标注", a._s1b_system)
        bp = a.analyze_deep(env, qual, [])
        self.assertIsNotNone(bp.claims[0].predicate_form)
        self.assertIsNone(bp.claims[1].predicate_form)


class TestS6Consume(unittest.TestCase):
    """判决梯消费:LLM 标注拦 blocklist 拦不住的;留痕;自证路径不受门。"""

    def setUp(self):
        self.led = make_ledger()

    def _install(self, text, corpus_bound=False):
        from helpers import add_evidence
        bp = AnalysisBlueprint(claims=[
            ClaimDraft(id="C9", text=text, category=ClaimCategory.FACT,
                       section_index=2, corpus_bound=corpus_bound)],
            hypotheses=[], gaps=[])
        self.led.register_event(rumor_envelope("evt_t"), "", "evt_t")
        self.led.install_blueprint("evt_t", bp, aliases=ALIASES)
        add_evidence(self.led, "evt_t", "gsxt", "工商登记存续", "C9")

    def _verify(self, forms):
        eng = VerdictEngine(self.led, AUTH,
                            ScriptedOpinions([opinion("evt_t:C9", [
                                ("evt_t:E1", "支持", "工商登记存续")])]),
                            retry=0, ss_blocklist=["基线", "中位"])
        eng.set_predicate_forms(forms)
        return eng.verify("evt_t:C9", 1)

    def test_LLM标注拦住blocklist拦不住的(self):
        form = {"beyond_single_doc": True, "predicate_type": "persistence",
                "grounding_quotes": ["持续成立"]}
        self._install("本企业具备承接资质,且该条件持续成立")
        r = self._verify({"C9": form})
        self.assertEqual(r.final_status, ClaimStatus.PENDING)  # 单源例外被拦
        self.assertIn("[S1b标注]", r.note)

    def test_无标注_blocklist兜底_现行为(self):
        self._install("其利润率显著高于行业基线")
        r = self._verify({})
        self.assertEqual(r.final_status, ClaimStatus.PENDING)
        self.assertIn("blocklist", r.note)

    def test_无标注无命中_单源例外放行_零回归(self):
        self._install("该公司为存续状态")
        r = self._verify({})
        self.assertEqual(r.final_status, ClaimStatus.VERIFIED)

    def test_payload留痕_仅非None时写键(self):
        form = {"beyond_single_doc": True, "predicate_type": "persistence",
                "grounding_quotes": ["持续成立"]}
        self._install("该条件持续成立")
        self._verify({"C9": form})
        row = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='判决'"
            " AND payload_json LIKE '%evt_t:C9%'").fetchone()  # dl 无 claim_id 列
        p = json.loads(row["payload_json"])
        self.assertEqual(p["predicate_form"]["predicate_type"], "persistence")

    def test_无标注payload无pf键_旧形态稳定(self):
        self._install("该公司为存续状态")
        self._verify({})
        row = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='判决'").fetchone()
        self.assertNotIn("predicate_form", json.loads(row["payload_json"]))

    def test_自证路径不受谓词门影响(self):
        """仲裁:corpus_bound 走自证例外分支,谓词门不适用(分支顺序既有结构)。

        注:权威根∧corpus_bound 场景 _rule_note 归因显示"单源例外"(既有
        note 判定顺序与实际分支脱节,件 4 之前即如此,登记观察项不修);
        本测试只断言行为=自证例外放行。
        """
        form = {"beyond_single_doc": True, "predicate_type": "comparison",
                "grounding_quotes": ["信封载明基线"]}
        self._install("信封载明基线数值为一百", corpus_bound=True)
        r = self._verify({"C9": form})
        self.assertEqual(r.final_status, ClaimStatus.VERIFIED)  # 自证例外放行


class TestRewriteRegression(unittest.TestCase):
    """改写集回归:E2 标注(annotations.json)× 预注册预期(case_set)。"""

    def test_case_set标注验票全过且判定与预期一致(self):
        cs = {c["id"]: c for c in
              json.load(open(CASE_SET, encoding="utf-8"))["cases"]}
        doc = json.load(open(ANNOT, encoding="utf-8"))
        self.assertEqual(len(doc["case_set"]), len(cs))
        for a in doc["case_set"]:
            c = cs[a["id"]]
            out = validate({k: a[k] for k in
                            ("beyond_single_doc", "predicate_type",
                             "grounding_quotes")}, c["text"])
            self.assertIsNotNone(out, f"{a['id']} 标注未过验票")
            self.assertEqual(out["beyond_single_doc"], c["expected_beyond"],
                             f"{a['id']} 判定与预注册预期不一致")


if __name__ == "__main__":
    unittest.main()
