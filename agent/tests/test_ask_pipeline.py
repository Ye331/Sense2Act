"""主动澄清全链 e2e(合成回放,模板=test_dry_stop.py):
P0 答案传导 / 人答 meta 回放 / 配对失败 / 门1 拦截 / 旧件零扰动。"""
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
from sense2act.models.intel import (  # noqa: E402
    AnalysisBlueprint,
    ClaimDraft,
    GapDraft,
    HypothesisSeed,
)
from sense2act.run_meta import meta_path  # noqa: E402
from sense2act.store import connect  # noqa: E402
from sense2act.store.ledger import LedgerRepository  # noqa: E402

ENVELOPE = ROOT / "examples" / "tender_medical_ai" / "envelope.json"
EID = "evt_askpipe"
QUESTION = "按哪个口径继续调查?"


def _blueprint() -> AnalysisBlueprint:
    """H0(内定嫌疑,成员 C1/C3)/ H1(程序合规,成员 C2)的真分叉。"""
    return AnalysisBlueprint(
        claims=[
            ClaimDraft(id="C1", text="测试医院采购项目被内定给A公司",
                       category=ClaimCategory.FACT, section_index=2),
            ClaimDraft(id="C2", text="测试医院采购项目公开招标程序合规",
                       category=ClaimCategory.FACT, section_index=2),
            ClaimDraft(id="C3", text="测试医院采购仅一家投标",
                       category=ClaimCategory.FACT, section_index=2),
        ],
        hypotheses=[
            HypothesisSeed(id="H0", label="内定嫌疑", claim_ids=["C1", "C3"]),
            HypothesisSeed(id="H1", label="程序合规", claim_ids=["C2"]),
        ],
        gaps=[GapDraft(text="中标公告原文")])


def _s1a() -> dict:
    return {"stage": "s1a", "response": {
        "signalType": "风险", "reason": "测试定性",
        "selectedSeedLabels": [], "basisGaps": []}}


def _s1b() -> dict:
    return {"stage": "s1b", "response": _blueprint().model_dump(by_alias=True)}


def _s2_tool(round_no: int, target: str = "C2") -> dict:
    return {"stage": "s2", "response": {
        "round": round_no, "mainTarget": target, "action": "查工具",
        "gapChain": [{"gap": "中标公告原文", "acceptance": "页面正文级证据",
                      "failover": "换检索词"}],
        "searchHints": ["医院 采购"], "sideTargets": [],
        "stopReason": "", "degraded": False}}


def _s2_ask(round_no: int, target: str = "C2") -> dict:
    return {"stage": "s2", "response": {
        "round": round_no, "mainTarget": target, "action": "问人",
        "gapChain": [{"gap": "口径分叉:内定 vs 程序合规",
                      "acceptance": "委托方裁决口径", "failover": ""}],
        "searchHints": [], "sideTargets": [], "stopReason": "",
        "degraded": False,
        "questionPackage": {
            "question": QUESTION,
            "options": ["甲", "乙"],
            "evidenceSummary": "两条线索各支持一个假设",
            "adjudicationTarget": {
                "kind": "hypothesis",
                "map": [{"option": "甲", "targetId": "H1"},
                        {"option": "乙", "targetId": "H0"}]}}}}


def _s5() -> dict:
    return {"stage": "s5_derive", "response": {"derivedClaims": [],
                                               "cohorts": []}}


def _write(tmp: str, name: str, entries: list[dict]) -> Path:
    rec = Path(tmp) / name
    rec.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return rec


def _seed(tmp: str) -> Path:
    seed = Path(tmp) / "seed.json"
    seed.write_text("{}", encoding="utf-8")   # 空种子:采集零收获
    return seed


