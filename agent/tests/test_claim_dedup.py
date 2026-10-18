"""蓝图确定性去重单元:同稿重复合并 / 不同事实不误并 / 假设归属 remap / 阈值。"""
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

DUPE_A = ("线索 k3 载明:新华社沈阳8月31日电(记者胡喆、王莹)8月31日,"
          "由中国科学院大连化学物理研究所联合科大讯飞、阿里云等单位共同研发的"
          "智能化工大模型3.0Pro正式对外发布。")
DUPE_B = ("线索 k4 载明:新华社沈阳8月31日电(记者胡喆、王莹)8月31日,"
          "由中国科学院大连化学物理研究所联合科大讯飞、阿里云等单位共同研发的"
          "智能化工大模型3.0Pro正式对外发布。")          # 仅线索卡号不同 -> 高相似
DISTINCT = "测试医院上一年度信息化预算总额为 3000 万元,由院务会审议通过。"


def _blueprint(texts: list[str], hyp_claims: list[str] | None = None):
    claims = [ClaimDraft(id=f"C{i+1}", text=t, category=ClaimCategory.FACT,
                         section_index=2)
              for i, t in enumerate(texts)]
    hypotheses = [HypothesisSeed(id="H0", label="正常经营",
                                 claim_ids=hyp_claims or [])]
    return AnalysisBlueprint(claims=claims, hypotheses=hypotheses,
                             gaps=[GapDraft(text="预算明细")])


def _ledger_and_install(tmp: str, bp: AnalysisBlueprint) -> LedgerRepository:
    led = LedgerRepository(connect(Path(tmp) / "t.db"), ROOT / "data" / "config")
    env = InvestigationEnvelope(kind="tender", event_id="", tender=TenderSignal(
        project_id="MEDAI-2026-001", project_name="测试采购项目",
        purchaser="测试医院", published_date="2026-09-20"))
    led.register_event(env, signal_type="商机", event_id="evt_dd")
    led.install_blueprint("evt_dd", bp)
    return led


def _merge_logs(led: LedgerRepository) -> list[dict]:
    rows = led.conn.execute(
        "select payload_json from decision_log where kind='入账'").fetchall()
    out: list[dict] = []
    for r in rows:
        out.extend(json.loads(r["payload_json"]).get("dedup_merged") or [])
    return out


class TestBlueprintDedup(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_near_duplicate_merged_and_logged(self):
        """同稿重复(仅卡号不同)→ 只入首条,decision_log 留痕。"""
        led = _ledger_and_install(self._tmp.name,
                                  _blueprint([DUPE_A, DUPE_B, DISTINCT]))
        try:
            rows = led.conn.execute(
                "select draft_id from claims order by draft_id").fetchall()
            self.assertEqual([r["draft_id"] for r in rows], ["C1", "C3"])
            logs = _merge_logs(led)
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["dropped"], "C2")
            self.assertEqual(logs[0]["kept"], "C1")
        finally:
            led.conn.close()

    def test_distinct_claims_all_kept(self):
        """不同事实不误并:无去重记录。"""
        led = _ledger_and_install(self._tmp.name,
                                  _blueprint([DUPE_A, DISTINCT]))
        try:
            n = led.conn.execute("select count(*) from claims").fetchone()[0]
            self.assertEqual(n, 2)
            self.assertEqual(_merge_logs(led), [])
        finally:
            led.conn.close()

    def test_hypothesis_claim_ids_remap(self):
        """被并 claim 的假设归属转给保留条。"""
        led = _ledger_and_install(
            self._tmp.name, _blueprint([DUPE_A, DUPE_B], hyp_claims=["C2"]))
        try:
            rows = led.conn.execute(
                "select claim_id from hypothesis_claims").fetchall()
            self.assertEqual([r["claim_id"] for r in rows], ["evt_dd:C1"])
        finally:
            led.conn.close()

    def test_threshold_semantics(self):
        """阈值语义:同一对文本,调高阈值不并、调低则并(θ 是唯一开关)。"""
        texts = [DUPE_A, DUPE_B]
        original = ledger_mod.CLAIM_DEDUP_SIM
        try:
            ledger_mod.CLAIM_DEDUP_SIM = 0.99   # 高于该对实际相似度 -> 不并
            with tempfile.TemporaryDirectory() as t1:
                led = _ledger_and_install(t1, _blueprint(texts))
                try:
                    n = led.conn.execute(
                        "select count(*) from claims").fetchone()[0]
                    self.assertEqual(n, 2)
                finally:
                    led.conn.close()

            ledger_mod.CLAIM_DEDUP_SIM = 0.5    # 低于 -> 并
            with tempfile.TemporaryDirectory() as t2:
                led = _ledger_and_install(t2, _blueprint(texts))
                try:
                    n = led.conn.execute(
                        "select count(*) from claims").fetchone()[0]
                    self.assertEqual(n, 1)
                finally:
                    led.conn.close()
        finally:
            ledger_mod.CLAIM_DEDUP_SIM = original


if __name__ == "__main__":
    unittest.main()
