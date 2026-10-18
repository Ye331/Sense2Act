"""装配器单元:上游 Top-K 批量信号 → 信封(白名单映射/合成句/门禁/双入口)。"""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sense2act import assemble as asm  # noqa: E402
from sense2act.models.envelope import InvestigationEnvelope  # noqa: E402

BATCH_PATH = ROOT / "examples" / "upstream_topk" / "agent_top_k_samples.json"
TENDER_ENVELOPE = ROOT / "examples" / "tender_medical_ai" / "envelope.json"
SUBMIT_TENDER = ROOT / "examples" / "upstream_submit" / "tender_sample.json"
SUBMIT_RUMOR = ROOT / "examples" / "upstream_submit" / "rumor_sample.json"


def v2_tender() -> dict:
    return {
        "signal_id": "SIG-T-1", "company_id": "C-1", "kind": "tender",
        "payload": {
            "project_id": "R-raw-1", "procurement_project_id": "PRJ-1",
            "title": "测试采购项目", "buyer": "测试医院",
            "publish_date": "2026-10-03", "amount_yuan": 1000000,
            "project_summary": "摘要", "source_url": "https://example.org/a",
            "signal_reason": "语义相似度 0.65 达阈值;金额为中位数 5.0 倍(样本 4)。",
            "relevance_score": 0.65, "amount_score": 0.8,
            "novelty_score": None, "frequency_score": 0.6,
            "amount_reliable": True, "novelty_reliable": True,
            "frequency_reliable": False, "category": "测试领域",
        },
    }


def v2_rumor() -> dict:
    return {
        "signal_id": "SIG-R-1", "company_id": "C-1", "kind": "rumor",
        "payload": {
            "subject_name": "测试主体", "subject_credit_code": "",
            "summary": "待核实说法", "source": "toutiao.com",
            "signal_time": "2026-10-03T10:00:00+08:00",
            "watch_focus": "核实说法",
            "clues": [{"clue_id": "k1", "source": "新华网", "platform": "toutiao",
                       "title": "标题", "content": "正文文本",
                       "url": "https://example.org/n1",
                       "published_at": "2026-10-03T09:00:00+08:00",
                       "author_type": "媒体"}],
        },
    }


def load_batch_dict() -> dict:
    return json.loads(BATCH_PATH.read_text(encoding="utf-8-sig"))


def min_signal() -> dict:
    """最小上游信号(全字段齐备,可靠=true/充分;个别用例就地改坏它)。"""
    return {
        "project": {"project_id": "R-test1", "title": "测试采购项目",
                    "buyer": "测试医院", "publish_date": "2026-09-09",
                    "amount_yuan": 1000000.0, "category": "测试领域",
                    "project_summary": "测试摘要"},
        "scores": {"relevance_score": 0.7, "amount_score": 0.9,
                   "novelty_score": 0.1, "frequency_score": 0.5},
        "historical_context": {
            "amount": {"historical_median_amount_yuan": 200000.0,
                       "amount_ratio": 5.0, "history_count": 4,
                       "simulated_history_count": 0, "reliable": True},
            "novelty": {"history_count": 4, "simulated_history_count": 0,
                        "max_history_similarity": 0.8, "reliable": False,
                        "most_similar_project": {
                            "project_id": "R-old", "title": "旧项目",
                            "publish_date": "2025-01-01",
                            "data_origin": "real", "source_url": None}},
            "frequency": {"window_days": 30, "recent_count": 2,
                          "historical_window_average_count": 1.0,
                          "frequency_ratio": 2.0,
                          "historical_windows_used": 4, "reliable": True}},
        "trigger": {"relevance_threshold": 0.6, "evidence_status": "insufficient"},
        "sources": {"source_url": "https://example.org/a",
                    "content_source_note": "未抓取正文"},
        "scoring_context": {"company_profile_used": "医疗AI"},
    }


