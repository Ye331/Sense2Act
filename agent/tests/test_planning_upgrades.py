"""规划层改造单元(2026-10-04):区分度 D(c)/cost(改造②)/矛盾重述拦截(改造①)。

D(c) 语义锚定 hypothesis_standings 的加性记分(已证实+1/矛盾-1),
flip 口径 = dominant(相对主导)翻转的假设个数。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.store import ledger as ledger_mod  # noqa: E402
from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.envelope import InvestigationEnvelope, TenderSignal  # noqa: E402
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    GapDraft,
    HypothesisSeed,
)
from sense2act.store import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

TEXT_A = ("线索 k3 载明:新华社沈阳8月31日电,由大连化物所联合科大讯飞共同研发的"
          "智能化工大模型3.0Pro正式对外发布。")
TEXT_A_RESTATE = TEXT_A.replace("线索 k3 载明", "线索 k9 载明")   # 高相似换卡号重述
TEXT_B = "测试医院上一年度信息化预算总额为 3000 万元,由院务会审议通过。"
TEXT_C = "测试医院信息科启动 PACS 系统升级选型调研。"
TEXT_D = "与本案无关的另一条独立事实陈述,内容完全不同。"


def _blueprint(texts: list[str],
               hypotheses: list[HypothesisSeed] | None = None,
               ids: list[str] | None = None) -> AnalysisBlueprint:
    claim_ids = ids or [f"C{i+1}" for i in range(len(texts))]
    claims = [ClaimDraft(id=cid, text=t, category=ClaimCategory.FACT,
                         section_index=2)
              for cid, t in zip(claim_ids, texts)]
    hyps = hypotheses if hypotheses is not None else [
        HypothesisSeed(id="H0", label="正常经营", claim_ids=[])]
    return AnalysisBlueprint(claims=claims, hypotheses=hyps,
                             gaps=[GapDraft(text="预算明细")])


def _new_ledger(tmp: str, event_id: str = "evt_pu") -> LedgerRepository:
    led = LedgerRepository(connect(Path(tmp) / "t.db"), ROOT / "data" / "config")
    env = InvestigationEnvelope(kind="tender", event_id="", tender=TenderSignal(
        project_id="MEDAI-2026-001", project_name="测试采购项目",
        purchaser="测试医院", published_date="2026-09-20"))
    led.register_event(env, signal_type="商机", event_id=event_id)
    return led


def _entry_logs(led: LedgerRepository) -> list[dict]:
    rows = led.conn.execute(
        "select payload_json from decision_log where kind='入账'").fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


class TestDiscriminativeness(unittest.TestCase):
    """改造②:D(c) 反事实战况扰动。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_fresh_two_hypotheses_basic_flip(self):
        """全待证实起步:查实任一成员使该假设升为主导 -> plus=1;证伪不动格局 -> minus=0。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A, TEXT_B],
                hypotheses=[HypothesisSeed(id="H1", label="假设一", claim_ids=["C1"]),
                            HypothesisSeed(id="H2", label="假设二", claim_ids=["C2"])]))
            disc = led.claim_discriminativeness("evt_pu")
            self.assertEqual(disc["C1"]["plus"], 1)
            self.assertEqual(disc["C1"]["minus"], 0)
            self.assertEqual(disc["C1"]["cost"], 1)      # 无证据 -> max(1, 0)
            self.assertEqual(disc["C1"]["ratio"], 1.0)
        finally:
            led.conn.close()

    def test_pillar_contradiction_kills_dominant(self):
        """承重柱证伪:H1(已证实+待证实)主导;证伪其待证实成员 -> H1 掉出主导 -> minus=1。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A, TEXT_B, TEXT_C],
                hypotheses=[HypothesisSeed(id="H1", label="假设一",
                                           claim_ids=["C1", "C2"]),
                            HypothesisSeed(id="H2", label="假设二", claim_ids=["C3"])]))
            led.update_claim_status("evt_pu:C1", "已证实", None, 0)
            disc = led.claim_discriminativeness("evt_pu")
            self.assertEqual(disc["C2"]["minus"], 1)     # 证伪承重柱,H1 失去主导
            self.assertEqual(disc["C2"]["plus"], 0)      # 再证实只是巩固,格局不变
            self.assertEqual(disc["C3"]["plus"], 1)      # H2 从 0 升 1,与 H1 并列主导
        finally:
            led.conn.close()

    def test_orphan_claim_zero_discrimination(self):
        """不挂任何假设的 claim(背景/复述型)-> D=0,自然落入低优先。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A, TEXT_D],
                hypotheses=[HypothesisSeed(id="H1", label="假设一", claim_ids=["C1"])]))
            disc = led.claim_discriminativeness("evt_pu")
            self.assertEqual(disc["C2"]["plus"], 0)
            self.assertEqual(disc["C2"]["minus"], 0)
            self.assertEqual(disc["C2"]["ratio"], 0.0)
        finally:
            led.conn.close()

    def test_cost_counts_ruling_evidence(self):
        """cost = max(1, 名下裁定证据条数);ratio 随成本下降。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A],
                hypotheses=[HypothesisSeed(id="H1", label="假设一", claim_ids=["C1"])]))
            led.conn.execute(
                "INSERT INTO claim_evidence(claim_id,evidence_id,ruling,round)"
                " VALUES('evt_pu:C1','evt_pu:E1','支持',1)")
            led.conn.execute(
                "INSERT INTO claim_evidence(claim_id,evidence_id,ruling,round)"
                " VALUES('evt_pu:C1','evt_pu:E2','支持',1)")
            led.conn.commit()
            disc = led.claim_discriminativeness("evt_pu")
            self.assertEqual(disc["C1"]["cost"], 2)
            self.assertEqual(disc["C1"]["ratio"], 0.5)   # (1+0)/2
        finally:
            led.conn.close()

    def test_event_summary_disc_only_for_pending(self):
        """event_summary:待证实附 disc,已证实不附(S2 主目标只能选未决)。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A, TEXT_B],
                hypotheses=[HypothesisSeed(id="H1", label="假设一",
                                           claim_ids=["C1", "C2"])]))
            led.update_claim_status("evt_pu:C1", "已证实", None, 0)
            s = led.event_summary("evt_pu")
            pend = {e["id"]: e for e in s["claims_by_status"].get("待证实", [])}
            verf = {e["id"]: e for e in s["claims_by_status"].get("已证实", [])}
            self.assertIn("disc", pend["C2"])
            self.assertEqual(pend["C2"]["disc"]["minus"], 1)   # 证伪承重柱,H1 掉主导
            self.assertEqual(pend["C2"]["disc"]["plus"], 0)    # 再证实只是巩固
            self.assertNotIn("disc", verf["C1"])
        finally:
            led.conn.close()

    def test_event_summary_disc_gate_off(self):
        """include_disc=False=盲规划对照(off 臂):摘要任何条目都不含 disc。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A, TEXT_B],
                hypotheses=[HypothesisSeed(id="H1", label="假设一",
                                           claim_ids=["C1", "C2"])]))
            s = led.event_summary("evt_pu", include_disc=False)
            for status_entries in s["claims_by_status"].values():
                for e in status_entries:
                    self.assertNotIn("disc", e)
        finally:
            led.conn.close()


