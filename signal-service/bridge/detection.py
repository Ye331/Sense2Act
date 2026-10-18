"""后端检测队列 → 招投标检测 → 信号回推(契约 Backend/docs/api-design.md §9.2)。

    GET  /internal/detection-queue         待扫描文档 + 启用规则 + 关注画像
    GET  /documents?org_id=&date_to=       同单位历史(检测需要真实历史,严格早于检测日)
    POST /internal/signals                 入选 Top-K 的命中;后端按阈值自动立项调查
    POST /internal/detection-scan-complete 本批全部处理完毕(有无命中都要标)

检测算法本身不改:复用 signals.tender 的 SignalDetector,这里只做数据进出。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

import httpx

from signals.tender.schemas import Announcement, DetectionRequest
from signals.tender.service import TenderService
from .hits import record_hits

log = logging.getLogger("signal.bridge")
DETECTOR = "signal-service/tender-v0.2"
HISTORY_DAYS = 730


class BackendClient:
    def __init__(self, base_url: str, internal_key: str, transport=None, timeout: float = 30):
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", timeout=timeout, transport=transport,
                                  headers={"X-Internal-Key": internal_key})

    def _call(self, method: str, path: str, **kwargs):
        response = self._http.request(method, path, **kwargs)
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"{method} {path} -> [{body.get('code')}] {body.get('message')}")
        return body["data"]

    def detection_queue(self, limit: int) -> list[dict]:
        return self._call("GET", "/internal/detection-queue", params={"limit": limit})["items"]

    def history(self, org_id: str, before: date) -> list[dict]:
        rows, page = [], 1
        while True:
            data = self._call("GET", "/documents", params={
                "org_id": org_id, "date_from": (before - timedelta(days=HISTORY_DAYS)).isoformat(),
                "date_to": (before - timedelta(days=1)).isoformat(), "page": page, "page_size": 100})
            rows += data["items"]
            if len(rows) >= data["total"] or not data["items"] or page >= 10:
                return rows
            page += 1

    def push_signal(self, document_id: str, hits: list[dict], score: float) -> dict:
        return self._call("POST", "/internal/signals", json={
            "document": {"id": document_id}, "hits": hits, "score": score, "detector": DETECTOR})

    def scan_complete(self, document_ids: list[str]) -> None:
        if document_ids:
            self._call("POST", "/internal/detection-scan-complete", json={"document_ids": document_ids, "detector": DETECTOR})


def _announcement(doc: dict, summary: str = "") -> Announcement | None:
    """后端文档 → 检测输入;缺发布日期的文档无法定位检测日,跳过。"""
    if not doc.get("publish_date"):
        return None
    org = doc.get("org") or {}
    return Announcement(
        id=doc["id"], title=doc.get("title") or doc["id"], buyer=doc.get("org_name") or org.get("name") or "",
        publish_date=doc["publish_date"], amount=float(doc["amount"]) if doc.get("amount") not in (None, "") else None,
        project_summary=summary or doc.get("title") or "", source_url=doc.get("url") or "", category=doc.get("category") or "")


class DetectionBridge:
    def __init__(self, client: BackendClient, service: TenderService, default_profile: str, company_id: str = "platform"):
        self.client, self.service = client, service
        self.default_profile, self.company_id = default_profile, company_id

    def run_once(self, limit: int = 50) -> dict:
        items = self.client.detection_queue(limit)
        if not items:
            return {"scanned": 0, "signals": 0}
        profiles = items[0].get("profiles") or []
        profile = ("关注方向:" + "、".join(profiles)) if profiles else self.default_profile
        rules = {rule["type"]: rule for rule in items[0].get("rules") or [] if rule.get("enabled", True)}

        by_day: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            doc = item["document"]
            # 检测只覆盖招投标公告;其他类型文档(政策/新闻)本服务不判定,照常标记已扫描
            if doc.get("doc_type") == "announcement" and doc.get("publish_date"):
                by_day[doc["publish_date"]].append(doc)

        signals = 0
        for day, docs in sorted(by_day.items()):
            signals += self._detect_day(date.fromisoformat(day), docs, profile, rules)
        self.client.scan_complete([item["document"]["id"] for item in items])
        return {"scanned": len(items), "signals": signals}

    def _detect_day(self, day: date, docs: list[dict], profile: str, rules: dict) -> int:
        current = [a for d in docs if (a := _announcement(d, (d.get("content_text") or "")[:800]))]
        history, seen = [], set()
        for org_id in {d.get("org_id") for d in docs if d.get("org_id")}:
            for row in self.client.history(org_id, day):
                if row["id"] not in seen and (a := _announcement(row)) and a.publish_date < day:
                    seen.add(row["id"])
                    history.append(a)
        start = min([a.publish_date for a in history], default=day - timedelta(days=HISTORY_DAYS))
        request = DetectionRequest(company_id=self.company_id, company_profile=profile, detection_date=day,
                                   history_start_date=start, history=history, current=current)
        response = self.service.detect(request)
        weights = self.service.config["priority_weights"]
        pushed = 0
        for record in response.records:
            if not record.get("is_signal"):
                continue
            hits = record_hits(record, weights, profile, rules)
            result = self.client.push_signal(record["project_id"], hits, round(float(record["priority_score"]), 4))
            log.info("signal %s doc=%s score=%.3f auto_investigated=%s", result["signal_id"], record["project_id"],
                     record["priority_score"], result.get("auto_investigated"))
            pushed += 1
        return pushed
