"""运行配置与域配置加载。

两类配置严格分离(架构第十三节"域配置与认识论分离"):
- InvestigationConfig: 循环机制参数(换领域不动);
- DomainConfig(data/config/*.json): 域内容(词表/规则表/监测名单),换行业只换这些文件。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 六节固定大纲(代码插入,LLM 不改顶层;Open Questions 与行动建议排除在 D 钩子外)
FIXED_SECTIONS: list[tuple[str, str]] = [
    ("主体与事件背景", "standard"),
    ("动态时间线与关联方", "standard"),
    ("核心事实", "standard"),
    ("结论", "standard"),
    ("Open Questions", "open_questions"),
    ("行动建议", "recommendation"),
]


@dataclass
class InvestigationConfig:
    """循环机制参数(认识论层,与领域无关)。"""

    max_rounds: int = 6                 # 轮数上限 N(硬停兜底)
    verdict_scope: str = "touched"      # touched=本轮涉证据+被刷新 / all=每轮全量(文档口径)
    interleave: bool = True             # False 退化为"查完再写"(v2 行为,对照实验用)
    deployment_mode: str = "report_only"  # action_facing 时 STALE 节核对通过前禁止交付
    explore_depth_max: int = 3          # 探索器递归深度上限
    explore_calls_max: int = 15         # 探索器调用上限
    evidence_topk: int = 5              # 起草/卷宗喂给模型的每 claim 证据条数
    ask_max_per_task: int = 2           # 循环内"问人"上限
    llm_retry: int = 2                  # 各槽位校验失败 re-prompt 次数(后降级,不卡死)
    max_llm_calls: int = 160            # 预算护栏:耗尽则优雅停止,按现有台账出部分报告
    max_collect_calls: int = 80         # 采集调用护栏(探索器换词重试会放大调用量)
    craq_retry: int = 1                 # S4 质量差回 S3 重查次数(不消耗新轮)
    assoc_mode: str = "entity"          # entity=实体名重合(回放基线)/
                                        # entity_text=+词面重合(真跑建议;宽进)
    disc_enabled: bool = True           # S2 区分度输入开关(改造②;off=盲规划对照,
                                        # sweep3d A/B 用:live 采样方差大,效应验收需开关化)
    dry_stop_rounds: int = 3            # 代码兜底停:连续 N 轮零新增证据/新 claim → 强制停
                                        # (S2 停与换纪律的代码第二道;0=关。数据背书:
                                        # 12 轮实验 85% 轮次零新增证据,模型主动停仅 1/71)
    calibration_enabled: bool = False   # 改造④规划校准率:采集后对 S2 验收标准判型回填
    rumor_meta_seed: bool = False       # rumor 信封元数据种子(2026-10-10):默认关——
                                        # 证据环境变更会使冻结录制件卷宗截片错位
                                        # (录制件配对纪律);live 验证/下批重录后转正
                                        # (先判后用不改判证据;默认关——判型是新 stage,
                                        # 旧录制件零影响,开启录制后回放须同开[meta 对齐])
    askgate_enabled: bool = True        # 门1 分叉实在性(P1-8):候选假设已有主导(分差≥θ)
                                        # 就不烦用户,决策单改写查工具+审计。默认开——只拦
                                        # "声明了 adjudicationTarget 且映射到假设"的问询,
                                        # 旧录制件(无该字段)恒放行 → 零扰动
    ask_ambiguity_threshold: int = 2    # θ_ambig:候选假设分差 ≥ θ 即"已有主导"拦问。
                                        # 加性记分小整数域用绝对差(与 dominant 同构更严);
                                        # 临时默认 2,定标照 θ=0.87 先例留 P3(应拦/应放两簇)
    ask_final_enabled: bool = False     # 停前问人兜底(P3,2026-10-10):死轮兜底停触发且
                                        # 存在未裁决合格分叉(≥2 有成员假设)且 ask 预算
                                        # 有剩 → 停前发起一次确定性裁决问询(问人版死轮
                                        # 兜底,同构:prompt 第一道不灵,代码第二道;
                                        # S2 三次 live 0 问人的流量钥匙;默认关=旧件零影响)


@dataclass
class DomainConfig:
    """域配置(data/config/*.json);字段全部是"内容"而非机制。"""

    config_dir: Path
    credibility: dict = field(default_factory=dict)      # 来源可信度规则表
    lineage: dict = field(default_factory=dict)          # source_lineage 域名->上游根
    relation_vocab: list[str] = field(default_factory=list)
    hypothesis_seeds: list[dict] = field(default_factory=list)  # red-flag 种子词表
    platform_rules: dict = field(default_factory=dict)   # 平台与工具路由规则表
    watchlist: list[dict] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def authoritative_roots(self) -> set[str]:
        return set(self.credibility.get("authoritative_roots", []))

    @property
    def ss_exception_blocklist(self) -> list[str]:
        """单源例外谓词负清单(2026-10-16 发现四):含这些比较/依赖性谓词的
        claim,单一权威根不足以裁定(证据形态超出单份文件所能承载),不适用
        权威登记源单源例外,须走独立根>=2 慢路。空/缺键=现行为(零迁移)。"""
        return list(self.credibility.get("single_source_exception_blocklist", []))

    def credibility_of(self, root: str) -> str:
        tiers = self.credibility.get("tiers", {})
        for tier, roots in tiers.items():
            if root in roots:
                return tier
        return self.credibility.get("default_tier", "低")

    def upstream_root(self, domain: str) -> str:
        """谱系根:逐级上溯(防 A->B->gsxt 链条);自映射/未知域名 -> 自身。"""
        seen: set[str] = set()
        cur = domain
        while cur in self.lineage and cur not in seen:
            seen.add(cur)
            cur = self.lineage[cur]
        return cur


def load_domain_config(config_dir: str | Path) -> DomainConfig:
    base = Path(config_dir)

    def _read(name: str, default):
        p = base / name
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8-sig"))

    return DomainConfig(
        config_dir=base,
        credibility=_read("credibility_rules.json", {}),
        lineage=_read("source_lineage.json", {}),
        relation_vocab=_read("relation_vocab.json", []),
        hypothesis_seeds=_read("hypothesis_seeds.json", []),
        platform_rules=_read("platform_rules.json", {}),
        watchlist=_read("watchlist.json", []),
        aliases=_read("aliases.json", {}),
    )