class TestContradictionRestateFilter(unittest.TestCase):
    """改造①:蓝图安装时拦截已矛盾 claim 的换措辞重述。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_restatement_blocked_and_audited(self):
        """C1 被判矛盾后,续查蓝图重述同一断言 -> 不登记、审计留痕、归属丢弃。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A],
                hypotheses=[HypothesisSeed(id="H0", label="正常经营",
                                           claim_ids=["C1"])]))
            led.update_claim_status("evt_pu:C1", "矛盾", "矛盾翻转", 1)
            led.install_blueprint("evt_pu", _blueprint(
                [TEXT_A_RESTATE],
                hypotheses=[HypothesisSeed(id="H0", label="正常经营",
                                           claim_ids=["C2"])],
                ids=["C2"]))
            n = led.conn.execute("select count(*) from claims").fetchone()[0]
            self.assertEqual(n, 1)                       # 重述未入账
            entries = _entry_logs(led)
            blocked = [b for e in entries for b in (e.get("contradiction_blocked") or [])]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0]["draft"], "C2")
            self.assertEqual(blocked[0]["matched_claim"], "evt_pu:C1")
        finally:
            led.conn.close()

    def test_distinct_claim_allowed_after_contradiction(self):
        """矛盾后安装的不同事实照常入账(过滤器不误伤)。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint([TEXT_A]))
            led.update_claim_status("evt_pu:C1", "矛盾", "矛盾翻转", 1)
            led.install_blueprint("evt_pu", _blueprint([TEXT_D], ids=["C2"]))
            n = led.conn.execute("select count(*) from claims").fetchone()[0]
            self.assertEqual(n, 2)
            entries = _entry_logs(led)
            blocked = [b for e in entries for b in (e.get("contradiction_blocked") or [])]
            self.assertEqual(blocked, [])
        finally:
            led.conn.close()

    def test_fresh_install_no_contradiction_no_effect(self):
        """无矛盾史的首装:拦截线为空(既有 fixture 零影响的安全证明)。"""
        led = _new_ledger(self._tmp.name)
        try:
            led.install_blueprint("evt_pu", _blueprint([TEXT_A, TEXT_B]))
            entries = _entry_logs(led)
            blocked = [b for e in entries for b in (e.get("contradiction_blocked") or [])]
            self.assertEqual(blocked, [])
        finally:
            led.conn.close()

    def test_theta2_threshold_semantics(self):
        """θ2 是唯一开关:高阈值放行中相似重述,低阈值拦截。"""
        texts = [TEXT_A, TEXT_A_RESTATE]   # 相似度同去重定标对(k3/k9),约 0.98
        original = ledger_mod.CLAIM_CONTRA_REJECT_SIM
        try:
            ledger_mod.CLAIM_CONTRA_REJECT_SIM = 0.99   # 高于该对 -> 放行
            with tempfile.TemporaryDirectory() as t1:
                led = _new_ledger(t1)
                try:
                    led.install_blueprint("evt_pu", _blueprint([TEXT_A]))
                    led.update_claim_status("evt_pu:C1", "矛盾", "矛盾翻转", 1)
                    led.install_blueprint("evt_pu", _blueprint(
                        [TEXT_A_RESTATE],
                        hypotheses=[HypothesisSeed(id="H1", label="假设一",
                                                   claim_ids=["C2"])],
                        ids=["C2"]))
                    n = led.conn.execute(
                        "select count(*) from claims").fetchone()[0]
                    self.assertEqual(n, 2)               # 重述入账(阈值放行)
                finally:
                    led.conn.close()

            ledger_mod.CLAIM_CONTRA_REJECT_SIM = 0.5    # 低于 -> 拦截
            with tempfile.TemporaryDirectory() as t2:
                led = _new_ledger(t2)
                try:
                    led.install_blueprint("evt_pu", _blueprint([TEXT_A]))
                    led.update_claim_status("evt_pu:C1", "矛盾", "矛盾翻转", 1)
                    led.install_blueprint("evt_pu", _blueprint(
                        [TEXT_A_RESTATE], ids=["C2"]))
                    n = led.conn.execute(
                        "select count(*) from claims").fetchone()[0]
                    self.assertEqual(n, 1)               # 重述被拦截
                finally:
                    led.conn.close()
        finally:
            ledger_mod.CLAIM_CONTRA_REJECT_SIM = original


if __name__ == "__main__":
    unittest.main()
