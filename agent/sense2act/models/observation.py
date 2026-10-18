"""S3 观察包(三成员五规约:纯代码组装,S4 的唯一感知界面)。"""
from __future__ import annotations

from pydantic import Field

from .base import SchemaModel


class CollectRequest(SchemaModel):
    query: str
    tool: str = "search"                      # search | history_baseline
    platforms: list[str] = []
    limit: int = 10
    # 定向检索锚点(信封精确定位字段,采集适配器按需消费;空=退回宽词过滤)
    buyer: str = ""                           # 采购人(精确子串匹配)
    project_no: str = ""                      # 项目编号(精确子串匹配)
    published_date: str = ""                  # 发布日期(翻页回溯的日期锚点)
    source_url: str = ""                      # 信封自带目标公告 URL(直抓 first-hop)


class SourceReport(SchemaModel):
    """采集回执:8 态 + retryable;失败回执与成功同构入包(失败不伪装)。

    message 对齐 crawler_tool 契约(远程回执含该字段,extra=forbid 下必须声明)。
    """

    platform: str
    status: str                               # success/empty/.../failed(8 值)
    count: int = 0
    retryable: bool | None = None
    message: str = ""


class EvidenceItemLite(SchemaModel):
    """采集到的原始条目(S4 加工前)。"""

    evidence_id: str = ""                     # 入账时代码派生
    platform: str
    source: str                               # 域名
    url: str = ""
    title: str = ""
    content: str
    published_at: str = ""
    author_type: str = ""
    dedup_key: str = ""                       # 采集侧去重键(无则 S5 计算)


class CollectReceipt(SchemaModel):
    request: CollectRequest
    items: list[EvidenceItemLite] = []
    source_reports: list[SourceReport] = []
    status: str = "success"                   # success | partial | failed
    tool: str = "search"


class TrajectoryStep(SchemaModel):
    """探索器轨迹(仅 explore 轮附;旁路落日志供评测与下轮 S2 历史摘要)。"""

    desc: str
    depth: int = 0


class ObservationPackage(SchemaModel):
    """观察包 = 本轮全部观察的唯一界面。

    五规约:O1 纯代码零 LLM;O2 完整性(失败来源以回执入包);
    O3 可溯源(URL/摘录/抓取时间);O4 轨迹条件性;O5 唯一界面。
    """

    round: int
    main_target: str
    action_brief: str = ""
    items: list[EvidenceItemLite] = []
    source_reports: list[SourceReport] = []
    trajectory: list[TrajectoryStep] | None = None