def _run(tmp: str, rec: Path, seed: Path, extra: list[str]) -> dict:
    db = Path(tmp) / "t.db"
    out = Path(tmp) / "runs_out"
    proc = subprocess.run(
        [sys.executable, "-m", "sense2act", "investigate",
         "--envelope", str(ENVELOPE), "--replay", str(rec),
         "--seed", str(seed), "--db", str(db), "--runs-dir", str(out),
         "--event-id", EID, *extra],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120)
    result = {}
    rp = out / EID / "result.json"
    if rp.exists():
        result = json.loads(rp.read_text(encoding="utf-8-sig"))
    return {"returncode": proc.returncode, "stderr": proc.stderr,
            "result": result, "db": db}


def _ledger(db: Path) -> LedgerRepository:
    return LedgerRepository(connect(db), ROOT / "data" / "config")


def _adjudication_rows(db: Path) -> list[dict]:
    led = _ledger(db)
    try:
        return [dict(r) for r in led.conn.execute(
            "SELECT * FROM adjudications ORDER BY id").fetchall()]
    finally:
        led.conn.close()


def _clarify_modes(db: Path) -> list[str]:
    """decision_log 里 kind='澄清' 各行的 mode 序列(按落账顺序)。"""
    led = _ledger(db)
    try:
        out = []
        for r in led.decision_log_rows(EID):
            if r["kind"] == "澄清":
                out.append(json.loads(r["payload_json"]).get("mode"))
        return out
    finally:
        led.conn.close()


