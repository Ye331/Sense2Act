"""后端接入层:用内存假后端跑通 领取→留痕→证据→报告→收尾,并校验契约不变量。"""
from __future__ import annotations

import argparse
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from sense2act.bridge.client import CONFLICT, UNPROCESSABLE, BackendError
from sense2act.bridge.envelope import build_envelope, signal_kind
from sense2act.bridge.report import _pack_summary, _plain
from sense2act.bridge.report_projection import attachments, envelope_fact, fallback_fact, _specific_evidence_ids
from sense2act.bridge.reporter import StepReporter, normalize_published
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
# 新闻信号:signal-service/bridge/news.py 推送的形状(detail.rumor = RumorSignal 载荷,线索 id 即新闻文档 id)
NEWS_DOC = {"id": "doc_n1", "doc_type": "news", "title": "我国化工行业首个大模型发布新版本", "publish_date": "2026-09-03",
            "url": "http://www.toutiao.com/a7681110928078766644/?channel=", "content_text": "科大讯飞联合发布智能化工大模型3.0Pro"}
RUMOR = {"subject_name": "科大讯飞", "subject_credit_code": "", "summary": "科大讯飞等联合发布智能化工大模型3.0Pro",
         "source": "环球网", "signal_time": "2026-10-18T10:00:00+00:00", "watch_focus": "核实联合发布是否属实",
         "clues": [{"clue_id": "doc_n1", "source": "环球网", "platform": "toutiao", "title": NEWS_DOC["title"],
                    "content": NEWS_DOC["content_text"], "url": NEWS_DOC["url"],
                    "published_at": "2026-09-03T09:13:00+08:00", "author_type": "媒体"}]}
NEWS_HITS = [{"rule_type": "news_event", "rule_id": None, "rule_version": None, "weight": 1.0,
              "detail": {"score": 0.98, "reliable": True, "explanation": "规则[竞品与行业发布],主体“科大讯飞”", "rumor": RUMOR}}]


class FakeBackend:
    """只实现 worker 用到的内部接口,按后端规则拒绝非法写入。"""

    def __init__(self, status="created", max_rounds=8, token_budget=10**9, hits=HITS, document=DOC):
        self.inv = {"id": "inv_1", "status": status, "max_rounds": max_rounds, "token_budget": token_budget, "token_used": 0}
        self.hits, self.document = hits, document
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
        return {"investigation": self.inv, "signal": {"id": "sig_1", "hits": self.hits, "score": "0.91", "document": self.document}}

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
    opts = argparse.Namespace(mode="replay", replay_case=str(ROOT / "examples/tender_medical_ai"),
                              replay_rumor_case=str(ROOT / "examples/ai_model_release"), data_dir=data_dir,
                              config_dir=str(ROOT / "data/config"), max_llm_calls=160, max_collect_calls=80)
    return Worker(backend, opts)


