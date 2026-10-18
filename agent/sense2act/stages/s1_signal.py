"""S1 信号分析与问题生成。

S1a 轻分析:快速定性(信号类型 + 假设种子选择 + 依据不足缺口)。
  入口澄清已退役(前置信号发现层的相关性过滤接管"该不该查"),
  仅极严条件由 pipeline 触发(见 investigation.py)。
S1b 重分析:调查感知 claim 分解——claim 出生即带调查属性
  (优先级=check-worthiness x 解锁效应 x 时效衰减 + 假设归属 + 节映射)。

信封侧证据种子(检测-调查置信贯通)由纯代码装配:detector 解释与线索卡
直接作为第一批证据,谱系根=signal_layer / 线索源域名。
"""
from __future__ import annotations

import json
from typing import Protocol

from ..llm import StructuredLLM, call_structured
from ..models.base import ClaimCategory
from ..models.envelope import InvestigationEnvelope, TenderSignal
from ..models.intel import AnalysisBlueprint, ClaimDraft, QualificationResult
from ..prompts import S1A_PROMPT, s1b_prompt


class SignalAnalyzer(Protocol):
    def analyze_light(self, envelope: InvestigationEnvelope,
                      seed_labels: list[str]) -> QualificationResult: ...

    def analyze_deep(self, envelope: InvestigationEnvelope,
                     qualification: QualificationResult,
                     relation_vocab: list[str]) -> AnalysisBlueprint: ...


class LLMSignalAnalyzer:
    def __init__(self, llm: StructuredLLM, retry: int = 2, variant: str = "中"):
        self._llm = llm
        self._retry = retry
        # S1b 粒度档位在构造时固化(未知档位此处显式报错,不延迟到首轮调用)
        self._s1b_system = s1b_prompt(variant)

    def analyze_light(self, envelope: InvestigationEnvelope,
                      seed_labels: list[str]) -> QualificationResult:
        user = (
            "信号信封:\n"
            + json.dumps(envelope.model_dump(by_alias=True), ensure_ascii=False)
            + "\n\n可选假设种子标签(只能从中选):\n"
            + json.dumps(seed_labels, ensure_ascii=False)
        )
        return call_structured(self._llm, stage="s1a", system=S1A_PROMPT, user=user,
                               model_cls=QualificationResult, retry=self._retry)

    def analyze_deep(self, envelope: InvestigationEnvelope,
                     qualification: QualificationResult,
                     relation_vocab: list[str]) -> AnalysisBlueprint:
        user = (
            "信号信封:\n"
            + json.dumps(envelope.model_dump(by_alias=True), ensure_ascii=False)
            + "\n\n已定性的解读:\n"
            + json.dumps(qualification.model_dump(by_alias=True), ensure_ascii=False)
            + "\n\n报告固定节(claim 的 section_index 只能取 0-3):\n"
            + json.dumps(
                ["0 主体与事件背景", "1 动态时间线与关联方", "2 核心事实", "3 结论"],
                ensure_ascii=False)
            + "\n\n关系类型词表(关系类 claim 的 relationType 须命中):\n"
            + json.dumps(relation_vocab, ensure_ascii=False)
        )
        return call_structured(self._llm, stage="s1b", system=self._s1b_system,
                               user=user,
                               model_cls=AnalysisBlueprint, retry=self._retry)


def tender_field_claims(t: TenderSignal | None) -> list[ClaimDraft]:
    """机械生成 tender 商机字段 claim(世界断言,corpus_bound=False)。

    六字段:预算/投标截止/获取招标文件截止/代理机构/联系人/采购内容。
    信封有值的用值(预算/采购内容),无值的用"见官方公告"弱断言(投标截止/
    代理机构/联系人)——采集 ccgp 详情后,正文级证据经权威根单源例外证实,
    简报商机字段从"摘要级/未知"升"正文级已证实"。
    id 由调用方续接 S1b 编号(此处留空)。
    """
    if not t:
        return []
    amount_text = "见官方公告"
    if t.amount_yuan:
        fv = float(t.amount_yuan)
        amount_text = f"{int(fv) if fv.is_integer() else fv} 元"
    fields = [
        ("预算金额", amount_text),
        ("投标截止时间", "见官方公告"),
        ("获取招标文件截止时间", "见官方公告"),
        ("采购代理机构", "见官方公告"),
        ("采购联系人", "见官方公告"),
        ("采购内容", t.project_summary or t.project_name),
    ]
    return [
        ClaimDraft(
            id="",
            text=f"本项目{label}为 {value}",
            category=ClaimCategory.FACT,
            section_index=2,              # 核心事实节
            priority=4,
            priority_reason="商机简报字段,正文级证实",
            entity_names=[t.purchaser] if t.purchaser else [],
            corpus_bound=False,           # 世界断言,非信封自证
        )
        for label, value in fields
    ]