class TestAskHeadlessChain(unittest.TestCase):
    """① headless 全链:S2 问人→自动首选项→裁决落账→standings 翻转→claims 不动。"""

    def test_full_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = _write(tmp, "rec.json", [
                _s1a(), _s1b(), _s2_ask(1), _s2_tool(2), _s5()])
            r = _run(tmp, rec, _seed(tmp), ["--max-rounds", "2"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            rows = _adjudication_rows(r["db"])
            self.assertEqual(len(rows), 2)
            by_target = {row["target_id"]: row["selected"] for row in rows}
            self.assertEqual(by_target[f"{EID}:H1"], 1)   # 首选项甲→H1 选中
            self.assertEqual(by_target[f"{EID}:H0"], 0)
            # P0:裁决行 + 审计行(headless_auto 问答 + human_adjudication 应用)
            self.assertIn("headless_auto", _clarify_modes(r["db"]))
            self.assertIn("human_adjudication", _clarify_modes(r["db"]))
            # standings 翻转;claims 现态零变化(分权)
            led = _ledger(r["db"])
            try:
                st = {s["hypothesis_id"].rsplit(":", 1)[-1]: s
                      for s in led.hypothesis_standings(EID)}
                self.assertEqual(st["H1"]["score"], 1)
                self.assertTrue(st["H1"]["dominant"])
                self.assertEqual(st["H0"]["score"], -2)   # H0 成员 C1/C3 各 -1
                for cid in ("C1", "C2", "C3"):
                    self.assertEqual(
                        led.claim_row(f"{EID}:{cid}")["status"], "待证实")
            finally:
                led.conn.close()


class TestAskMetaReplay(unittest.TestCase):
    """② 人答回放:meta.ask_answers 供给非首选项;③ 问题不符 → rc=2。"""

    def _meta(self, tmp: str, rec: Path, answers: list[dict]) -> None:
        meta = {"variant": "中", "max_rounds": 2,
                "ask_mode": "interactive", "ask_answers": answers}
        meta_path(rec).write_text(json.dumps(meta, ensure_ascii=False),
                                  encoding="utf-8")

    def test_replay_uses_recorded_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = _write(tmp, "rec.json", [
                _s1a(), _s1b(), _s2_ask(1), _s2_tool(2), _s5()])
            self._meta(tmp, rec, [
                {"round": 1, "question": QUESTION, "answer": "乙"}])
            r = _run(tmp, rec, _seed(tmp),
                     ["--max-rounds", "2", "--ask-mode", "interactive"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            rows = _adjudication_rows(r["db"])
            by_target = {row["target_id"]: row["selected"] for row in rows}
            self.assertEqual(by_target[f"{EID}:H0"], 1)   # 人答乙→H0 选中(非首选项)
            self.assertIn("replay", _clarify_modes(r["db"]))
            self.assertIn("human_adjudication", _clarify_modes(r["db"]))

    def test_question_mismatch_hard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = _write(tmp, "rec.json", [
                _s1a(), _s1b(), _s2_ask(1), _s2_tool(2), _s5()])
            self._meta(tmp, rec, [
                {"round": 1, "question": "另一个问题?", "answer": "甲"}])
            r = _run(tmp, rec, _seed(tmp),
                     ["--max-rounds", "2", "--ask-mode", "interactive"])
            self.assertEqual(r["returncode"], 2)
            self.assertIn("人答回放错位", r["stderr"])


class TestAskGateE2E(unittest.TestCase):
    """④ 门1 e2e:轮 1 刷新使 H0=2/H1=0,轮 2 问人被拦改写查工具;off 臂放行。"""

    def _gate_fixture(self, tmp: str) -> tuple[Path, Path, Path]:
        # 注意:空种子下 S5 派生器零采集短路、不消费 s5_derive 条目,
        # 录制件不放 _s5(否则轮 2 的 s2 撞上它=回放错位→planner 降级)
        rec = _write(tmp, "rec.json", [
            _s1a(), _s1b(), _s2_tool(1), _s2_ask(2)])
        refresh = Path(tmp) / "refresh.json"
        refresh.write_text(json.dumps(
            {"1": [{"draft_id": "C1", "new_status": "已证实"},
                   {"draft_id": "C3", "new_status": "已证实"}]},
            ensure_ascii=False), encoding="utf-8")
        return rec, _seed(tmp), refresh

    def test_gate_on_blocks_ask_and_continues_s3(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed, refresh = self._gate_fixture(tmp)
            r = _run(tmp, rec, seed, ["--max-rounds", "2",
                                      "--refresh-plan", str(refresh)])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            self.assertEqual(_adjudication_rows(r["db"]), [])  # 问询被拦,未问
            self.assertIn("askgate_blocked", _clarify_modes(r["db"]))
            # 改写单落当轮 S3:轮 2 照常走了采集→入账(s5_derive 被消费)
            led = _ledger(r["db"])
            try:
                rounds = {r["round"] for r in led.decision_log_rows(EID)
                          if r["kind"] == "入账"}
                self.assertIn(2, rounds)
            finally:
                led.conn.close()

    def test_gate_off_allows_ask(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec, seed, refresh = self._gate_fixture(tmp)
            r = _run(tmp, rec, seed, ["--max-rounds", "2",
                                      "--refresh-plan", str(refresh),
                                      "--ask-gate", "off"])
            self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
            rows = _adjudication_rows(r["db"])
            self.assertEqual(len(rows), 2)                 # 问询发生
            by_target = {row["target_id"]: row["selected"] for row in rows}
            self.assertEqual(by_target[f"{EID}:H1"], 1)


class TestZeroDisturbance(unittest.TestCase):
    """⑤ 零扰动:旧式序列(无 adjudicationTarget)在门 on/off 下台账逐条相同。"""

    def test_legacy_fixture_identical_under_gate_on_off(self):
        entries = [_s1a(), _s1b(), _s2_tool(1), _s2_tool(2)]
        snapshots = []
        for gate in ("on", "off"):
            with tempfile.TemporaryDirectory() as tmp:
                rec = _write(tmp, "rec.json", entries)
                r = _run(tmp, rec, _seed(tmp),
                         ["--max-rounds", "2", "--ask-gate", gate])
                self.assertEqual(r["returncode"], 0, r["stderr"][-400:])
                led = _ledger(r["db"])
                try:
                    snapshots.append({
                        "log": [(x["round"], x["kind"], x["payload_json"])
                                for x in led.decision_log_rows(EID)],
                        "claims": [(x["draft_id"], x["status"])
                                   for x in led.all_claims(EID)],
                        "adjudications": _adjudication_rows(r["db"]),
                    })
                finally:
                    led.conn.close()
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[0]["adjudications"], [])


if __name__ == "__main__":
    unittest.main()
