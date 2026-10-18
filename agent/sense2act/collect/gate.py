"""出网闸门(network_gate 骨架版)。

四不变式(正式版实现承诺,骨架期以接口纪律存在):
I1 唯一出口——任何网络访问必经 send,绕过即缺陷;
I2 replay 零网络——REPLAY 模式下查指纹命中抄答案,未命中抛 NotRecordedError;
I3 录制完整性——LIVE 模式成败都落盘录制;
I4 确定性——replay 下同请求字节级同响应(对比实验可归因的形式化保证)。

骨架期 OfflineGate 直接透传(零网络:采集本身来自 FakeCollector),
调用计数保留,闸门接入点已焊死(S3 只经闸门拿回执)。
"""
from __future__ import annotations

from typing import Protocol

from ..models.observation import CollectReceipt, CollectRequest


class NetworkGate(Protocol):
    def send(self, req: CollectRequest, *, mode: str = "LIVE") -> CollectReceipt: ...


class OfflineGate:
    """骨架版闸门:透传 + 调用计数(采集护栏 max_collect_calls 在此 enforcement)。"""

    def __init__(self, collector, max_calls: int = 40):
        self._collector = collector
        self._max_calls = max_calls
        self.calls = 0

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self.calls)

    def send(self, req: CollectRequest, *, mode: str = "LIVE") -> CollectReceipt:
        if self.calls >= self._max_calls:
            return CollectReceipt(
                request=req, items=[], tool=req.tool,
                source_reports=[], status="failed")
        self.calls += 1
        return self._collector.search(req)
