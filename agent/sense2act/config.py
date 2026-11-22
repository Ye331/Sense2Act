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
    predicate_form_enabled: bool = False  # 谓词形态标注(手术包件 4,2026-10-22):
                                          # S1b 出生时标 predicate_form,判决单源例外
                                          # 加法保守合并 blocklist∨LLM(只能加拦)。
                                          # 默认 off=纯 blocklist 现行为(E2 三判据
                                          # 已过闸,重录波以全开录制后转正)
    predicate_check_enabled: bool = False  # 谓词充分性核对(2026-10-25):判决梯
                                           # 权威放行路径前核对证据是否载明 claim
                                           # 谓词,不载明→维持待证实(定标探针
                                           # 七轮定标+用户拍板;默认 off=不装配=
                                           # 零调用零痕迹,照 red_team 先例)
    corpus_bound_code_enabled: bool = False  # corpus_bound 代码判定化(手术包件 6,
                                             # 2026-10-22):claim×信封语料规范化子串
                                             # 匹配,代码覆写模型标注(保守:对不上落
                                             # world 慢路);默认 off=模型标注现行为
    t1_independence_enabled: bool = False    # T1 根独立性查检(手术包件 5,
                                             # 2026-10-22):支持根内容级近重复折并
                                             # (判决侧与 ESS 侧同折,口径一致);
                                             # 默认 off=数根现行为
    red_team_enabled: bool = False      # 反驳模式(方案三,2026-10-23):判决后
                                        # 对抗复核(选靶=已证实∧world断言∧单根;
                                        # 反证走既有 S6 复验;空手不加分只留痕)。
                                        # 默认 off=行为零变化(行为参数:多采集+
                                        # 多 LLM+可能触发复验;重录波触发频率
                                        # 读数后定转正,照三开关生命周期)
    red_team_budget: int = 2            # 每 claim 定向查询上限(预算分账)
    flip_guard_enabled: bool = False    # D4 维持偏置闸(2026-11-08):复验场景
                                        # 降级需硬反证/冷却窗外拦+升回滞后+抖动
                                        # 报警(规则表 data/config/flip_rules.json
                                        # 版本化改表即生效;维持偏置=宁漏翻不误翻)。
                                        # 默认 off=闸全关=全链零行为变化(旧录制件
                                        # 回放零扰动;on 入 META_KEYS 配对,照
                                        # --predicate-check 先例:录制 on 回放须同开)
    kb_enabled: bool = False            # S3 第四通道(K2,2026-10-27):KB 检索
                                        # (蓝图 §四;--kb 默认 off=不装配=全链
                                        # 零字节差异;on 时 lineage 继承+版本配对;
                                        # 泄漏墙红线:bench 主读数永远 KB-off)
    kb_path: str = ""                   # K5 呈现消费(渲染批次 2026-10-27):
                                        # --kb on 时=CLI --kb-path;资质核对门控
                                        # 链见 s7_report._kb_rows(有规则卡∧有
                                        # 自我卡否则零痕迹)
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
    ess_enabled: bool = True            # ESS 证据强度分旁车(阶段一,2026-10-20;方案 §五):
                                        # finalize 末尾纯计算写 runs/<event>/ess.json +
                                        # decision_log 概要 1 条(kind='其他');零行为参数
                                        # (不进判决/不进 prompt/不入 META_KEYS)——
                                        # off=只不落旁车,调查行为分毫不差
    assets_enabled: bool = False        # 报告附件(A 线,2026-10-23):S7 前对
                                        # official_website 证据下载产品图入 assets 列,
                                        # 仅报告渲染消费,判决链(S4-S6)不读;默认关=
                                        # 离线/回放零扰动,live+crawler local 显式开启
    snapshots_enabled: bool = False     # 招标公告截图(B 线,2026-10-23):对已证实
                                        # 且挂判决的 ccgp/天津政采关键证据整页截图入
                                        # 附件;browser_auto 隔离通道(决策 #14),默认
                                        # dry-run,真拍受硬限协议强制,额度与 capture 共用


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

    @property
    def pc_trigger_patterns(self) -> list[str]:
        """充分性核对触发正清单(2026-10-25,定标探针拍板):病根族词样——
        清单外 claim 不进核对闸(元数据承载型:发布日期/平台/缺省核查,
        内容核对范畴上治不了,零错杀暴露);宁窄勿宽,漏触发=该条不治
        (可观测)。空/缺键=闸零触发(等价 off)。"""
        return list(self.credibility.get("predicate_check_trigger_patterns",
                                         []))

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
