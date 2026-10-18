"""后端 §9.1 爬虫接入面:领取到期源 → 批量推文档 → 回报运行结果。

传输与错误处理复用 agent 的 BackendClient(标准库 urllib,code != 0 抛 BackendError)。
"""
from __future__ import annotations

from sense2act.bridge.client import BackendClient, BackendError

__all__ = ["BackendError", "IngestClient"]


class IngestClient(BackendClient):
    def ingest_queue(self) -> list[dict]:
        return self._call("GET", "/internal/ingest-queue")["items"]

    def push_documents(self, source_id: str, items: list[dict]) -> dict:
        return self._call("POST", "/internal/documents", {"source_id": source_id, "items": items})

    def report_run(self, source_id: str, ok: bool, stats: dict, errors: list[dict]) -> dict:
        return self._call("POST", "/internal/ingest-runs",
                          {"source_id": source_id, "ok": ok, "stats": stats, "errors": errors})
