"""平台与工具路由规则表(S3 路径 A 的"智能客户端"规则,配置非代码)。

规则来自 data/config/platform_rules.json:
- tools: 工具选择(私域历史基线 vs 常规采集)——按缺口/提示词的关键词命中;
- platform_rules: R1-R5 平台规则(官方口径/大众报道/深度长文/微信生态/时效优先);
- excluded_platforms: 坚硬平台(微信正文/微博深度/小红书/抖音)不走 S3;
- fallback_platforms: 兜底联搜。
"""
from __future__ import annotations

from ..models.observation import CollectRequest
from ..store.ledger import normalize_text


class ToolRouter:
    def __init__(self, platform_rules: dict):
        self._cfg = platform_rules or {}

    def route(self, gap_text: str, hints: list[str], claim_text: str = "",
              limit: int = 10, search_context: dict | None = None) -> CollectRequest:
        """按规则表推导执行方式(决策 #11:S3 自己推导,决策单不带参数)。

        search_context = 信封精确定位字段 {buyer, project_no, published_date},
        随采集请求下传(定向检索锚点;采集适配器按需消费,空则退回宽词)。
        """
        blob = normalize_text(
            " ".join([gap_text or ""] + (hints or []) + [claim_text or ""])).casefold()

        tool = "search"
        for rule in self._cfg.get("tools", []):
            if rule.get("kind") == "history" and self._hit(blob, rule.get("match", [])):
                tool = "history_baseline"
                break

        # 平台选择对两种 tool 都执行(2026-10-16 live 验收修复):此前工具命中
        # history_baseline 后平台被整段短路为空,live 模式私域库不存在 → 采集
        # 零 HTTP 空转(51 次调用全空回执)。历史记录的权威源本就是公开政采源
        # (credibility_rules 权威根),私域工具语义只留给离线 FakeCollector
        # 按 tool 取数,不受 platforms 影响。
        platforms: list[str] = []
        for rule in self._cfg.get("platform_rules", []):
            if self._hit(blob, rule.get("match", [])):
                platforms = list(rule.get("platforms", []))
                break
        if not platforms:
            platforms = list(self._cfg.get("fallback_platforms", ["toutiao"]))
        excluded = set(self._cfg.get("excluded_platforms", []))
        platforms = [p for p in platforms if p not in excluded] or list(
            self._cfg.get("fallback_platforms", []))

        query = " ".join((hints or [claim_text])[:3]).strip() or claim_text
        ctx = search_context or {}
        # §八 契约:路由键确定性注入 query(零 LLM)——"自由词 | 编号:XXX |
        # 采购人:YYY"。检索键来自信封路由键(项目编号/采购人),自由词由 S2
        # 模型补充;适配器可解析编号/采购人做精确匹配,或取 | 前自由词搜索。
        routing = []
        if ctx.get("project_no"):
            routing.append(f"编号:{ctx['project_no']}")
        if ctx.get("buyer"):
            routing.append(f"采购人:{ctx['buyer']}")
        if routing:
            query = f"{query} | {' | '.join(routing)}"
        return CollectRequest(
            query=query, tool=tool, platforms=platforms, limit=limit,
            buyer=ctx.get("buyer", ""), project_no=ctx.get("project_no", ""),
            published_date=ctx.get("published_date", ""),
            source_url=ctx.get("source_url", ""))

    @staticmethod
    def _hit(blob: str, keywords: list[str]) -> bool:
        return any(normalize_text(k).casefold() in blob for k in keywords if k)
