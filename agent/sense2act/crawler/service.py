"""本地采集服务:多平台扇出 -> 去重合并 -> 8 态回执汇总。

对外接口与闸门同构(send/calls/remaining/save),S3 Executor 无感切换
三档采集(fixture / 远程 crawler 服务 / 本地爬虫框架)。
聚合口径沿 crawler_tool:任一回执非 success/empty -> partial;
有失败且零条目 -> failed。
"""
from __future__ import annotations

from ..models.observation import (
    CollectReceipt,
    CollectRequest,
    EvidenceItemLite,
    SourceReport,
)
from .registry import SourceRegistry


class LocalCollectService:
    def __init__(self, registry: SourceRegistry, egress=None, max_calls: int = 80):
        self._registry = registry
        self._egress = egress          # EgressHttpClient(录制/回放挂在真出网点)
        self._max_calls = max_calls
        self.calls = 0

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self.calls)

    def send(self, req: CollectRequest) -> CollectReceipt:
        if self.calls >= self._max_calls:
            return CollectReceipt(request=req, items=[], tool=req.tool,
                                  source_reports=[], status="failed")
        self.calls += 1
        # 仅当确实无平台可去时才早退(2026-10-16 live 验收修复):此前 tool
        # 先判无条件早退,history_baseline 请求即使带平台也零 HTTP——CRAG
        # 回边换来的 fallback 平台同样被挡在门外。私域历史库待接的诚实回执
        # 口子保留(真无私域源时如实 source_unavailable,不伪装)。
        if req.tool == "history_baseline" and not req.platforms:
            return CollectReceipt(
                request=req, items=[], tool=req.tool,
                source_reports=[SourceReport(
                    platform="internal_history", status="source_unavailable",
                    count=0, retryable=False,
                    message="本地框架暂无内部历史库工具(私域数据待接)")],
                status="empty")

        platforms = req.platforms or self._registry.defaults()
        items: list[EvidenceItemLite] = []
        reports: list[SourceReport] = []
        seen: set[str] = set()
        for platform in platforms:
            try:
                adapter = self._registry.create(platform)
                result = self._call_search(adapter, req)
            except KeyError:
                reports.append(SourceReport(
                    platform=platform, status="source_unavailable", count=0,
                    retryable=False, message="未注册的平台"))
                continue
            except Exception as exc:
                reports.append(SourceReport(
                    platform=platform, status="failed", count=0, retryable=True,
                    message=type(exc).__name__))
                continue
            kept = 0
            for raw in result.items[: max(0, req.limit)]:
                lite = EvidenceItemLite.model_validate(raw)
                key = lite.dedup_key or f"{lite.source}|{lite.title}"
                if key in seen:
                    continue
                seen.add(key)
                items.append(lite)
                kept += 1
            reports.append(SourceReport(
                platform=platform, status=result.status, count=kept,
                retryable=result.retryable, message=result.message or ""))

        has_fail = any(r.status not in ("success", "empty") for r in reports)
        if has_fail and not items:
            status = "failed"
        elif has_fail:
            status = "partial"
        else:
            status = "success" if items else "empty"
        return CollectReceipt(request=req, items=items, source_reports=reports,
                              status=status, tool=req.tool)

    def save(self) -> None:
        """与闸门接口对齐:出网录制由 EgressHttpClient 边跑边写,此处兜底落盘。"""
        if self._egress is not None and hasattr(self._egress, "save"):
            self._egress.save()

    @staticmethod
    def _call_search(adapter, req: CollectRequest):
        """定向检索锚点(采购人/项目编号/发布日期/信封自带 URL)按需下传:
        只有声明了这些参数的适配器(如 ccgp)才接收,其余源忽略(避免签名不匹配)。"""
        import inspect
        sig = inspect.signature(adapter.search)
        kwargs = {}
        if "buyer" in sig.parameters:
            kwargs["buyer"] = req.buyer
            kwargs["project_no"] = req.project_no
            kwargs["published_date"] = req.published_date
        if "source_url" in sig.parameters:
            kwargs["source_url"] = req.source_url
        return adapter.search(req.query, req.limit, **kwargs)