def envelope_evidence_seeds(envelope: InvestigationEnvelope,
                            include_metadata: bool = False) -> list[dict]:
    """信封侧证据种子(纯代码,无 LLM):

    - tender: detector 解释各成一条(检测-调查置信贯通,谱系根=signal_layer);
    - rumor: 线索卡各一条(谱系根由 S4 按线索源域名打标);
    - rumor 元数据种子(include_metadata=True,2026-10-10):信封自述字段的
      忠实文本,供 corpus_bound claim 走自证例外——默认关(录制件配对纪律),
      由 --rumor-meta-seed 启用。
    """
    items: list[dict] = []
    if envelope.kind == "tender" and envelope.tender:
        t = envelope.tender
        # 信封基础字段(第一方结构化事实,先于检测器入账)
        items.append({
            "platform": "signal_layer", "source": "signal_layer",
            "title": "信号层信封基础字段",
            "content": (
                f"招标信号基础字段:项目编号 {t.project_id};项目名称 {t.project_name};"
                f"采购方 {t.purchaser};预算金额 {t.amount_yuan} 元;发布日期 "
                f"{t.published_date};领域 {t.domain or '(未标注)'}。"
                f"摘要:{t.project_summary}"),
            "lineage_root": "signal_layer", "author_type": "内部检测",
            "credibility": "中", "url": "", "published_at": t.published_date,
        })
        for name, det in (
            ("金额异常", t.amount_anomaly), ("频率异常", t.frequency_anomaly),
            ("新颖性", t.novelty_anomaly), ("相关性", t.relevance),
        ):
            if det is None:
                continue
            items.append({
                "platform": "signal_layer", "source": "signal_layer",
                "title": f"信号层检测器:{name}",
                "content": (
                    f"{t.purchaser} 的采购项目『{t.project_name}』触发{name}检测:"
                    f"{det.explanation}(评分 {det.score};"
                    + ("历史依据充分" if det.basis_sufficient else "历史依据不足,不强行评分")
                    + ")。"),
                "lineage_root": "signal_layer",
                "author_type": "内部检测",
                "credibility": "中",
                "url": "", "published_at": t.published_date,
            })
    elif envelope.rumor:
        r = envelope.rumor
        for clue in r.clues:
            items.append({
                "platform": clue.platform, "source": clue.source,
                "title": clue.title or "(线索卡)",
                "content": clue.content,
                "lineage_root": clue.source,  # S4 会按谱系表上溯复核
                "author_type": clue.author_type, "credibility": "低",
                "url": clue.url, "published_at": clue.published_at,
            })
        # 信封元数据种子(rumor 侧对称 tender 基础字段;2026-10-10):主体/摘要/
        # 时间/监测重点等信封自述字段是 corpus_bound claim 的忠实文本,无种子
        # 则 S6 意见全投"无关"、自证例外永不触发(实测:rumor 全 14 条复述
        # 滞留待证实)。追加在线索卡之后;platform=signal_layer 使 corpus 直挂
        # 通道(associate_backlog ②)可用。no_extract=True:内部记录不经 S4
        # 实体抽取(信封"提到"的实体不是该源的报道实体,入实体交集会伪造挂账
        # 谱系),只走 ② 直挂道——零元数据误挂。
        if include_metadata:
            items.append({
                "platform": "signal_layer", "source": "signal_layer",
                "title": "信号层信封元数据",
                "content": (
                    f"rumor 信封元数据:监测主体 {r.subject_name};"
                    f"统一信用代码 {r.subject_credit_code or '(未提供)'};"
                    f"事件摘要:{r.summary};主要来源 {r.source};"
                    f"信号识别时间 {r.signal_time}"
                    + (f";监测重点:{r.watch_focus}" if r.watch_focus else "")
                    + f";线索 {len(r.clues)} 条。"),
                "lineage_root": "signal_layer", "author_type": "内部检测",
                "credibility": "中", "url": "", "published_at": r.signal_time,
                "no_extract": True,
            })
    return items
