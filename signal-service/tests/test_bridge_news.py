"""后端接入·新闻分支:新闻文档 → 原文时间(快照)→ 新闻事件检测 → news_event 信号(带 RumorSignal 载荷)。"""
import json
from datetime import datetime, timezone

import httpx

from bridge.detection import BackendClient, DetectionBridge
from bridge.news import DETECTOR, NewsBridge
from core.config import ROOT

NOW = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)
TITLE = "科大讯飞发布医疗大模型新版本"
MEDIA = {"doc_n1": ("新华社", "2026-10-16T09:00:00+08:00"), "doc_n2": ("光明网", "2026-10-16T10:30:00+08:00"),
         "doc_n3": ("环球网", "2026-10-16T11:05:00+08:00")}


def news_doc(doc_id, lead):
    return {"id": doc_id, "doc_type": "news", "title": TITLE, "url": f"https://news.example.com/{doc_id}",
            "publish_date": "2026-10-16", "content_text": f"{lead}。10月16日,科大讯飞发布医疗大模型新版本,"
            "覆盖辅助诊断、电子病历生成等场景,已在多家医院试点。该大模型面向基层医疗机构开放。"}


DOCS = [news_doc("doc_n1", "新华社合肥10月16日电"), news_doc("doc_n2", "据光明网报道"), news_doc("doc_n3", "环球网消息")]


def snapshot(doc_id):
    media, published = MEDIA[doc_id]
    return (f"<html><head><meta property='article:published_time' content='{published}'><title>{TITLE}</title></head>"
            f"<body><article><h1>{TITLE}</h1><p>来源:{media}</p></article></body></html>")


class FakeBackend:
    def __init__(self, docs, snapshots=True):
        self.queue = [{"document": doc, "profiles": [], "rules": []} for doc in docs]
        self.docs = {doc["id"]: doc for doc in docs}
        self.snapshots = snapshots
        self.signals, self.scanned, self.reads = [], [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Key"] == "k"
        path = request.url.path.removeprefix("/api/v1")
        if path == "/internal/detection-queue":
            return self.ok({"items": self.queue})
        if path.startswith("/documents/") and path.endswith("/snapshot"):
            doc_id = path.split("/")[2]
            self.reads.append(path)
            if not self.snapshots:
                return httpx.Response(404, json={"code": 40401, "message": "无快照", "data": None})
            return httpx.Response(200, text=snapshot(doc_id), headers={"Content-Type": "text/html;charset=UTF-8"})
        if path.startswith("/documents/"):
            doc_id = path.split("/")[2]
            self.reads.append(path)
            media = MEDIA[doc_id][0]
            return self.ok({**self.docs[doc_id], "raw": {"platform": "toutiao", "source_name": media,
                                                         "author_type": "媒体", "detail_fetched": True}})
        if path == "/internal/signals":
            self.signals.append(json.loads(request.content))
            return self.ok({"signal_id": f"sig_{len(self.signals)}", "status": "investigating", "auto_investigated": True})
        if path == "/internal/detection-scan-complete":
            self.scanned += json.loads(request.content)["document_ids"]
            return self.ok({"updated": len(self.scanned)})
        return httpx.Response(404, json={"code": 40401, "message": path, "data": None})

    @staticmethod
    def ok(data):
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": data})


def bridge_for(backend, service, data_dir, news=True):
    client = BackendClient("http://backend", "k", transport=httpx.MockTransport(backend))
    news_bridge = NewsBridge(client, ROOT / "signals/news/platform.json", data_dir, clock=lambda: NOW) if news else None
    return DetectionBridge(client, service, "医疗信息系统", news=news_bridge)


def test_news_documents_become_one_rumor_signal(service, tmp_path):
    backend = FakeBackend(DOCS)
    assert bridge_for(backend, service, tmp_path).run_once() == {"scanned": 3, "signals": 1}
    signal = backend.signals[0]
    assert signal["detector"] == DETECTOR
    assert signal["document"]["id"] in MEDIA                      # 锚在事件的首条新闻文档上
    assert 0 < signal["score"] <= 1
    (hit,) = signal["hits"]
    assert hit["rule_type"] == "news_event" and hit["rule_id"] is None
    detail = hit["detail"]
    # 与 Agent 的约定:score / reliable / explanation,外加装配 rumor 信封的完整载荷
    assert {"score", "reliable", "explanation"} <= set(detail) and detail["score"] == signal["score"]
    assert detail["subject_name"] == "科大讯飞" and detail["event_types"] == ["PRODUCT_LAUNCH"]
    assert sorted(detail["document_ids"]) == sorted(MEDIA)        # 三家媒体归成同一事件
    rumor = detail["rumor"]
    assert rumor["subject_name"] == "科大讯飞"
    clues = {clue["clue_id"]: clue for clue in rumor["clues"]}
    assert set(clues) == set(MEDIA)                               # 线索 id 即平台文档 id
    assert clues["doc_n1"]["published_at"] == "2026-10-16T09:00:00+08:00"   # 原文时间取自快照
    assert clues["doc_n2"]["source"] == "光明网"
    assert sorted(backend.scanned) == sorted(MEDIA)


def test_alert_is_pushed_once(service, tmp_path):
    backend = FakeBackend(DOCS)
    bridge = bridge_for(backend, service, tmp_path)
    bridge.run_once()
    bridge.run_once()                                             # 同批文档再来一遍:告警已提交,不重复推
    assert len(backend.signals) == 1


def test_news_without_original_time_is_observed_not_signaled(service, tmp_path):
    backend = FakeBackend(DOCS, snapshots=False)
    assert bridge_for(backend, service, tmp_path).run_once() == {"scanned": 3, "signals": 0}
    assert backend.signals == [] and sorted(backend.scanned) == sorted(MEDIA)


def test_news_detection_off_only_marks_scanned(service, tmp_path):
    backend = FakeBackend(DOCS)
    assert bridge_for(backend, service, tmp_path, news=False).run_once() == {"scanned": 3, "signals": 0}
    assert backend.reads == [] and sorted(backend.scanned) == sorted(MEDIA)


def test_stale_lock_from_crash_is_cleared(service, tmp_path):
    (tmp_path / "news_monitor.lock").write_text("2026-10-17T00:00:00+00:00", encoding="utf-8")
    backend = FakeBackend(DOCS)
    assert bridge_for(backend, service, tmp_path).run_once()["signals"] == 1
