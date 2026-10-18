"""调查信封(入口契约)。

两种形态:
- tender: 招标信号(《A text summary》信号发现层的 Top-K 输出;
  上游批量文件经 sense2act/assemble.py 确定性装配,白名单映射不走 LLM);
  detector 解释作为第一批证据入账(检测-调查置信贯通),
  basis_sufficient=False 登记"补历史基线"缺口(基线修补回注)。
- rumor: 传闻信号(供应商风险等叙事型爆料),带线索卡。

信封装配由代码完成:元数据走字段,不走 LLM。
"""
from __future__ import annotations

from .base import SchemaModel
from pydantic import Field


class DetectorScore(SchemaModel):
    score: float = Field(ge=0.0, le=1.0)
    explanation: str                 # 可解释触发理由,如"金额是历史中位数的4.2倍"
    basis_sufficient: bool = True    # 历史不足时 False(不强行评分,与三态纪律同构)


class SimilarProjectRef(SchemaModel):
    """历史最相似项目引用(上游新颖性检测的对照物)。"""
    project_id: str = ""
    title: str = ""
    publish_date: str = ""
    data_origin: str = ""            # real | simulated
    source_url: str = ""             # 上游为 null 时由装配器归一为空串


class AmountHistory(SchemaModel):
    """金额检测的历史依据(结构化,随信封进 S1 prompt;不进证据种子)。"""
    historical_median_amount_yuan: float | None = None
    amount_ratio: float | None = None
    history_count: int | None = None
    simulated_history_count: int | None = None


class NoveltyHistory(SchemaModel):
    """新颖性检测的历史依据。"""
    history_count: int | None = None
    simulated_history_count: int | None = None
    max_history_similarity: float | None = None
    most_similar_project: SimilarProjectRef | None = None


class FrequencyHistory(SchemaModel):
    """频率检测的历史依据。"""
    window_days: int | None = None
    recent_count: int | None = None
    historical_window_average_count: float | None = None
    frequency_ratio: float | None = None
    historical_windows_used: int | None = None


class TenderSignal(SchemaModel):
    project_id: str                  # 信号层归一后的项目 ID(生命周期续查的路由键)
    project_name: str
    purchaser: str                   # 采购单位(别名映射后)
    amount_yuan: float | None = None
    published_date: str              # YYYY-MM-DD
    domain: str = ""                 # 领域标注
    project_summary: str = ""
    source_url: str = ""             # 公告原始链接(预留锚定键,同 subjectCreditCode 定位)
    content_source_note: str = ""    # 上游内容来源说明(如"未抓取官方正文")
    company_profile: str = ""        # 上游相关性打分所用的企业画像
    amount_history: AmountHistory | None = None
    novelty_history: NoveltyHistory | None = None
    frequency_history: FrequencyHistory | None = None
    amount_anomaly: DetectorScore | None = None
    frequency_anomaly: DetectorScore | None = None
    novelty_anomaly: DetectorScore | None = None
    relevance: DetectorScore | None = None


class ClueCard(SchemaModel):
    clue_id: str
    source: str                      # 域名或平台名
    platform: str = "other"
    title: str = ""
    content: str
    url: str = ""
    published_at: str = ""
    author_type: str = "自媒体"      # 自媒体|媒体|官方|聚合站


class RumorSignal(SchemaModel):
    subject_name: str                # 监测对象名(如 供应商 B)
    subject_credit_code: str = ""    # 统一社会信用代码(外部锚定键)
    summary: str                     # 传闻叙事
    source: str
    signal_time: str
    watch_focus: str = ""            # 监测重点(喂 S1 调查指令)
    clues: list[ClueCard] = []


class InvestigationEnvelope(SchemaModel):
    kind: str                        # tender | rumor
    event_id: str = ""               # 留空则代码派生(项目 ID 哈希)
    tender: TenderSignal | None = None
    rumor: RumorSignal | None = None

    def signal_label(self) -> str:
        if self.kind == "tender" and self.tender:
            return self.tender.project_name
        if self.rumor:
            return self.rumor.summary[:60]
        return "(空信封)"

    def derive_event_id(self) -> str:
        """event_id 留空时的派生规则(单一事实源:cli 与 assemble 共用)。"""
        if self.kind == "tender" and self.tender:
            return "evt_" + "".join(
                ch if ch.isalnum() else "_" for ch in self.tender.project_id)[:32]
        return "evt_signal"
