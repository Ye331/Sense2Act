"""Sense2Act 后端内部 API 客户端(契约:Backend/docs/api-design.md §9.3)。

只用标准库:Agent 的离线回放环境不必额外装 HTTP 依赖。
所有响应是 {code, message, data};code != 0 统一抛 BackendError,调用方按业务码分支。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

CONFLICT = 40901        # 状态冲突:调查已被占用/已终态(用户停止后迟到写入)
UNPROCESSABLE = 42201   # 业务规则拒绝:超轮次/超预算/证据不可登记
RATE_LIMITED = 42901


class BackendError(RuntimeError):
    def __init__(self, code: int, message: str, status: int = 0):
        super().__init__(f"[{code}] {message}")
        self.code, self.message, self.status = code, message, status


class BackendClient:
    def __init__(self, base_url: str, internal_key: str, timeout: float = 30):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.key = internal_key
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None, query: dict | None = None):
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method, headers={
            "X-Internal-Key": self.key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:   # 4xx/5xx 也带统一响应包
            status, raw = exc.code, exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise BackendError(50001, f"响应不是 JSON(HTTP {status})", status) from None
        if payload.get("code", 0) != 0:
            raise BackendError(int(payload["code"]), str(payload.get("message", "")), status)
        return payload.get("data")

    # ---- 调查生命周期 ----
    def investigations(self, status: str) -> list[dict]:
        return self._call("GET", "/internal/investigations", query={"status": status})["items"]

    def start(self, inv_id: str) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/start")

    def context(self, inv_id: str) -> dict:
        return self._call("GET", f"/internal/investigations/{inv_id}/context")

    def step(self, inv_id: str, event: str, round_no: int, payload: dict,
             token_usage: int = 0, request_id: str | None = None) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/steps", {
            "event": event, "round": round_no, "payload": payload,
            "token_usage": token_usage, "request_id": request_id})

    def create_question(self, inv_id: str, text: str, round_no: int) -> dict:
        return self._call("POST", "/internal/questions", {
            "investigation_id": inv_id, "text": text, "raised_in_round": round_no})

    def update_question(self, question_id: str, status: str, answer_summary: str,
                        evidence_ids: list[str]) -> dict:
        return self._call("PATCH", f"/internal/questions/{question_id}", {
            "status": status, "answer_summary": answer_summary, "evidence_ids": evidence_ids})

    def register_evidence(self, inv_id: str, **fields) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/evidences", fields)

    def submit_report(self, inv_id: str, draft: dict) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/report", draft)

    def complete(self, inv_id: str) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/complete")

    def fail(self, inv_id: str, error: str) -> dict:
        return self._call("POST", f"/internal/investigations/{inv_id}/fail", {"error": error[:500]})
