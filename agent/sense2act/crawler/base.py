"""适配器基类与结果契约(8 态语义与 crawler_tool 对齐)。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# 8 态:success/empty/authentication_required/rate_limited/parser_changed/
#       network_error/source_unavailable/failed(与 SourceReport.status 对齐)


@dataclass
class AdapterResult:
    status: str
    items: list[dict] = field(default_factory=list)   # evidence-lite 形态
    retryable: bool | None = None
    message: str = ""


class SourceAdapter(ABC):
    """采集适配器:一个平台一个类;只依赖注入的 http_get,不自建连接。

    纪律(沿 crawler_tool):失败回执与成功同构,绝不把失败伪装成 empty;
    页面级解析失败 -> parser_changed;验证码/登录 -> authentication_required。
    """

    platform: str
    adapter_version: str

    @abstractmethod
    def search(self, query: str, limit: int) -> AdapterResult: ...

    def health(self) -> dict:
        return {"platform": self.platform,
                "adapter_version": self.adapter_version,
                "status": "unknown"}
