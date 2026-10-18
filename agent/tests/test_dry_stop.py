"""死轮兜底停单元:round_progress 计量 + 合成回放验证连续零增量→代码兜底停。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sense2act.models.base import ClaimCategory  # noqa: E402
from sense2act.models.intel import ClaimDraft  # noqa: E402
from sense2act.store import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402
from helpers import make_ledger  # noqa: E402

ENVELOPE = ROOT / "examples" / "tender_medical_ai" / "envelope.json"


class TestRoundProgress(unittest.TestCase):

    def test_counts_new_evidence_and_claims(self):
        """信息增量 = 本轮 evidence 行 + 本轮 created_round 的 claim 行。"""
        led = LedgerRepository(connect(":memory:"), ROOT / "data" / "config")
        try:
            env = json.loads(
                (ROOT / "examples" / "tender_medical_ai" / "envelope.json")
                .read_text(encoding="utf-8-sig"))
            from sense2act.models.envelope import InvestigationEnvelope
            led.register_event(InvestigationEnvelope.model_validate(env),
                               signal_type="商机", event_id="evt_rp")
            led.install_blueprint("evt_rp", _blueprint())
            self.assertEqual(led.round_progress("evt_rp", 0), (0, 2))  # 蓝图 2 条
            eid = led.insert_evidence("evt_rp", {
                "platform": "p", "source": "s", "title": "t",
                "content": "正文内容", "lineage_root": "s"}, round_no=1)
            self.assertIsNotNone(eid)
            self.assertEqual(led.round_progress("evt_rp", 1), (1, 0))
            led.add_claim("evt_rp", {"id": "D1", "text": "派生条",
                                     "category": "事实", "section_index": 2},
                          round_no=1)
            self.assertEqual(led.round_progress("evt_rp", 1), (1, 1))
            self.assertEqual(led.round_progress("evt_rp", 2), (0, 0))
        finally:
            led.conn.close()


def _blueprint():
    from sense2act.models.intel import AnalysisBlueprint, GapDraft, HypothesisSeed
    return AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="测试医院采购 AI 辅助诊断系统",
                       category=ClaimCategory.FACT, section_index=2),
            ClaimDraft(id="C2", text="该项目预算为 480 万元",
                       category=ClaimCategory.FACT, section_index=2),
        ],
        hypotheses=[HypothesisSeed(id="H0", label="正常经营")],
        gaps=[GapDraft(text="需求细节")])


def _s1a() -> dict:
    return {"stage": "s1a", "response": {
        "signalType": "商机", "reason": "测试定性",
        "selectedSeedLabels": [], "basisGaps": []}}


def _s1b() -> dict:
    bp = _blueprint().model_dump(by_alias=True)
    return {"stage": "s1b", "response": bp}


def _s2(round_no: int) -> dict:
    return {"stage": "s2", "response": {
        "round": round_no, "mainTarget": "C1", "action": "查工具",
        "gapChain": [{"gap": "需求与预算原文", "acceptance": "页面正文级证据",
                      "failover": "换检索词"}],
        "searchHints": ["医院 采购"], "sideTargets": [],
        "stopReason": "", "degraded": False}}


def _s5() -> dict:
    return {"stage": "s5_derive", "response": {"derivedClaims": [],
                                               "cohorts": []}}


def _write(tmp: str, n_rounds: int) -> tuple[Path, Path]:
    rec = Path(tmp) / "recording.json"
    entries = [_s1a(), _s1b()]
    for i in range(1, n_rounds + 1):
        entries.append(_s2(i))
        entries.append(_s5())
    rec.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    seed = Path(tmp) / "seed.json"
    seed.write_text("{}", encoding="utf-8")     # 空种子:采集全程零收获
    return rec, seed


class TestDryStopReplay(unittest.TestCase):
    """合成回放:空种子 → 每轮零信息增量 → 连续 3 轮触发代码兜底停。"""

    @staticmethod
    def _run_cli(rec: Path, seed: Path, db: Path, extra: list[str]) -> dict:
        out = Path(db).parent / "runs_out"
        proc = subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate",
             "--envelope", str(ENVELOPE), "--replay", str(rec),
             "--seed", str(seed), "--db", str(db), "--runs-dir", str(out),
             *extra],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
        result = {}
        rp = out / "evt_drystop" / "result.json"
        if rp.exists():
            result = json.loads(rp.read_text(encoding="utf-8-sig"))
        return {"returncode": proc.returncode, "stderr": proc.stderr,
                "result": result}

    def test_dry_stop_fires_at_three(self):
        """连续 3 轮零增量 → 代码兜底停,不烧到轮数上限。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _write(tmp, n_rounds=4)   # 4 轮决策供给,只应消耗 3
            r = self._run_cli(rec, seed, Path(tmp) / "t.db",
                              ["--event-id", "evt_drystop"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            self.assertIn("证据通道已穷尽", r["result"].get("stop_reason", ""))
            self.assertIn("代码兜底", r["result"].get("stop_reason", ""))

    def test_switch_off_runs_to_round_cap(self):
        """--dry-stop-rounds 0 = 关:同场景烧到轮数上限(对照臂)。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed = _write(tmp, n_rounds=6)
            r = self._run_cli(rec, seed, Path(tmp) / "t.db",
                              ["--event-id", "evt_drystop",
                               "--dry-stop-rounds", "0"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            self.assertIn("轮数上限", r["result"].get("stop_reason", ""))


if __name__ == "__main__":
    unittest.main()