class BridgeTest(unittest.TestCase):
    def test_full_investigation_round_trip(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            self.assertEqual(worker_for(backend, tmp).poll_once(), 1)
            runs = Path(tmp) / "inv_1" / "runs"
            status = __import__("json").loads((runs / "chain.status.json").read_text(encoding="utf-8"))
            payload = __import__("json").loads((runs / "chain.web.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "ready")
            self.assertEqual(payload["schema_version"], "chain_web.v1")
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
        self.assertIn("当前判断：", backend.report["summary"])
        self.assertIn("依据：", backend.report["summary"])
        self.assertIn("改变条件：", backend.report["summary"])
        self.assertEqual(backend.report["event_extraction"]["entities"][0]["name"], "XX市第一人民医院")

    def test_chain_failure_is_soft_and_writes_failed_status(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, patch(
                "sense2act.bridge.chain_artifact.build_chain_web",
                side_effect=RuntimeError("secret C:/private/raw.txt")):
            self.assertEqual(worker_for(backend, tmp).poll_once(), 1)
            status = __import__("json").loads((Path(tmp) / "inv_1" / "runs" / "chain.status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["error_code"], "RuntimeError")
            self.assertNotIn("private", status["error_message"])
            self.assertFalse((Path(tmp) / "inv_1" / "runs" / "chain.web.json.tmp").exists())
        self.assertEqual(backend.inv["status"], "completed")
        self.assertIsNotNone(backend.report)

    def test_budget_rejection_still_delivers_partial_report(self):
        backend = FakeBackend(max_rounds=1)
        with tempfile.TemporaryDirectory() as tmp:
            worker_for(backend, tmp).poll_once()
        self.assertEqual(backend.inv["status"], "completed", backend.inv.get("error"))
        self.assertTrue(all(s["round"] <= 1 for s in backend.steps))
        self.assertTrue(backend.report["claims"])
        self.assertEqual({c["nature"] for c in backend.report["claims"]}, {"fact"})
        self.assertTrue(backend.report["pending_findings"])
        self.assertTrue(all(c["status"] in {"verified", "contested"}
                            for c in backend.report["claims"]))
        submitted = {c["source_claim_id"] for c in backend.report["claims"]}
        self.assertTrue(all(set(view["supporting_claim_ids"]) <= submitted
                            for view in backend.report["conclusions"]))
        for key in ("当前判断：", "依据：", "缺口：", "改变条件：", "下一步："):
            self.assertIn(key, backend.report["summary"])
        self.assertTrue(all(c["evidence_ids"] and
                            all(eid in backend.evidences for eid in c["evidence_ids"])
                            for c in backend.report["claims"]))

    def test_report_projection_is_deterministic(self):
        reports = []
        for _ in range(2):
            backend = FakeBackend(max_rounds=1)
            with tempfile.TemporaryDirectory() as tmp:
                worker_for(backend, tmp).poll_once()
            reports.append(backend.report)
        self.assertEqual(reports[0], reports[1])

    def test_summary_cleanup_and_atomic_limit(self):
        self.assertEqual(_plain("## 结论\n**判断** `值`〔内部注记〕"), "结论\n判断 值")
        packed = _pack_summary(["当前判断：完整句。", "依据：引用 E12345。"],
                               "结论状态:已证实 1 条。", limit=34)
        self.assertLessEqual(len(packed), 34)
        self.assertNotIn("E123", packed)  # 整行放不下时舍弃，不截断引用 ID
        self.assertTrue(packed.endswith("结论状态:已证实 1 条。"))
        self.assertEqual(_pack_summary([], "超长" * 100, limit=20),
                         "结论状态与停止原因详见调查记录。")

    def test_envelope_evidence_whitelist_requires_anchor_and_exact_value(self):
        envelope = build_envelope({"signal": {"hits": HITS, "document": DOC}}, "evt_x")
        self.assertTrue(envelope_fact("采购人为XX市第一人民医院", envelope))
        self.assertTrue(envelope_fact("预算金额为4,800,000.00元", envelope))
        self.assertFalse(envelope_fact("近两年存在同额采购", envelope))
        self.assertFalse(envelope_fact("采购人为另一家医院", envelope))
        self.assertEqual(fallback_fact(envelope),
                         "项目名称为《XX市第一人民医院AI辅助诊断系统采购项目》")

    def test_exact_direction_does_not_fall_back_to_opposite_evidence(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE claim_evidence(claim_id TEXT,evidence_id TEXT,ruling TEXT,round INT)")
        conn.execute("INSERT INTO claim_evidence VALUES('c1','E1','矛盾',1)")
        ledger = SimpleNamespace(conn=conn)
        reporter = StepReporter(SimpleNamespace(), "inv", ledger, "evt", "doc")
        reporter.evidence = {"E1": "ev_1"}
        self.assertEqual(reporter.claim_evidence("c1", "支持", exact=True), [])
        self.assertEqual(reporter.claim_evidence("c1", "矛盾", exact=True), ["ev_1"])

    def test_current_announcement_cannot_support_historical_or_conditional_claim(self):
        envelope = build_envelope({"signal": {"hits": HITS, "document": DOC}}, "evt_x")
        row = {"url": DOC["url"], "title": DOC["title"], "content": DOC["content_text"]}
        ledger = SimpleNamespace(evidence_row=lambda _eid: row)
        reporter = SimpleNamespace(evidence={"evt_x:E2": "ev_2"}, ledger=ledger)
        self.assertEqual(_specific_evidence_ids(
            reporter, envelope, "近两年有同等金额的采购记录", ["ev_2"]), [])
        self.assertEqual(_specific_evidence_ids(
            reporter, envelope, "若本企业中标将形成正向收入贡献", ["ev_2"]), [])
        self.assertEqual(_specific_evidence_ids(
            reporter, envelope, "本企业若参与投标具备完成报名与递交的可行条件", ["ev_2"]), [])
        self.assertEqual(_specific_evidence_ids(
            reporter, envelope, "该项目已获立项批复并列入建设规划", ["ev_2"]), [])
        self.assertEqual(_specific_evidence_ids(
            reporter, envelope, "公告截至核查时未被更正或撤销", ["ev_2"]), [])

    def test_screenshot_asset_projects_as_presentation_only_image(self):
        ledger = SimpleNamespace(evidence_assets=lambda _event: {
            "evt:E2": [{"path": "assets/E2_shot.png", "kind": "screenshot",
                         "content_type": "image/png", "sha256": "a" * 64,
                         "bytes": 42, "caption": "公告截图"}]})
        reporter = SimpleNamespace(ledger=ledger, event_id="evt", inv="inv_1",
                                   evidence={"evt:E2": "ev_2"})
        projected = attachments(reporter)
        self.assertEqual(projected[0]["kind"], "image")
        self.assertEqual(projected[0]["storage_key"],
                         "inv_1/runs/evt/assets/E2_shot.png")
        self.assertTrue(projected[0]["presentation_only"])

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

    def test_backend_document_id_never_masquerades_as_project_number(self):
        envelope = build_envelope({"signal": {"hits": HITS, "document": DOC}}, "evt_x")
        self.assertEqual(envelope.tender.project_id, "")
        hits = [{**HITS[0], "detail": {**HITS[0]["detail"], "project_id": "1409992026AGK00184"}}, *HITS[1:]]
        routed = build_envelope({"signal": {"hits": hits, "document": DOC}}, "evt_x")
        self.assertEqual(routed.tender.project_id, "1409992026AGK00184")

    def test_news_signal_replays_rumor_case_end_to_end(self):
        backend = FakeBackend(hits=NEWS_HITS, document=NEWS_DOC)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(worker_for(backend, tmp).poll_once(), 1)
        self.assertEqual(backend.inv["status"], "completed", backend.inv.get("error"))
        report = backend.report
        self.assertTrue(report["title"].startswith("科大讯飞·"))          # 新闻信号按监测主体出标题
        self.assertEqual(report["event_extraction"]["event"]["type"], "news")
        self.assertEqual(report["event_extraction"]["entities"][0]["name"], "科大讯飞")
        self.assertTrue(report["claims"])
        # 线索卡作为外部证据登记:带 URL、发布时间与 Agent 读到的原文快照
        self.assertTrue(all(e.get("url") and e.get("published_at") and e.get("snapshot") for e in backend.evidences.values()))

    def test_news_hit_builds_rumor_envelope(self):
        context = {"signal": {"hits": NEWS_HITS, "document": NEWS_DOC}}
        self.assertEqual(signal_kind(context), "rumor")
        self.assertEqual(signal_kind({"signal": {"hits": HITS, "document": DOC}}), "tender")
        envelope = build_envelope(context, "evt_n")
        self.assertEqual(envelope.kind, "rumor")
        self.assertEqual(envelope.rumor.subject_name, "科大讯飞")
        self.assertEqual(envelope.rumor.clues[0].clue_id, "doc_n1")
        self.assertEqual(envelope.rumor.clues[0].published_at, "2026-09-03T09:13:00+08:00")
        broken = [{**NEWS_HITS[0], "detail": {"score": 0.9}}]
        with self.assertRaises(ValueError):
            build_envelope({"signal": {"hits": broken, "document": NEWS_DOC}}, "evt_n")

    def test_published_time_normalization(self):
        self.assertEqual(normalize_published("2026-01-15"), "2026-01-15")
        self.assertEqual(normalize_published("2026-10-03T09:00:00"), "2026-10-03T09:00:00+08:00")
        self.assertEqual(normalize_published("2026-10-03T09:00:00Z"), "2026-10-03T09:00:00+00:00")
        self.assertIsNone(normalize_published(""))


if __name__ == "__main__":
    unittest.main()
