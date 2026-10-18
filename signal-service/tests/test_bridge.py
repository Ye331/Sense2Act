"""后端接入:检测队列 → 检测 → 信号回推 → 扫描完成;/embed 向量端点。"""
import json

import httpx
from fastapi.testclient import TestClient

from application import create_app
from bridge.detection import DETECTOR, BackendClient, DetectionBridge

HISTORY = [
    {"id": "doc_h1", "title": "历史影像平台", "org": {"id": "org_1", "name": "医院甲"}, "amount": "100.00", "publish_date": "2026-07-05", "url": "https://example.com/h1", "category": "医疗信息化"},
    {"id": "doc_h2", "title": "历史临床系统", "org": {"id": "org_1", "name": "医院甲"}, "amount": "200.00", "publish_date": "2026-07-06", "url": "https://example.com/h2", "category": "医疗信息化"},
    {"id": "doc_h3", "title": "历史数据中心", "org": {"id": "org_1", "name": "医院甲"}, "amount": "300.00", "publish_date": "2026-08-05", "url": "https://example.com/h3", "category": "医疗信息化"},
]
QUEUE = [
    {"document": {"id": "doc_c1", "doc_type": "announcement", "title": "新医疗AI采购", "org_name": "医院甲", "org_id": "org_1",
                  "amount": "800.00", "publish_date": "2026-10-04", "url": "https://example.com/c1", "category": "医疗信息化",
                  "content_text": "新医疗AI采购"},
     "org_stats": None, "profiles": [], "rules": [{"id": "rule_seed01", "version": 3, "type": "amount_anomaly", "enabled": True}]},
    {"document": {"id": "doc_p1", "doc_type": "policy", "title": "某政策", "publish_date": "2026-10-04"}, "profiles": [], "rules": []},
]


class FakeBackend:
    def __init__(self):
        self.signals, self.scanned, self.history_calls = [], [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Key"] == "k"
        path = request.url.path.removeprefix("/api/v1")
        if path == "/internal/detection-queue":
            return self.ok({"items": QUEUE})
        if path == "/documents":
            params = dict(request.url.params)
            self.history_calls.append(params)
            rows = [d for d in HISTORY if d["publish_date"] <= params["date_to"]]
            return self.ok({"items": rows, "total": len(rows), "page": 1, "page_size": 100})
        if path == "/internal/signals":
            body = json.loads(request.content)
            self.signals.append(body)
            return self.ok({"signal_id": "sig_1", "status": "investigating", "auto_investigated": True})
        if path == "/internal/detection-scan-complete":
            self.scanned += json.loads(request.content)["document_ids"]
            return self.ok({"updated": 2})
        return httpx.Response(404, json={"code": 40401, "message": path, "data": None})

    @staticmethod
    def ok(data):
        return httpx.Response(200, json={"code": 0, "message": "ok", "data": data})


def test_queue_documents_are_detected_and_signals_pushed(service):
    backend = FakeBackend()
    bridge = DetectionBridge(BackendClient("http://backend", "k", transport=httpx.MockTransport(backend)), service, "医疗信息系统")
    assert bridge.run_once() == {"scanned": 2, "signals": 1}
    # 历史严格早于检测日,按单位取
    assert backend.history_calls[0]["org_id"] == "org_1" and backend.history_calls[0]["date_to"] == "2026-10-03"
    signal = backend.signals[0]
    assert signal["document"] == {"id": "doc_c1"} and signal["detector"] == DETECTOR and 0 < signal["score"] <= 1
    hits = {hit["rule_type"]: hit for hit in signal["hits"]}
    assert hits["amount_anomaly"]["rule_id"] == "rule_seed01" and hits["amount_anomaly"]["rule_version"] == 3
    assert hits["amount_anomaly"]["detail"]["ratio"] == 4.0 and "中位数" in hits["amount_anomaly"]["detail"]["explanation"]
    # 与 Agent 的约定:每个命中都带 score / reliable / explanation
    assert all({"score", "reliable", "explanation"} <= set(hit["detail"]) for hit in signal["hits"])
    assert hits["semantic_match"]["detail"]["profile"] == "医疗信息系统"
    # 有无命中、是否公告,处理过的文档全部标记已扫描
    assert sorted(backend.scanned) == ["doc_c1", "doc_p1"]


def test_empty_queue_is_a_noop(service):
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or FakeBackend.ok({"items": []}))
    bridge = DetectionBridge(BackendClient("http://backend", "k", transport=transport), service, "画像")
    assert bridge.run_once() == {"scanned": 0, "signals": 0}
    assert len(calls) == 1


def test_embed_endpoint_matches_backend_wire_contract(service):
    app = create_app(service)
    app.state.encoder = service.encoder
    with TestClient(app) as client:
        response = client.post("/embed", json={"inputs": ["历史影像", "医疗AI"]})
        assert response.status_code == 200
        assert response.json() == {"embeddings": [[0.6, 0.8], [1.0, 0.0]]}
        assert client.post("/embed", json={"inputs": []}).status_code == 422