class TestLoadBatch(unittest.TestCase):

    def test_load_frozen_fixture(self):
        """冻结样例可加载:归一为 {signals:[...]},v1 条目形状保留。"""
        batch = asm.load_batch(BATCH_PATH)
        self.assertEqual(len(batch["signals"]), 5)
        self.assertIn("project", batch["signals"][0])       # v1 形状
        self.assertIn("scores", batch["signals"][0])

    def test_reject_unknown_schema_version(self):
        """schema_version 非 0.1 前缀 → ValueError(防静默错配)。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.json"
            p.write_text(json.dumps({"schema_version": "0.2-alpha",
                                     "signals": [min_signal()]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                asm.load_batch(p)

    def test_reject_empty_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.json"
            p.write_text(json.dumps({"schema_version": "0.1-draft",
                                     "signals": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                asm.load_batch(p)


class TestSubmissionFormatV2(unittest.TestCase):
    """v2 统一提交外层:{signal_id, company_id, kind, payload}。"""

    def test_tender_mapping_and_routing_key(self):
        """路由键=procurement_project_id;source_url 直映;null 分数→检测器缺省。"""
        env = asm.signal_to_envelope(v2_tender())
        t = env.tender
        assert t is not None
        self.assertEqual(t.project_id, "PRJ-1")          # 调查关联键,非原公告 ID
        self.assertEqual(t.published_date, "2026-10-03")
        self.assertEqual(t.amount_yuan, 1000000.0)
        self.assertEqual(t.source_url, "https://example.org/a")
        self.assertEqual(t.domain, "测试领域")
        self.assertIsNone(t.novelty_anomaly)             # null 分数不造 0 分
        self.assertIsNone(t.amount_history)              # v2 无历史结构块来源
        self.assertEqual(env.event_id, "")               # tender 留空派生

    def test_tender_detector_explanations(self):
        """signal_reason 全文进相关性条目;其余检测器短句含得分与可靠性。"""
        t = asm.signal_to_envelope(v2_tender()).tender
        assert t is not None
        self.assertIn("语义相似度 0.65", t.relevance.explanation)
        self.assertTrue(t.relevance.basis_sufficient)    # 相关性无可靠性标记
        self.assertIn("金额变化得分 0.800", t.amount_anomaly.explanation)
        self.assertTrue(t.amount_anomaly.basis_sufficient)
        self.assertIn("不强行评分", t.frequency_anomaly.explanation)
        self.assertFalse(t.frequency_anomaly.basis_sufficient)
        self.assertIsNone(t.novelty_anomaly)

    def test_rumor_passthrough_and_event_id(self):
        """rumor 近直通;event_id=evt_<signal_id> 防派生撞键。"""
        env = asm.signal_to_envelope(v2_rumor())
        r = env.rumor
        assert r is not None
        self.assertEqual(env.kind, "rumor")
        self.assertEqual(r.subject_name, "测试主体")
        self.assertEqual(r.signal_time, "2026-10-03T10:00:00+08:00")
        self.assertEqual(len(r.clues), 1)
        self.assertEqual(r.clues[0].clue_id, "k1")
        self.assertEqual(r.clues[0].author_type, "媒体")
        self.assertEqual(env.event_id, "evt_SIG_R_1")
        self.assertEqual(env.derive_event_id(), "evt_signal")  # 对照:派生会撞键

    def test_unknown_kind_rejected(self):
        bad = {"signal_id": "S", "company_id": "C", "kind": "news", "payload": {}}
        with self.assertRaises(ValueError):
            asm.signal_to_envelope(bad)

    def test_load_single_submission_and_list_containers(self):
        """容器归一:单条提交对象 / 提交列表 均可加载。"""
        batch = asm.load_batch(SUBMIT_TENDER)
        self.assertEqual(len(batch["signals"]), 1)
        self.assertIn("payload", batch["signals"][0])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "list.json"
            p.write_text(json.dumps([v2_tender(), v2_rumor()],
                                    ensure_ascii=False), encoding="utf-8")
            batch = asm.load_batch(p)
            self.assertEqual(len(batch["signals"]), 2)

    def test_assemble_batch_rows_carry_submission_meta(self):
        """manifest 行携带 signal_id/company_id;rumor 行标识=subject_name。"""
        rows = asm.assemble_batch({"signals": [v2_tender(), v2_rumor()]})
        self.assertTrue(all(r["status"] == "ok" for r in rows))
        self.assertEqual(rows[0]["signal_id"], "SIG-T-1")
        self.assertEqual(rows[0]["company_id"], "C-1")
        self.assertEqual(rows[1]["project_id"], "测试主体")
        self.assertEqual(rows[1]["event_id"], "evt_SIG_R_1")

    def test_frozen_v2_samples_roundtrip(self):
        """冻结样例装配后可回读验证(信封 extra=forbid 全链)。"""
        for path in (SUBMIT_TENDER, SUBMIT_RUMOR):
            batch = asm.load_batch(path)
            env = asm.signal_to_envelope(batch["signals"][0])
            dump = json.loads(env.model_dump_json(by_alias=True))
            InvestigationEnvelope.model_validate(dump)




    def test_project_seven_fields(self):
        """冻结样例第 0 条:七基础字段白名单直映。"""
        env = asm.signal_to_envelope(load_batch_dict()["signals"][0])
        t = env.tender
        assert t is not None
        self.assertEqual(env.kind, "tender")
        self.assertEqual(t.project_id, "R-c67b890aeb583592")
        self.assertTrue(t.project_name.startswith("天津市肿瘤医院空港医院"))
        self.assertEqual(t.purchaser, "天津市肿瘤医院空港医院")
        self.assertEqual(t.published_date, "2026-09-09")
        self.assertEqual(t.amount_yuan, 6270000.0)
        self.assertEqual(t.domain, "其他")
        self.assertIn("数智化病理系统", t.project_summary)

    def test_detector_scores_match_upstream(self):
        sig = load_batch_dict()["signals"][0]
        env = asm.signal_to_envelope(sig)
        t = env.tender
        assert t is not None
        self.assertAlmostEqual(t.amount_anomaly.score, sig["scores"]["amount_score"])
        self.assertAlmostEqual(t.novelty_anomaly.score, sig["scores"]["novelty_score"])
        self.assertAlmostEqual(t.frequency_anomaly.score, sig["scores"]["frequency_score"])
        self.assertAlmostEqual(t.relevance.score, sig["scores"]["relevance_score"])

    def test_basis_sufficient_from_reliable(self):
        """四路 basis_sufficient:reliable→检测器;evidence_status→relevance。"""
        env = asm.signal_to_envelope(min_signal())
        t = env.tender
        assert t is not None
        self.assertTrue(t.amount_anomaly.basis_sufficient)       # amount.reliable=true
        self.assertFalse(t.novelty_anomaly.basis_sufficient)     # novelty.reliable=false
        self.assertTrue(t.frequency_anomaly.basis_sufficient)    # frequency.reliable=true
        self.assertFalse(t.relevance.basis_sufficient)           # evidence_status≠sufficient
        self.assertIn("依据完整度不足", t.relevance.explanation)

    def test_relevance_basis_sufficient_when_sufficient(self):
        """冻结样例:5 条 evidence_status=sufficient → relevance.basisSufficient=true。"""
        for sig in load_batch_dict()["signals"]:
            t = asm.signal_to_envelope(sig).tender
            assert t is not None
            self.assertTrue(t.relevance.basis_sufficient)

    def test_amount_sentence_with_numbers_and_sim_warn(self):
        """合成句:数值进句;simulated>0 附模拟警示。"""
        t = asm.signal_to_envelope(load_batch_dict()["signals"][0]).tender
        assert t is not None
        exp = t.amount_anomaly.explanation
        self.assertIn("627.0 万元", exp)
        self.assertIn("21.9 万元", exp)
        self.assertIn("28.63 倍", exp)
        self.assertIn("历史基线含 8 个模拟样本", exp)
        self.assertIn("金额异常得分 1.000", exp)

    def test_novelty_sentence_with_simulated_ref(self):
        """冻结样例第 2 条(密云):对照项目为模拟样本、source_url null → 空串。"""
        t = asm.signal_to_envelope(load_batch_dict()["signals"][2]).tender
        assert t is not None
        exp = t.novelty_anomaly.explanation
        self.assertIn("模拟样本", exp)
        msp = t.novelty_history.most_similar_project
        assert msp is not None
        self.assertEqual(msp.data_origin, "simulated")
        self.assertEqual(msp.source_url, "")

    def test_frequency_sentence(self):
        t = asm.signal_to_envelope(load_batch_dict()["signals"][0]).tender
        assert t is not None
        exp = t.frequency_anomaly.explanation
        self.assertIn("近 30 天", exp)
        self.assertIn("采购 2 次", exp)
        self.assertIn("比值 2.67", exp)

    def test_null_amount(self):
        """null 金额 → amountYuan None + "未披露"合成句,检测器仍在。"""
        sig = min_signal()
        sig["project"]["amount_yuan"] = None
        t = asm.signal_to_envelope(sig).tender
        assert t is not None
        self.assertIsNone(t.amount_yuan)
        self.assertIn("未披露采购金额", t.amount_anomaly.explanation)
        self.assertIsNotNone(t.amount_history)

    def test_missing_scores_and_blocks(self):
        """缺 scores → 检测器整个缺省(不造 0 分);缺历史块 → 历史块 None。"""
        sig = min_signal()
        del sig["scores"], sig["historical_context"]
        t = asm.signal_to_envelope(sig).tender
        assert t is not None
        self.assertIsNone(t.amount_anomaly)
        self.assertIsNone(t.frequency_anomaly)
        self.assertIsNone(t.novelty_anomaly)
        self.assertIsNone(t.relevance)
        self.assertIsNone(t.amount_history)
        self.assertIsNone(t.novelty_history)
        self.assertIsNone(t.frequency_history)

    def test_missing_required_field_raises(self):
        sig = min_signal()
        del sig["project"]["buyer"]
        with self.assertRaises(ValueError):
            asm.signal_to_envelope(sig)

    def test_score_out_of_range_raises(self):
        """score 越界 → ValueError,不静默截断。"""
        sig = min_signal()
        sig["scores"]["amount_score"] = 1.5
        with self.assertRaises(ValueError):
            asm.signal_to_envelope(sig)

    def test_process_quantities_absent_from_dump(self):
        """过程量纪律:priority/weights/rank/reasons 等不进信封。"""
        dump = json.loads(
            asm.signal_to_envelope(load_batch_dict()["signals"][0]).model_dump_json())
        flat = json.dumps(dump, ensure_ascii=False)
        for forbidden in ("priorityScore", "priority_score", "dailyRank",
                          "topK", "weights", "signalReason", "reasons",
                          "sourceRow", "embeddingModel", "priorityLabel"):
            self.assertNotIn(forbidden, flat)

    def test_new_fields_camelcase_and_roundtrip(self):
        """新字段 camelCase 别名落盘;extra=forbid 下回读通过。"""
        env = asm.signal_to_envelope(load_batch_dict()["signals"][0])
        dump = json.loads(env.model_dump_json(by_alias=True))
        t = dump["tender"]
        self.assertIn("sourceUrl", t)
        self.assertIn("companyProfile", t)
        self.assertIn("amountHistory", t)
        self.assertIn("mostSimilarProject", t["noveltyHistory"])
        self.assertEqual(dump["eventId"], "")     # event_id 留空纪律
        InvestigationEnvelope.model_validate(dump)   # 回读不炸

    def test_derive_event_id_matches_cli_rule(self):
        """与 cli 旧行为逐字等价(非 alnum→_ 后 [:32],evt_ 前缀不在 32 位内)。"""
        env = asm.signal_to_envelope(load_batch_dict()["signals"][0])
        self.assertEqual(env.derive_event_id(), "evt_R_c67b890aeb583592")
        rumored = InvestigationEnvelope(kind="rumor", event_id="")
        self.assertEqual(rumored.derive_event_id(), "evt_signal")


class TestAssembleBatch(unittest.TestCase):

    def test_all_ok_and_duplicate_warning(self):
        """5 条全 ok;批内重复 project_id 不硬失败,标警告。"""
        batch = load_batch_dict()
        rows = asm.assemble_batch(batch)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["status"] == "ok" for r in rows))
        batch["signals"].append(copy.deepcopy(batch["signals"][0]))
        rows = asm.assemble_batch(batch)
        self.assertEqual(rows[-1]["status"], "ok")
        self.assertIn("重复", rows[-1]["warning"])

    def test_single_error_does_not_kill_batch(self):
        """单条缺 project_id → 该条 error,其余照常 ok。"""
        batch = load_batch_dict()
        broken = min_signal()
        del broken["project"]["project_id"]
        batch["signals"].append(broken)
        rows = asm.assemble_batch(batch)
        self.assertEqual(rows[-1]["status"], "error")
        self.assertIn("project_id", rows[-1]["error"])
        self.assertEqual(sum(1 for r in rows if r["status"] == "ok"), 5)


class TestAssembleMain(unittest.TestCase):

    @staticmethod
    def _args(batch: str, out_dir: str) -> SimpleNamespace:
        return SimpleNamespace(batch=batch, out_dir=out_dir)

    def test_writes_envelopes_and_manifest(self):
        """CLI 落盘:5 信封 + manifest;落盘件可 model_validate 回读。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "out")
            code = asm.main(self._args(str(BATCH_PATH), out))
            self.assertEqual(code, 0)
            out_p = Path(out)
            files = sorted(out_p.glob("signal_*.json"))
            self.assertEqual(len(files), 5)
            for f in files:
                env = InvestigationEnvelope.model_validate(
                    json.loads(f.read_text(encoding="utf-8")))
                self.assertEqual(env.kind, "tender")
            manifest = json.loads(
                (out_p / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["ok_count"], 5)
            self.assertEqual(manifest["count"], 5)
            self.assertTrue(all("file" in r for r in manifest["rows"]))
            self.assertIn("priority", json.dumps(manifest))       # 分诊量留 manifest

    def test_error_batch_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            batch = load_batch_dict()
            batch["signals"].append(min_signal())
            del batch["signals"][-1]["project"]["project_id"]
            p.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
            code = asm.main(self._args(str(p), str(Path(tmp) / "out")))
            self.assertEqual(code, 1)

    def test_missing_file_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = asm.main(self._args(str(Path(tmp) / "nope.json"),
                                       str(Path(tmp) / "out")))
            self.assertEqual(code, 2)


class TestInvestigateBatchEntry(unittest.TestCase):
    """investigate 直读批量:互斥/越界门禁(subprocess)+ 离线链路冒烟。"""

    @staticmethod
    def _run_cli(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "sense2act", "investigate", *extra],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)

    def test_envelope_and_batch_mutually_exclusive(self):
        r = self._run_cli("--envelope", str(TENDER_ENVELOPE),
                          "--envelope-batch", str(BATCH_PATH))
        self.assertEqual(r.returncode, 2)

    def test_signal_index_with_envelope_rejected(self):
        """--envelope 配非 0 --signal-index → 装配门禁(argparse 之后再拦)。"""
        with tempfile.TemporaryDirectory() as tmp:
            replay = Path(tmp) / "empty.json"
            replay.write_text("[]", encoding="utf-8")
            r = self._run_cli("--envelope", str(TENDER_ENVELOPE),
                              "--signal-index", "2",
                              "--replay", str(replay))
            self.assertEqual(r.returncode, 2)
            self.assertIn("[门禁] --signal-index", r.stderr)

    def test_signal_index_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            replay = Path(tmp) / "empty.json"
            replay.write_text("[]", encoding="utf-8")
            r = self._run_cli("--envelope-batch", str(BATCH_PATH),
                              "--signal-index", "99",
                              "--replay", str(replay))
            self.assertEqual(r.returncode, 2)
            self.assertIn("越界", r.stderr)

    def test_batch_replay_smoke_offline_chain(self):
        """空回放冒烟:装配→校验→register_event→种子入账全链通,
        首次 LLM 调用按预期 fail-fast(回放响应耗尽 stage=s1a)。"""
        with tempfile.TemporaryDirectory() as tmp:
            replay = Path(tmp) / "empty.json"
            replay.write_text("[]", encoding="utf-8")
            db = Path(tmp) / "smoke.db"
            r = self._run_cli("--envelope-batch", str(BATCH_PATH),
                              "--signal-index", "0",
                              "--replay", str(replay), "--db", str(db))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("回放响应耗尽(stage=s1a)", r.stderr)
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "select event_id, project_id, kind from events").fetchall()
            finally:
                conn.close()
            self.assertEqual(
                rows, [("evt_R_c67b890aeb583592", "R-c67b890aeb583592", "tender")])


if __name__ == "__main__":
    unittest.main()
