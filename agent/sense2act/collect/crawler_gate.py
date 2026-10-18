"""P0 回放闸门(本项目的采集边界版):LIVE 录制 / REPLAY 回放 / 指纹寻址。

四不变式(架构决策 #15,落在本项目真实边界上——Sense2act 与采集服务的
HTTP 交互,而不是 crawler_tool 内部的出网点):
I1 唯一出口——S3 的真实采集必经本闸门;
I2 replay 零网络——REPLAY 模式查指纹命中抄答案,未命中抛 NotRecordedError;
I3 录制完整性——LIVE 模式成败都录(save() 由 CLI 在 finally 调用);
I4 确定性——replay 下同请求指纹字节级同响应(对比实验可归因)。

录制文件格式(JSON):{fingerprint: {fingerprint, request, receipt}},指纹=
请求规范 JSON(排序键)的 sha256 前 16 位。文件随跑随写,防崩溃丢失。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models.observation import CollectReceipt, CollectRequest


def request_fingerprint(req: CollectRequest) -> str:
    canonical = json.dumps(
        {"tool": req.tool, "query": req.query,
         "platforms": sorted(req.platforms), "limit": req.limit},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class CrawlerGate:
    """三形态:live+record / 纯 replay / live 直通(不录)。"""

    def __init__(self, client=None, *, record_path: str | Path | None = None,
                 replay_path: str | Path | None = None, max_calls: int = 80):
        if replay_path and client is not None:
            raise ValueError("REPLAY 闸门不接受 client(replay 零网络,I2)")
        if record_path and replay_path:
            raise ValueError("record 与 replay 互斥")
        self._client = client
        self._record_path = Path(record_path) if record_path else None
        self.calls = 0
        self._max_calls = max_calls
        self._pending: dict[str, dict] = {}
        self._records: dict[str, dict] = {}
        if replay_path:
            p = Path(replay_path)
            if not p.exists():
                raise FileNotFoundError(f"采集回放文件不存在: {p}")
            self._records = {
                k: v for k, v in json.loads(
                    p.read_text(encoding="utf-8-sig")).items()}

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self.calls)

    def send(self, req: CollectRequest, *, mode: str = "LIVE") -> CollectReceipt:
        fp = request_fingerprint(req)
        if self._records:
            rec = self._records.get(fp)
            if rec is None:
                raise NotRecordedError(
                    f"采集回放未命中指纹 {fp}(query={req.query!r})——"
                    f"请在录制模式下重跑该查询")
            self.calls += 1
            return CollectReceipt.model_validate(rec["receipt"])
        if self.calls >= self._max_calls:
            return CollectReceipt(
                request=req, items=[], tool=req.tool,
                source_reports=[], status="failed")
        self.calls += 1
        receipt = self._client.search(req)
        if self._record_path is not None:
            self._pending[fp] = {
                "fingerprint": fp,
                "request": req.model_dump(by_alias=True),
                "receipt": receipt.model_dump(by_alias=True),
            }
            self._flush()
        return receipt

    def _flush(self) -> None:
        if self._record_path is None:
            return
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        self._record_path.write_text(
            json.dumps(self._pending, ensure_ascii=False, indent=1),
            encoding="utf-8")

    # 兼容 OfflineGate 的接口面
    def save(self) -> None:
        self._flush()


class NotRecordedError(RuntimeError):
    pass
