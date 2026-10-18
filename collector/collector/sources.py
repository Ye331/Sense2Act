"""信息源 → 适配器:按 sources.adapter 选适配器,按 sources.config 抓取与筛选。

招标源用适配器的 harvest()(全量清单 + 8 态回执);新闻源按 config.queries 逐词检索后合并。
失败不伪装:整源抓取失败返回失败态,由 worker 如实回报,后端据此降级源健康度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sense2act.crawler import (AdapterResult, CcgpAdapter, CctvNewsAdapter, SogouWechatAdapter,
                               SouthWeekendAdapter, TianjinZfcgAdapter, ToutiaoAdapter, YifangbaoAdapter)


@dataclass(frozen=True)
class AdapterSpec:
    build: Callable[..., object]   # (http_get, config) -> 适配器实例
    doc_type: str                  # 默认文档类型
    detail: bool                   # 是否补抓详情页(正文 / 金额 / 快照)
    search: bool = False           # 检索型(按 config.queries 逐词搜)


ADAPTERS: dict[str, AdapterSpec] = {
    "ccgp": AdapterSpec(lambda get, c: CcgpAdapter(http_get=get, detail_cap=0, max_pages=c.pages),
                        "announcement", detail=True),
    "tianjin_zfcg": AdapterSpec(lambda get, c: TianjinZfcgAdapter(http_get=get), "announcement", detail=True),
    # 乙方宝详情在登录墙后:只收列表级,不补抓(尊重其商业模式,见适配器说明)
    "yifangbao": AdapterSpec(lambda get, c: YifangbaoAdapter(http_get=get, channels=tuple(c.channels)),
                             "announcement", detail=False),
    "toutiao": AdapterSpec(lambda get, c: ToutiaoAdapter(http_get=get), "news", detail=True, search=True),
    "south_weekend": AdapterSpec(lambda get, c: SouthWeekendAdapter(http_get=get), "news", detail=True, search=True),
    "cctv_news": AdapterSpec(lambda get, c: CctvNewsAdapter(http_get=get), "news", detail=True, search=True),
    # 微信正文属坚硬平台(红线):只收搜狗摘要,不抓正文
    "sogou_wechat": AdapterSpec(lambda get, c: SogouWechatAdapter(http_get=get), "news", detail=False, search=True),
}


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value or [] if str(v).strip()]


def _int(value, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SourceConfig:
    """sources.config(jsonb)里采集器认识的键;未知键忽略,缺省走默认值。"""

    max_items: int = 30            # 每轮最多处理的新条目(详情补抓的礼貌上限)
    keywords: list[str] = field(default_factory=list)          # 标题含任一即收;空 = 全收
    exclude_keywords: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)            # 招标阶段白名单(如 招标采购、采购意向)
    category: str | None = None    # 统一打的品目(后端按 机构+品目 维护金额基线)
    region: str | None = None      # 列表不带地域时的兜底(如天津政采 = 天津市)
    doc_type: str | None = None    # 覆盖适配器默认的文档类型
    detail: bool | None = None     # 覆盖适配器默认的详情补抓开关
    pages: int = 2                 # ccgp 每栏目回溯页数
    channels: list[str] = field(default_factory=list)          # 乙方宝 zbzt 频道路径
    queries: list[str] = field(default_factory=list)           # 新闻检索词
    per_query: int = 10

    @classmethod
    def parse(cls, raw: dict | None) -> "SourceConfig":
        raw = raw or {}
        detail = raw.get("detail")
        return cls(
            max_items=_int(raw.get("max_items"), 30, 1, 200),
            keywords=_strings(raw.get("keywords")),
            exclude_keywords=_strings(raw.get("exclude_keywords")),
            stages=_strings(raw.get("stages")),
            category=(str(raw["category"]).strip() or None) if raw.get("category") else None,
            region=(str(raw["region"]).strip() or None) if raw.get("region") else None,
            doc_type=raw.get("doc_type") if raw.get("doc_type") in ("announcement", "policy", "news") else None,
            detail=detail if isinstance(detail, bool) else None,
            pages=_int(raw.get("pages"), 2, 1, 22),
            channels=_strings(raw.get("channels")),
            queries=_strings(raw.get("queries")),
            per_query=_int(raw.get("per_query"), 10, 1, 50),
        )

    def accepts(self, item: dict) -> bool:
        title = str(item.get("title") or "").casefold()
        if self.keywords and not any(k.casefold() in title for k in self.keywords):
            return False
        if any(k.casefold() in title for k in self.exclude_keywords):
            return False
        return not self.stages or item.get("stage") in self.stages


def harvest(spec: AdapterSpec, http_get, config: SourceConfig) -> AdapterResult:
    adapter = spec.build(http_get, config)
    if not spec.search:
        return adapter.harvest()
    if not config.queries:
        return AdapterResult(status="failed", retryable=False, message="检索型新闻源需在 config.queries 配置检索词")
    items, seen, failures = [], set(), []
    for query in config.queries:
        result = adapter.search(query, config.per_query)
        if result.status not in ("success", "empty"):
            failures.append(result)
            continue
        for item in result.items:
            key = item.get("dedup_key") or item.get("url")
            if key and key not in seen:
                seen.add(key)
                items.append(item)
    if failures and len(failures) == len(config.queries):
        return failures[0]
    return AdapterResult(status="success" if items else "empty", items=items,
                         message=f"{len(failures)} 个检索词失败已跳过" if failures else "")
