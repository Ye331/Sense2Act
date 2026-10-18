"""离线采集替身:FakeCollector(evidence_seed.json 驱动)+ FakeHistoryBaseline。

seed 格式(examples/*/evidence_seed.json):
{
  "batches": [ {"keys": ["关键词A", "关键词B"], "items": [ {EvidenceItemLite 字段} ],
                "reports": [可选 SourceReport 覆盖,故障注入用]} ],
  "history": { "单位名": {"items": [ {EvidenceItemLite 字段} ]} }
}
匹配规则(规范化后双向包含):batch.keys 任一 k 满足 k in query 或 query in k。
失败回执与成功同构(失败不伪装成 empty);未命中批次 -> empty 回执。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models.observation import (
    CollectReceipt,
    CollectRequest,
    EvidenceItemLite,
    SourceReport,
)
from ..store.ledger import normalize_text


def _norm(s: str) -> str:
    return normalize_text(s).casefold()


class FakeCollector:
    def __init__(self, seed: dict | str | Path):
        if isinstance(seed, (str, Path)):
            seed = json.loads(Path(seed).read_text(encoding="utf-8-sig"))
        self._batches = seed.get("batches", [])
        self._history = seed.get("history", {})

    def search(self, request: CollectRequest) -> CollectReceipt:
        if request.tool == "history_baseline":
            return self._search_history(request)
        q = _norm(request.query)
        for batch in self._batches:
            keys = [_norm(k) for k in batch.get("keys", [])]
            if any(k and (k in q or q in k) for k in keys):
                return self._receipt(request, batch)
        return CollectReceipt(
            request=request, items=[], tool=request.tool,
            source_reports=[SourceReport(
                platform=request.platforms[0] if request.platforms else "fallback",
                status="empty", count=0, retryable=False)],
            status="empty" if request.tool == "search" else "empty",
        )

    def _search_history(self, request: CollectRequest) -> CollectReceipt:
        q = _norm(request.query)
        for unit, payload in self._history.items():
            if _norm(unit) and (_norm(unit) in q or q in _norm(unit)):
                batch = {"items": payload.get("items", [])}
                return self._receipt(request, batch, tool="history_baseline",
                                     platform="internal_history")
        return CollectReceipt(
            request=request, items=[], tool="history_baseline",
            source_reports=[SourceReport(
                platform="internal_history", status="empty", count=0,
                retryable=False)],
            status="empty",
        )

    def _receipt(self, request: CollectRequest, batch: dict, *,
                 tool: str = "search", platform: str = "") -> CollectReceipt:
        items: list[EvidenceItemLite] = []
        for raw in batch.get("items", []):
            data = dict(raw)
            data.setdefault("platform", platform or data.get("platform", "toutiao"))
            data.setdefault("source", data.get("source", data.get("platform", "")))
            items.append(EvidenceItemLite.model_validate(data))
        if "reports" in batch:
            reports = [SourceReport.model_validate(r) for r in batch["reports"]]
            status = "failed" if all(
                r.status not in ("success", "empty") for r in reports) and not items \
                else ("partial" if any(r.status not in ("success", "empty")
                                       for r in reports) else "success")
        else:
            plats = sorted({i.platform for i in items}) or (
                [platform] if platform else request.platforms or ["fallback"])
            reports = [SourceReport(platform=p, status="success",
                                    count=sum(1 for i in items if i.platform == p),
                                    retryable=False) for p in plats]
            status = "success" if items else "empty"
        return CollectReceipt(request=request, items=items, source_reports=reports,
                              status=status, tool=tool)
