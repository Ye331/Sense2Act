"""后端接入层:用内存假后端跑通 领取→留痕→证据→报告→收尾,并校验契约不变量。"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from sense2act.bridge.client import CONFLICT, UNPROCESSABLE, BackendError
from sense2act.bridge.envelope import build_envelope
from sense2act.bridge.reporter import normalize_published
from sense2act.bridge.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
DOC = {"id": "doc_1", "title": "XX市第一人民医院AI辅助诊断系统采购项目", "org_name": "XX市第一人民医院",
       "amount": "4800000.00", "publish_date": "2026-09-20", "category": "医疗信息化",
       "url": "https://example.gov.cn/notice/1", "content_text": "公开招标AI辅助诊断系统"}
HITS = [
    {"rule_type": "amount_anomaly", "weight": 0.5, "detail": {"score": 0.91, "reliable": True, "explanation": "金额为历史中位数的4.2倍", "ratio": 4.2, "samples": 8}},
    {"rule_type": "novelty", "weight": 0.5, "detail": {"score": 0.83, "reliable": True, "explanation": "首次出现的采购方向"}},
    {"rule_type": "semantic_match", "weight": 0, "detail": {"score": 0.9, "explanation": "与医疗AI画像高度相关", "profile": "医疗AI"}},
]


class FakeBackend:
    """只实现 worker 用到的内部接口,按后端规则拒绝非法写入。"""

    def __init__(self, status="created", max_rounds=8, token_budget=10**9):
        self.inv = {"id": "inv_1", "status": status, "max_rounds": max_rounds, "token_budget": token_budget, "token_used": 0}
        self.steps, self.questions, self.evidences, self.report = [], {}, {}, None
        self.request_ids = {}

    def _writable(self):
        if self.inv["status"] != "investigating":
            raise BackendError(CONFLICT, "调查已终态")

    def investigations(self, status):
        return [self.inv] if self.inv["status"] == status else []

    def start(self, inv_id):
        if self.inv["status"] != "created":
            raise BackendError(CONFLICT, "已被占用")
        self.inv["status"] = "investigating"

    def context(self, inv_id):
        return {"investigation": self.inv, "signal": {"id": "sig_1", "hits": HITS, "score": "0.91", "document": DOC}}

    def step(self, inv_id, event, round_no, payload, token_usage=0, request_id=None):
        self._writable()
        assert event in {"round_started", "questions_generated", "tool_selected", "tool_completed", "reflection_updated"}, event
        if round_no < 1:
            raise BackendError(40001, "round 必须为正整数")
        if round_no > self.inv["max_rounds"]:
            raise BackendError(UNPROCESSABLE, "超出 max_rounds")
        if request_id in self.request_ids:
            return self.request_ids[request_id]
        self.inv["token_used"] += token_usage
        if self.inv["token_used"] > self.inv["token_budget"]:
            raise BackendError(UNPROCESSABLE, "超出 token_budget")
        self.steps.append({"event": event, "round": round_no, "payload": payload})
        self.request_ids[request_id] = {"seq": len(self.steps)}
        return self.request_ids[request_id]

    def create_question(self, inv_id, text, round_no):
        self._writable()
        qid = f"q_{len(self.questions) + 1}"
        self.questions[qid] = {"id": qid, "text": text, "status": "open"}
        return self.questions[qid]

    def update_question(self, qid, status, answer, evidence_ids):
        self._writable()
        assert all(e in self.evidences for e in evidence_ids), "问题引用了未登记证据"
        self.questions[qid].update(status=status, answer_summary=answer, evidence_ids=evidence_ids)

    def register_evidence(self, inv_id, **fields):
        self._writable()
        if not fields.get("doc_id") and not (fields.get("url") and fields.get("published_at")):
            raise BackendError(40001, "外部证据 url/published_at 必填")
        eid = f"ev_{len(self.evidences) + 1}"
        self.evidences[eid] = fields
        return {"id": eid}

    def submit_report(self, inv_id, draft):
        self._writable()
        for claim in draft["claims"]:
            assert claim["nature"] in {"fact", "inference", "speculation"}
            if not claim["evidence_ids"] or any(e not in self.evidences for e in claim["evidence_ids"]):
                raise BackendError(UNPROCESSABLE, "claim 引用未登记证据")
        self.report, self.inv["status"] = draft, "reporting"

    def complete(self, inv_id):
        if not self.report:
            raise BackendError(UNPROCESSABLE, "无报告")
        self.inv["status"] = "completed"

    def fail(self, inv_id, error):
        self.inv.update(status="failed", error=error)


def worker_for(backend, data_dir):
    opts = argparse.Namespace(mode="replay", replay_case=str(ROOT / "examples/tender_medical_ai"), data_dir=data_dir,
                              config_dir=str(ROOT / "data/config"), max_llm_calls=160, max_collect_calls=80)
    return Worker(backend, opts)


class BridgeTest(unittest.TestCase):
    def test_full_investigation_round_trip(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(worker_for(backend, tmp).poll_once(), 1)
        self.assertEqual(backend.inv["status"], "completed", backend.inv.get("error"))
        events = [s["event"] for s in backend.steps]
        for expected in ("round_started", "questions_generated", "tool_selected", "tool_completed", "reflection_updated"):
            self.assertIn(expected, events)
        # 信号层证据走 doc_id,外部证据带 Agent 读到的原文快照
        self.assertTrue(any(e.get("doc_id") == "doc_1" for e in backend.evidences.values()))
        self.assertTrue(any(e.get("snapshot") for e in backend.evidences.values()))
        # 录制样例四条 claim 全部查清 → 问题全部 clarified,报告全是事实
        self.assertTrue(backend.questions)
        self.assertTrue(all(q["status"] == "clarified" for q in backend.questions.values()))
        self.assertEqual({c["nature"] for c in backend.report["claims"]}, {"fact"})
        self.assertEqual(backend.report["event_extraction"]["entities"][0]["name"], "XX市第一人民医院")

    def test_budget_rejection_still_delivers_partial_report(self):
        backend = FakeBackend(max_rounds=1)
        with tempfile.TemporaryDirectory() as tmp:
            worker_for(backend, tmp).poll_once()
        self.assertEqual(backend.inv["status"], "completed", backend.inv.get("error"))
        self.assertTrue(all(s["round"] <= 1 for s in backend.steps))
        self.assertTrue(backend.report["claims"])

    def test_user_stop_aborts_without_overwriting_terminal_state(self):
        backend = FakeBackend()
        original = backend.step

        def stop_after_three(*args, **kwargs):
            if len(backend.steps) >= 3:
                backend.inv["status"] = "stopped"
            return original(*args, **kwargs)
        backend.step = stop_after_three
        with tempfile.TemporaryDirectory() as tmp:
            worker_for(backend, tmp).poll_once()
        self.assertEqual(backend.inv["status"], "stopped")
        self.assertIsNone(backend.report)

    def test_recover_fails_interrupted_and_completes_reported(self):
        for status, expected in (("investigating", "failed"), ("reporting", "completed")):
            backend = FakeBackend(status=status)
            backend.report = {"claims": []} if status == "reporting" else None
            with tempfile.TemporaryDirectory() as tmp:
                worker_for(backend, tmp).recover()
            self.assertEqual(backend.inv["status"], expected)

    def test_envelope_maps_hits_to_detectors(self):
        envelope = build_envelope({"signal": {"hits": HITS, "document": DOC}}, "evt_x")
        tender = envelope.tender
        self.assertEqual(tender.amount_yuan, 4800000.0)
        self.assertEqual(tender.amount_anomaly.score, 0.91)
        self.assertEqual(tender.novelty_anomaly.explanation, "首次出现的采购方向")
        self.assertEqual(tender.relevance.score, 0.9)
        self.assertIsNone(tender.frequency_anomaly)
        self.assertEqual(tender.amount_history.amount_ratio, 4.2)

    def test_published_time_normalization(self):
        self.assertEqual(normalize_published("2026-01-15"), "2026-01-15")
        self.assertEqual(normalize_published("2026-10-03T09:00:00"), "2026-10-03T09:00:00+08:00")
        self.assertEqual(normalize_published("2026-10-03T09:00:00Z"), "2026-10-03T09:00:00+00:00")
        self.assertIsNone(normalize_published(""))


if __name__ == "__main__":
    unittest.main()
