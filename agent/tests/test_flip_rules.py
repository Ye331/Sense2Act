"""D4 维持偏置闸 + D2 同 event 复验收口(2026-11-08,施工单
《动态信念修正-D4+D2收口施工单-20261108.md》§四测试清单)。

认识论定位(锁死):维持偏置=宁漏翻不误翻(拍板2);分权=触发与判决一律
确定性代码,修正链路 LLM 零参与;不开平行判决路径(闸只在降级方向加前置
条件,复用 S6 既有全部验票);首次判决(待证实→X)零影响(回归锁);
append-only=transitions 零改造(拦截即无 transition,零 schema 改动);
off=全链零行为变化(payload 形态与 off 逐键一致)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import (ROOT, add_evidence, install_simple, make_domain,  # noqa: E402
                     make_ledger)
from sense2act.models.base import ClaimStatus, Direction  # noqa: E402
from sense2act.models.verdict import EvidenceJudgment, Opinion  # noqa: E402
from sense2act.run_meta import META_KEYS, _snapshot  # noqa: E402
from sense2act.stages.s6_verdict import (VerdictEngine,  # noqa: E402
                                         load_flip_rules)

AUTH = make_domain().authoritative_roots
RULES = load_flip_rules(ROOT / "data" / "config")


def opinion(claim_id, judgments, verdict=ClaimStatus.PENDING):
    return Opinion(claim_id=claim_id,
                   evidence_judgments=[EvidenceJudgment(
                       evidence_id=eid, direction=Direction(d), quote=q,
                       reason="")
                       for eid, d, q in judgments],
                   verdict=verdict, reason="")


class Scripted:
    """脚本化意见提供者(照 test_s6_verdict 先例;顺序出票)。"""

    def __init__(self, opinions):
        self._q = list(opinions)

    def opinion(self, claim, evidence, feedback=""):
        if not self._q:
            raise AssertionError("意见队列耗尽")
        return self._q.pop(0)


class FlipBase(unittest.TestCase):
    def setUp(self):
        self.led = make_ledger()
        install_simple(self.led)

    def _engine(self, provider, rules=RULES):
        return VerdictEngine(self.led, AUTH, provider, retry=0,
                             flip_rules=rules)

    def _seed_verified(self, round_no=1):
        """C1 两条民间独立支持根 -> 已证实(双根慢路;权威反证留给翻转场景)。"""
        add_evidence(self.led, "evt_t", "forum_a", "员工爆料工资已发", "C1",
                     round_no=round_no, author_type="认证账号", credibility="高")
        add_evidence(self.led, "evt_t", "forum_b", "工会通报欠薪已解决", "C1",
                     round_no=round_no, author_type="认证账号", credibility="高")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决")],
            verdict=ClaimStatus.VERIFIED)])
        self._engine(prov).verify("evt_t:C1", round_no)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")

    def _backdate_last_flip(self, days: int) -> None:
        """末次翻转 ts 拨回 N 天前(照 test_dynamic_report 手工回填先例;
        冷却窗判据从 transitions ts 算,零 schema 改动)。"""
        ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.led.conn.execute(
            "UPDATE claim_transitions SET ts=? WHERE claim_id='evt_t:C1'",
            (ts,))
        self.led.conn.commit()

    def _verdict_payloads(self, kind: str = "判决") -> list[dict]:
        rows = self.led.decision_log_rows("evt_t")
        out = []
        for r in rows:
            if r["kind"] == kind:
                try:
                    out.append(json.loads(r["payload_json"]))
                except (json.JSONDecodeError, TypeError):
                    pass
        return out

    def _last_payload(self) -> dict:
        return self._verdict_payloads()[-1]


class TestRulesTable(FlipBase):
    """规则表:加载/缺文件全关/缺键单闸关/改表即生效(数值参数疫苗)。"""

    def test_load_real_table(self):
        self.assertIsInstance(RULES, dict)
        for key in ("downgrade", "hysteresis", "cooldown_days", "jitter_alarm"):
            self.assertIn(key, RULES)
        self.assertEqual(RULES["cooldown_days"], 7)
        self.assertEqual(RULES["hysteresis"]["promote_indep_roots"], 2)
        self.assertEqual(RULES["downgrade"]["hard_root"], 1)
        self.assertEqual(RULES["jitter_alarm"]["flips_per_claim_per_30d"], 2)

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(load_flip_rules(td))

    def test_missing_key_disables_single_gate(self):
        """缺 downgrade 键=降级闸关:转述级反证场景照旧降级(闸不装配该向)。"""
        self._seed_verified()
        self._backdate_last_flip(30)          # 出冷却窗,只剩降级闸
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        self._engine(prov, rules={"hysteresis": {"promote_indep_roots": 2}}).verify(
            "evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")

    def test_table_change_takes_effect(self):
        """改表即生效:hard_root 1->2 后,单硬反证根从放行降级变为拦截维持。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "gov_site", "监管通报处罚", "C1",
                     round_no=2, author_type="官方公告", credibility="高")
        judgments = [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "监管通报处罚")]
        prov = Scripted([opinion("evt_t:C1", judgments)])
        strict = {"downgrade": {"hard_root": 2, "authoritative_single": False},
                  "hysteresis": {"promote_indep_roots": 2}}
        # 默认表 hard_root=1:单硬根 -> 放行降级(已证实 -> 待证实)
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")
        # 改表 hard_root=2:同场景 -> 拦(维持已证实)
        self.led.conn.execute(
            "UPDATE claims SET status='已证实' WHERE claim_id='evt_t:C1'")
        self.led.conn.commit()
        prov2 = Scripted([opinion("evt_t:C1", judgments)])
        self._engine(prov2, rules=strict).verify("evt_t:C1", 3)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")


