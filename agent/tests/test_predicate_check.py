"""谓词充分性核对(--predicate-check,2026-10-25 挂载)单元测试。

覆盖:触发正清单(改表即生效疫苗)/ 验票(引文逐字+复述闸+幻觉 id+封闭
枚举+role)/ 推导(core 否拦、detail 否不拦、不适用出范畴)/ 合并(A/B
互证+双重标注一致性门)/ 挂点行为(权威路径闸、cb 豁免、民间根不闸、
auth_opp 不闸、off 零装配)/ META_KEYS 配对链疫苗。
LLM 桩=脚本化 generate_json(零网络;照 ScriptedOpinions 先例)。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (add_evidence, install_simple, make_domain,  # noqa: E402
                     make_ledger)
from sense2act.config import load_domain_config  # noqa: E402
from sense2act.models.base import ClaimStatus, Direction  # noqa: E402
from sense2act.models.verdict import (EvidenceJudgment,  # noqa: E402
                                      Opinion)
from sense2act.predicate_check import (PC_ANNO_PROMPT, PredicateChecker,  # noqa: E402
                                       triggered, validate)
from sense2act.stages.s6_verdict import VerdictEngine  # noqa: E402

AUTH = make_domain().authoritative_roots
EID = "evt_t:E1"


def anno(items, overall):
    """构造标注应答。items 元组=(evidence_id, satisfied, role, predicate, quote)。"""
    return {"items": [{"evidence_id": i[0], "satisfied": i[1], "role": i[2],
                       "predicate": i[3], "evidence_quote": i[4],
                       "claim_requires": i[3]}
                      for i in items],
            "overall": overall}


class StubLLM:
    """脚本化 JSON 应答桩(记录调用;可注入异常)。"""

    def __init__(self, script=None, exc=None):
        self.script = list(script or [])
        self.exc = exc
        self.calls = 0

    def generate_json(self, system, user, *, stage=""):
        assert system == PC_ANNO_PROMPT
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.script.pop(0) if self.script else {"items": [],
                                                       "overall": "不确定"}


def ev_rows():
    return [{"evidence_id": EID, "title": "招标公告",
             "content": "项目名称:宜春环保管家服务采购项目 预算金额:1980000 元",
             "lineage_root": "ccgp"}]


NO = ("evt_t:E1", "否", "core", "近两年时间范围", "")
NO_DETAIL = ("evt_t:E1", "否", "detail", "注册地门槛(示例)", "")
YES = ("evt_t:E1", "是", "core", "项目名称载明", "宜春环保管家服务采购项目")
NA = ("evt_t:E1", "不适用", "core", "发布平台", "")


class TestTrigger(unittest.TestCase):
    def test_pattern_hit_and_miss(self):
        pats = ["近两年", "历史"]
        self.assertTrue(triggered("近两年有同类记录", pats))
        self.assertFalse(triggered("公告于昨日发布", pats))
        self.assertFalse(triggered("任意文本", []))       # 空清单=零触发

    def test_rules_table_consume_vaccine(self):
        """改表即生效:规则表增删词样直接改变触发面(数值参数从规则表消费)。"""
        tmp = Path(tempfile.mkdtemp(prefix="s2a_pctest_"))
        try:
            src = (Path(__file__).resolve().parents[1] / "data" / "config"
                   / "credibility_rules.json")
            (tmp / "credibility_rules.json").write_text(
                src.read_text(encoding="utf-8-sig"), encoding="utf-8")
            dom = load_domain_config(tmp)
            self.assertIn("近两年", dom.pc_trigger_patterns)
            # 改表:删掉一个词样 → 属性面即变
            cfg = json.loads(src.read_text(encoding="utf-8-sig"))
            cfg["predicate_check_trigger_patterns"] = ["历史"]
            (tmp / "credibility_rules.json").write_text(
                json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            dom2 = load_domain_config(tmp)
            self.assertEqual(dom2.pc_trigger_patterns, ["历史"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestValidate(unittest.TestCase):
    def test_valid_yes_derives_loaded(self):
        v = validate(anno([YES], "载明"), "该项目由本地机构承担实施",
                     ev_rows())
        self.assertEqual(v["derived"], "载明")
        self.assertEqual(v["n_yes_valid"], 1)

    def test_quote_not_in_evidence_falls_uncertain(self):
        bad = ("evt_t:E1", "是", "core", "编引文", "证据里没有这句话")
        v = validate(anno([bad], "载明"), "claim", ev_rows())
        self.assertEqual(v["derived"], "不确定")
        self.assertEqual(v["items"][0]["why"], "引文验票败")

    def test_quote_restating_claim_rejected(self):
        """D4 复述验票:引文≠claim 子串(防拿 claim 自证)。"""
        rest = ("evt_t:E1", "是", "core", "复述", "宜春环保管家服务采购项目")
        v = validate(anno([rest], "载明"),
                     "本项目为宜春环保管家服务采购项目", ev_rows())
        self.assertEqual(v["derived"], "不确定")

    def test_hallucinated_id_rejected(self):
        bad = {"evidence_id": "evt_t:E99", "satisfied": "是", "role": "core",
               "predicate": "幻觉", "evidence_quote": "x"}
        v = validate({"items": [bad], "overall": "载明"}, "claim", ev_rows())
        self.assertEqual(v["derived"], "不确定")
        self.assertEqual(v["items"][0]["why"], "幻觉id或非法枚举")

    def test_core_no_blocks_detail_no_does_not(self):
        v = validate(anno([NO], "不载明"), "近两年有记录", ev_rows())
        self.assertEqual(v["derived"], "不载明")
        v2 = validate(anno([NO_DETAIL, YES], "载明"), "claim", ev_rows())
        self.assertEqual(v2["derived"], "不确定")   # detail 否不拦,有是项非全是

    def test_na_excluded_from_derivation(self):
        v = validate(anno([NA, YES], "载明"), "claim", ev_rows())
        self.assertEqual(v["derived"], "载明")      # 不适用出范畴,余项全是

    def test_malformed_raw_uncertain(self):
        for raw in (None, "junk", {"items": "x"}, {}):
            v = validate(raw, "claim", ev_rows())
            self.assertEqual(v["derived"], "不确定")


class TestChecker(unittest.TestCase):
    PATS = ["近两年"]

    def _checker(self, llm, **kw):
        return PredicateChecker(llm, self.PATS, as_of="2026-10-22", **kw)

    def test_not_triggered_zero_calls(self):
        llm = StubLLM()
        blocked, detail = self._checker(llm).blocks("公告于昨日发布",
                                                    ev_rows())
        self.assertFalse(blocked)
        self.assertFalse(detail["triggered"])
        self.assertEqual(llm.calls, 0)              # 清单外零调用

    def test_dual_channel_agree_blocks(self):
        no_round = anno([NO], "不载明")
        llm = StubLLM([no_round, no_round])
        blocked, detail = self._checker(llm).blocks("近两年有记录", ev_rows())
        self.assertTrue(blocked)
        self.assertEqual(llm.calls, 2)              # 1 根 × runs=2
        self.assertEqual(detail["pairs"][0]["root"], "ccgp")

    def test_single_run_uncertain_releases(self):
        """双重标注一致性门:任一轮不确定 → 放行(宁漏治)。"""
        llm = StubLLM([anno([NO], "不载明"), anno([YES], "载明")])
        blocked, _ = self._checker(llm).blocks("近两年有记录", ev_rows())
        self.assertFalse(blocked)

    def test_channel_conflict_releases(self):
        """A/B 互证:items 不载明 ∧ overall 载明 → 矛盾 → 保守放行。"""
        conflict = anno([NO], "载明")
        llm = StubLLM([conflict, conflict])
        blocked, _ = self._checker(llm).blocks("近两年有记录", ev_rows())
        self.assertFalse(blocked)

    def test_llm_failure_releases(self):
        llm = StubLLM(exc=RuntimeError("网络断"))
        blocked, _ = self._checker(llm).blocks("近两年有记录", ev_rows())
        self.assertFalse(blocked)                   # 失败不伪装=现行为


class ScriptedChecker:
    """引擎挂点测试桩:可编程判決+调用记录。"""

    def __init__(self, blocked, patterns=("近两年",)):
        self.blocked = blocked
        self.patterns = list(patterns)
        self.calls = []
        from sense2act.predicate_check import triggered
        self._triggered = triggered

    def blocks(self, claim_text, evidence_rows):
        self.calls.append((claim_text, [e["evidence_id"] for e in
                                        evidence_rows]))
        if not self._triggered(claim_text, self.patterns):
            return False, {"triggered": False, "blocked": False}
        return self.blocked, {"triggered": True, "blocked": self.blocked,
                              "pairs": []}


def opinion(claim_id, judgments, verdict=ClaimStatus.VERIFIED):
    return Opinion(claim_id=claim_id,
                   evidence_judgments=[EvidenceJudgment(
                       evidence_id=eid, direction=Direction(d), quote=q,
                       reason="") for eid, d, q, r in judgments],
                   verdict=verdict, reason="")


class TestEngineMount(unittest.TestCase):
    """挂点:仅权威放行路径;cb 豁免;民间根不闸;auth_opp 不闸;留痕。"""

    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _verify(self, checker, draft_text="近两年有同类采购记录"):
        # 单权威根支持 → 走单源例外放行路径(闸位)
        add_evidence(self.led, "evt_t", "ccgp", "目标公告全文", "C1")
        prov = type("P", (), {"opinion": staticmethod(
            lambda claim, evidence, feedback="": opinion(
                "evt_t:C1", [("evt_t:E1", "支持", "目标公告全文", "")]))})()
        engine = VerdictEngine(self.led, AUTH, prov, retry=0,
                               predicate_checker=checker)
        # 改 claim 文本为触发文本
        self.led.conn.execute(
            "UPDATE claims SET text=? WHERE claim_id='evt_t:C1'",
            (draft_text,))
        self.led.conn.commit()
        return engine.verify("evt_t:C1", 1)

    def test_blocked_keeps_pending_with_trace(self):
        chk = ScriptedChecker(blocked=True)
        result = self._verify(chk)
        self.assertEqual(result.final_status, ClaimStatus.PENDING)
        self.assertIn("充分性核对", result.note)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")
        # 留痕:判决 payload 带 predicate_check
        rows = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='判决'"
        ).fetchall()
        payloads = [json.loads(r["payload_json"]) for r in rows]
        self.assertTrue(any("predicate_check" in p for p in payloads))

    def test_pass_releases_verified(self):
        result = self._verify(ScriptedChecker(blocked=False))
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_untriggered_text_zero_calls(self):
        chk = ScriptedChecker(blocked=True)
        self._verify(chk, draft_text="公告昨日发布")
        # 未触发:桩内清单不命中 → blocks 仍被调但内部零 LLM;此处验证
        # 引擎侧仍只按闸位调用一次(路径正确)
        self.assertEqual(len(chk.calls), 1)

    def test_corpus_bound_exempt(self):
        chk = ScriptedChecker(blocked=True)
        self.led.conn.execute(
            "UPDATE claims SET corpus_bound=1 WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        result = self._verify(chk)
        self.assertEqual(len(chk.calls), 0)         # cb=1 不进闸
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_folk_double_root_not_gated(self):
        """纯民间双根:D1 闸外(民间路径 0 条过度)。"""
        chk = ScriptedChecker(blocked=True)
        add_evidence(self.led, "evt_t", "weibo.com", "爆料转载", "C1")
        add_evidence(self.led, "evt_t", "bbs.example.com", "论坛帖", "C1")
        prov = type("P", (), {"opinion": staticmethod(
            lambda claim, evidence, feedback="": opinion(
                "evt_t:C1", [("evt_t:E1", "支持", "爆料转载", ""),
                             ("evt_t:E2", "支持", "论坛帖", "")]))})()
        self.led.conn.execute(
            "UPDATE claims SET text='近两年有同类采购记录' "
            "WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        engine = VerdictEngine(self.led, AUTH, prov, retry=0,
                               predicate_checker=chk)
        result = engine.verify("evt_t:C1", 1)
        self.assertEqual(len(chk.calls), 0)         # auth_sup=False 不进闸
        self.assertEqual(result.final_status, ClaimStatus.VERIFIED)

    def test_registry_conflict_skips_gate(self):
        """登记源冲突:判决走矛盾分支,闸不跑(零多余调用)。"""
        chk = ScriptedChecker(blocked=True)
        add_evidence(self.led, "evt_t", "ccgp", "目标公告全文", "C1")
        add_evidence(self.led, "evt_t", "wenshu", "反方文书", "C1")
        prov = type("P", (), {"opinion": staticmethod(
            lambda claim, evidence, feedback="": opinion(
                "evt_t:C1", [("evt_t:E1", "支持", "目标公告全文", ""),
                             ("evt_t:E2", "矛盾", "反方文书", "")],
                verdict=ClaimStatus.CONTRADICTED))})()
        self.led.conn.execute(
            "UPDATE claims SET text='近两年有同类采购记录' "
            "WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        engine = VerdictEngine(self.led, AUTH, prov, retry=0,
                               predicate_checker=chk)
        result = engine.verify("evt_t:C1", 1)
        self.assertEqual(len(chk.calls), 0)
        self.assertEqual(result.final_status, ClaimStatus.CONTRADICTED)

    def test_off_zero_trace(self):
        """off(默认):checker=None,判决 payload 无 predicate_check 键。"""
        result = self._verify(ScriptedChecker(blocked=False))  # 占位构造
        # 重建无 checker 的引擎重判
        self.led.conn.execute(
            "UPDATE claims SET status='待证实' WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        prov = type("P", (), {"opinion": staticmethod(
            lambda claim, evidence, feedback="": opinion(
                "evt_t:C1", [("evt_t:E1", "支持", "目标公告全文", "")]))})()
        engine = VerdictEngine(self.led, AUTH, prov, retry=0)
        engine.verify("evt_t:C1", 2)
        rows = self.led.conn.execute(
            "SELECT payload_json FROM decision_log WHERE kind='判决'"
        ).fetchall()
        payloads = [json.loads(r["payload_json"]) for r in rows]
        self.assertTrue(any("predicate_check" not in p for p in payloads))


class TestMetaVaccine(unittest.TestCase):
    """配对链疫苗:META_KEYS/对齐白名单/评测 flagmap 三处同源。"""

    def test_predicate_check_in_meta_keys(self):
        from sense2act.run_meta import META_KEYS, ALIGN_KEYS
        self.assertIn("predicate_check", META_KEYS)
        self.assertIn("predicate_check", ALIGN_KEYS)   # 同源派生

    def test_flag_map_covers_predicate_check(self):
        from sense2act.evaluate import RUN_ARGS_FLAG_MAP
        self.assertEqual(RUN_ARGS_FLAG_MAP.get("predicate_check"),
                         "--predicate-check")

    def test_snapshot_normalizes_on_off(self):
        from sense2act.run_meta import _snapshot
        ns = type("NS", (), {"predicate_check": "on"})()
        self.assertTrue(_snapshot(ns)["predicate_check"] is True)


if __name__ == "__main__":
    unittest.main()