class TestMaintainBias(FlipBase):
    """维持偏置闸(拍板2 宁漏翻不误翻)。"""

    def test_hearsay_opposition_observe_only(self):
        """转述级反证=只观察不翻转:维持已证实+payload 挂 observe_only 留痕
        (transitions 零新增=拦截即无 transition,零 schema 改动)。"""
        self._seed_verified()
        self._backdate_last_flip(30)          # 出冷却窗
        n_trans = len(self.led.conn.execute(
            "SELECT 1 FROM claim_transitions WHERE claim_id='evt_t:C1'"
        ).fetchall())
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        p = self._last_payload()
        self.assertEqual(p["flip_guard"]["action"], "observe_only")
        self.assertEqual(p["flip_guard"]["hearsay_roots"], ["forum_x"])
        self.assertEqual(p["flip_guard"]["hard_roots"], [])
        n_after = len(self.led.conn.execute(
            "SELECT 1 FROM claim_transitions WHERE claim_id='evt_t:C1'"
        ).fetchall())
        self.assertEqual(n_after, n_trans)     # 状态未动=零 transition

    def test_hard_opposition_flips(self):
        """硬反证根(一手官方高可信,非权威清单)达门槛 -> 放行降级,零额外痕迹。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "gov_site", "监管通报处罚", "C1",
                     round_no=2, author_type="官方公告", credibility="高")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "监管通报处罚")])])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")
        self.assertNotIn("flip_guard", self._last_payload())   # 放行=零痕迹

    def test_authoritative_single_flips(self):
        """权威反证单源 -> 放行翻矛盾(已证实->矛盾;硬根=权威根达 hard_root)。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1",
                     round_no=2, author_type="官方", credibility="高")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")
        self.assertNotIn("flip_guard", self._last_payload())

    def test_first_party_opposition_observe_only(self):
        """当事方自述反证=利益相关陈述(author_type 精确等值,照 chain_graph
        party 先例),credibility 再高也不算 hard -> observe_only。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "company_web", "公司声明无欠薪", "C1",
                     round_no=2, author_type="当事方", credibility="高")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "公司声明无欠薪")])])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        self.assertEqual(self._last_payload()["flip_guard"]["action"],
                         "observe_only")

    def test_first_verdict_unaffected(self):
        """首次判决(待证实->X)零影响(回归锁):待证实+权威反证单源 -> 矛盾
        照旧,零 flip_guard 痕迹。"""
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1", round_no=1)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 1)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")
        self.assertNotIn("flip_guard", self._last_payload())


class TestHysteresis(FlipBase):
    """升级滞后:降级后再升回已证实需 promote_indep_roots 独立根(2:1)。"""

    def test_demoted_single_root_cannot_promote(self):
        """已证实->矛盾后,单权威支持根洗白被拦(维持矛盾+hysteresis_hold)。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1", round_no=2)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")
        # 轮3:单权威支持根 -> 判决梯权威单源例外给已证实,滞后闸拦
        add_evidence(self.led, "evt_t", "zxgk", "黑名单已移出", "C1", round_no=3)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "时效刷新", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "时效刷新", "列入欠薪黑名单"),
            ("evt_t:E4", "支持", "黑名单已移出")],
            verdict=ClaimStatus.VERIFIED)])
        self._engine(prov).verify("evt_t:C1", 3)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")
        p = self._last_payload()
        self.assertEqual(p["flip_guard"]["action"], "hysteresis_hold")
        self.assertEqual(p["flip_guard"]["promote_indep_roots"], 2)
        self.assertEqual(p["flip_guard"]["support_roots"], ["zxgk"])

    def test_demoted_two_roots_promote(self):
        """降级后两条独立支持根 -> 达 promote_indep_roots=2,放行升回。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1", round_no=2)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 2)
        add_evidence(self.led, "evt_t", "zxgk", "黑名单已移出", "C1", round_no=3)
        add_evidence(self.led, "evt_t", "wenshu", "判决书载明已履行", "C1",
                     round_no=3)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "时效刷新", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "时效刷新", "列入欠薪黑名单"),
            ("evt_t:E4", "支持", "黑名单已移出"),
            ("evt_t:E5", "支持", "判决书载明已履行")],
            verdict=ClaimStatus.VERIFIED)])
        self._engine(prov).verify("evt_t:C1", 3)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        self.assertNotIn("flip_guard", self._last_payload())

    def test_clock_demoted_then_repromote_needs_two(self):
        """时钟降级后的升回同走滞后闸(两轨并存拍板5:证据轨管有新证据翻转)。"""
        self._seed_verified()
        # 纯时钟降级(照 downgrade_untouched_due 同款 transition)
        self.led.update_claim_status("evt_t:C1", "待证实", "状态刷新", 2)
        add_evidence(self.led, "evt_t", "zxgk", "最新公示仍无欠薪", "C1", round_no=3)
        # 构造单根场景:意见把老两条改判时效刷新,只余 zxgk 一根支持
        # (若维持 E1/E2 支持,根集 3 个 >=2 天然放行,测不到滞后闸)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "时效刷新", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "支持", "最新公示仍无欠薪")],
            verdict=ClaimStatus.VERIFIED)])
        self._engine(prov).verify("evt_t:C1", 3)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "待证实")
        self.assertEqual(self._last_payload()["flip_guard"]["action"],
                         "hysteresis_hold")


class TestCooldownAndJitter(FlipBase):
    """冷却窗 + 抖动监控(防抖三件套后两件)。"""

    def test_cooldown_holds_same_strength(self):
        """翻转后 7 天内,同强度(非权威)反证不再二次翻转(cooldown_hold)。"""
        self._seed_verified()                 # 末次翻转 ts=今天(冷却窗内)
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        p = self._last_payload()
        self.assertEqual(p["flip_guard"]["action"], "cooldown_hold")
        self.assertEqual(p["flip_guard"]["cooldown_days"], 7)

    def test_cooldown_not_block_authoritative(self):
        """冷却窗不拦权威反证(法定公示变更非噪音):翻矛盾照旧。"""
        self._seed_verified()                 # ts=今天,冷却窗内
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1", round_no=2)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")

    def test_jitter_alarm_logged_in_existing_kind(self):
        """抖动:30 天内 3 次翻转(>阈值 2)-> 判决 payload 挂报警(不加新 kind)。"""
        self._seed_verified()
        now = datetime.now(timezone.utc)
        # 造满 3 条近 30 天 transition(现有 1 条 + 手工补 2 条;ts 字符串比较
        # 与 ledger._now 同 ISO 形态)
        for delta in (2, 1):
            ts = (now - timedelta(days=delta)).isoformat()
            self.led.conn.execute(
                "INSERT INTO claim_transitions(claim_id,old_status,new_status,"
                "cause,evidence_id,round,ts) VALUES('evt_t:C1','已证实','待证实',"
                "'状态刷新','',2,?)", (ts,))
        self.led.conn.commit()
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        self._engine(prov).verify("evt_t:C1", 2)
        p = self._last_payload()
        self.assertIn("jitter_alarm", p)       # kind='判决' 枚举内(不加新 kind)
        self.assertEqual(p["jitter_alarm"]["flips_30d"], 3)
        self.assertEqual(p["jitter_alarm"]["threshold"], 2)

    def test_oscillating_sequence_single_flip(self):
        """震荡验收(施工单门禁5,方案 §九 D4 验收标准):民间证据交替攻击
        反复注入 -> 全程仅首次判决一次翻转(冷却窗拦后续同强度反转),
        证据噪音不再晃动终态。"""
        self._seed_verified()                  # 唯一翻转=首次判决 transition
        n0 = len(self.led.conn.execute(
            "SELECT 1 FROM claim_transitions WHERE claim_id='evt_t:C1'"
        ).fetchall())
        for rnd in range(2, 6):
            eid = add_evidence(self.led, "evt_t", f"forum_x{rnd}",
                               f"网友说法{rnd}", "C1", round_no=rnd,
                               author_type="自媒体", credibility="低")
            eid_num = eid.rsplit("E", 1)[-1]
            prov = Scripted([opinion("evt_t:C1", [
                ("evt_t:E1", "支持", "员工爆料工资已发"),
                ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
                (f"evt_t:E{eid_num}", "矛盾", f"网友说法{rnd}")])])
            self._engine(prov).verify("evt_t:C1", rnd)
            self.assertEqual(self.led.claim_row("evt_t:C1")["status"],
                             "已证实")          # 每轮都维持
        n1 = len(self.led.conn.execute(
            "SELECT 1 FROM claim_transitions WHERE claim_id='evt_t:C1'"
        ).fetchall())
        self.assertEqual(n1, n0)               # 震荡序列=零新增翻转(单翻转)


class TestD2ResumeRecheck(FlipBase):
    """D2 缺口1 同 event 复验三分支(施工单验收件)+ 链路无终态过滤锁。"""

    def test_resume_touched_includes_terminal_claims(self):
        """链路锁:已终态 claim 新证据挂账 -> touched_claim_ids 含它
        (实证结论固化:复验入口无终态过滤,S5 挂账面向全部 claim)。"""
        self._seed_verified()
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        touched = self.led.touched_claim_ids("evt_t", 2)
        self.assertIn("evt_t:C1", touched)

    def test_branch1_new_support_maintains(self):
        """分支1:新证据支持已证实 claim -> 维持(无变更零痕迹)。"""
        self._seed_verified()
        add_evidence(self.led, "evt_t", "forum_c", "人社局热线答复已解决", "C1",
                     round_no=2, author_type="官方", credibility="高")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "支持", "人社局热线答复已解决")],
            verdict=ClaimStatus.VERIFIED)])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        self.assertNotIn("flip_guard", self._last_payload())

    def test_branch2_hard_opposition_demotes(self):
        """分支2:新反证硬根(权威)-> 降级过闸(翻矛盾)。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "rsj", "列入欠薪黑名单", "C1", round_no=2)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "列入欠薪黑名单")],
            verdict=ClaimStatus.CONTRADICTED)])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "矛盾")

    def test_branch3_hearsay_opposition_observes(self):
        """分支3:新反证转述级 -> 只观察不翻转(维持已证实+留痕)。"""
        self._seed_verified()
        self._backdate_last_flip(30)
        add_evidence(self.led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        self._engine(prov).verify("evt_t:C1", 2)
        self.assertEqual(self.led.claim_row("evt_t:C1")["status"], "已证实")
        self.assertEqual(self._last_payload()["flip_guard"]["action"],
                         "observe_only")


class TestGuardOffAndMetaPairing(unittest.TestCase):
    """横切:--flip-guard 默认 off 全链零行为 + META_KEYS 配对疫苗。"""

    def test_off_zero_behavior(self):
        """off(None):同场景照旧降级 + payload 无 flip_guard/jitter 键
        (形态与历史版本逐键一致=回放对账稳定)。"""
        led = make_ledger()
        install_simple(led)
        add_evidence(led, "evt_t", "forum_a", "员工爆料工资已发", "C1", round_no=1)
        add_evidence(led, "evt_t", "forum_b", "工会通报欠薪已解决", "C1", round_no=1)
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "支持", "工会通报欠薪已解决")],
            verdict=ClaimStatus.VERIFIED)])
        VerdictEngine(led, AUTH, prov, retry=0, flip_rules=None).verify(
            "evt_t:C1", 1)
        add_evidence(led, "evt_t", "forum_x", "网友称仍在欠薪", "C1",
                     round_no=2, author_type="自媒体", credibility="低")
        prov = Scripted([opinion("evt_t:C1", [
            ("evt_t:E1", "支持", "员工爆料工资已发"),
            ("evt_t:E2", "时效刷新", "工会通报欠薪已解决"),
            ("evt_t:E3", "矛盾", "网友称仍在欠薪")])])
        VerdictEngine(led, AUTH, prov, retry=0, flip_rules=None).verify(
            "evt_t:C1", 2)
        self.assertEqual(led.claim_row("evt_t:C1")["status"], "待证实")
        rows = [json.loads(r["payload_json"]) for r in led.decision_log_rows("evt_t")
                if r["kind"] == "判决"]
        for p in rows:
            self.assertNotIn("flip_guard", p)
            self.assertNotIn("jitter_alarm", p)

    def test_meta_keys_pairing_vaccine(self):
        """flip_guard 入 META_KEYS(配对契约;照 corpus_bound_code 疫苗先例),
        on/off 归一进 _snapshot(回放参数失真=硬错误的前提)。"""
        self.assertIn("flip_guard", META_KEYS)
        import argparse
        args = argparse.Namespace(flip_guard="on")
        snap = _snapshot(args)
        self.assertIs(snap["flip_guard"], True)


if __name__ == "__main__":
    unittest.main()
