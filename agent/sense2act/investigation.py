"""主循环编排:信封 -> S1a -> S1b -> [S2->S3->S4->S5->S6->D]xN -> S7。

轮数语义:一次完整 S2->S3->S4->S5->S6 遍历 = 1 轮;"问人"照常计轮;
D 起草钩子不消耗轮数(成本走 token 账);S4 质量差回 S3 重查一次
(CRAG 回边,不消耗新轮);预算护栏耗尽 -> 优雅停止出部分报告。
"""
from __future__ import annotations

import json
import re

from .config import DomainConfig, InvestigationConfig
from .llm import ModelBudgetExceeded, budget_report
from .models.decision import (
    AdjudicationMapping,
    AdjudicationTarget,
    QuestionPackage,
)
from .models.envelope import InvestigationEnvelope
from .models.observation import EvidenceItemLite, ObservationPackage
from .stages.clarify import AskChannel
from .stages.d_hooks import DraftHooks
from .stages.s1_signal import (SignalAnalyzer, envelope_evidence_seeds,
                               tender_field_claims)
from .stages.s2_planner import Planner, plan_history
from .stages.s3_executor import Executor
from .stages.s4_observer import Observer, quality_signal
from .stages.s5_ledger import LedgerWriter
from .stages.s6_verdict import VerdictEngine
from .stages.s7_report import RecommendProvider, ReportBuilder
from .store.ledger import LedgerRepository


def _to_lite(seed: dict) -> EvidenceItemLite:
    return EvidenceItemLite(
        platform=seed.get("platform", ""), source=seed.get("source", ""),
        url=seed.get("url", ""), title=seed.get("title", ""),
        content=seed.get("content", ""),
        published_at=seed.get("published_at", ""),
        author_type=seed.get("author_type", ""))


def _next_claim_no(claims) -> int:
    """S1b 生成的 C 编号最大值 +1(机械追加商机字段 claim 的续接号)。"""
    nums = [int(m.group(1)) for c in claims
            if (m := re.match(r"C(\d+)$", c.id or ""))]
    return (max(nums) + 1) if nums else 1


_PROJECT_NO_IN_TITLE_RE = re.compile(r"项目编号[:：]?\s*([A-Za-z0-9\-]{6,})")


def _extract_project_no(t) -> str:
    """项目编号:标题括号内"(项目编号SHGP-2026-A536)"正则优先(最可靠);
    project_id 可能是 R-哈希(信号层归一 ID,如 agent_topk),故仅作兜底,且剥
    PRJ- 前缀。提取不到返回空串(不硬造)。"""
    m = _PROJECT_NO_IN_TITLE_RE.search(t.project_name or "")
    if m:
        return m.group(1)
    return (t.project_id or "").replace("PRJ-", "")


class InvestigationPipeline:
    def __init__(
        self,
        ledger: LedgerRepository,
        domain: DomainConfig,
        config: InvestigationConfig,
        analyzer: SignalAnalyzer,
        planner: Planner,
        executor: Executor,
        observer: Observer,
        writer5: LedgerWriter,
        verdicts: VerdictEngine,
        hooks: DraftHooks,
        reports: ReportBuilder,
        clarifier: AskChannel,
        calibrator=None,
        adjudicator=None,
        askgate=None,
    ):
        self._ledger = ledger
        self._domain = domain
        self._config = config
        self._analyzer = analyzer
        self._planner = planner
        self._executor = executor
        self._observer = observer
        self._writer5 = writer5
        self._verdicts = verdicts
        self._hooks = hooks
        self._reports = reports
        self._clarifier = clarifier
        self._calibrator = calibrator   # 改造④:验收标准判型器(可选,默认关)
        self._adjudicator = adjudicator  # P1-8:人答裁决应用器(可选;None=答案不传导=旧行为)
        self._askgate = askgate          # P1-8:门1 分叉实在性(可选;None=不设门=旧行为)

    # ------------------------------------------------------------------

    def run(
        self,
        envelope: InvestigationEnvelope,
        event_id: str,
        refresh_plan: dict[int, list[dict]] | None = None,
    ) -> dict:
        refresh_plan = refresh_plan or {}
        eid, continued = self._ledger.register_event(
            envelope, "", event_id)
        if continued:
            self._ledger.log(eid, 0, "其他", {
                "note": "项目生命周期续查:同 project_id 注回原事件,不重开"})
        label = envelope.signal_label()

        # 定向检索锚点:信封精确定位字段下传采集层(采购人/项目编号/发布日期)。
        # 项目编号可靠来源=标题括号内"(项目编号SHGP-2026-A536)",project_id 在
        # 部分上游(R-哈希,agent_topk)不是编号——故标题正则优先,project_id 兜底。
        search_context: dict = {}
        if envelope.kind == "tender" and envelope.tender:
            t = envelope.tender
            search_context = {
                "buyer": t.purchaser or "",
                "project_no": _extract_project_no(t),
                "published_date": t.published_date or "",
                "source_url": t.source_url or "",
            }

        # 信封侧证据种子(检测-调查置信贯通;纯代码装配)。
        # no_extract 种子(rumor 信封元数据,2026-10-10)不经 S4:内部记录不做
        # 实体抽取(entity_ids 恒空 → 只走 corpus 直挂道,不做实体交集挂账),
        # 其余字段与 S4 产物同构,直接入账。
        seeds = envelope_evidence_seeds(
            envelope, include_metadata=self._config.rumor_meta_seed)
        direct0 = [s for s in seeds if s.get("no_extract")]
        seeds = [s for s in seeds if not s.get("no_extract")]
        processed0 = []
        if seeds:
            obs0 = ObservationPackage(round=0, main_target="-",
                                      action_brief="信封证据种子", items=[
                                          _to_lite(s) for s in seeds])
            processed0 = self._observer.process(eid, 0, obs0)
        if direct0:
            processed0 = processed0 + [{
                "platform": s["platform"], "source": s["source"],
                "url": s.get("url", ""), "title": s.get("title", ""),
                "content": s.get("content", ""),
                "published_at": s.get("published_at", ""),
                "author_type": s.get("author_type", ""),
                "lineage_root": s.get("lineage_root", ""),
                "credibility": s.get("credibility", "中"),
                "sensitive": False, "entity_ids": [], "entities": [],
                "round": 0,
            } for s in direct0]
        if processed0:
            self._writer5.commit(eid, 0, processed0, derive=False)

        # S1a 定性 -> (入口澄清默认直通) -> S1b 分解
        seed_labels = [s.get("label", "") for s in self._domain.hypothesis_seeds]
        qualification = self._analyzer.analyze_light(envelope, seed_labels)
        self._ledger.log(eid, 0, "其他", {
            "s1a": qualification.model_dump(by_alias=True)})
        blueprint = self._analyzer.analyze_deep(
            envelope, qualification, self._domain.relation_vocab)
        for gap in qualification.basis_gaps:  # 依据不足 -> 待查缺口并入蓝图
            if gap and gap not in [g.text for g in blueprint.gaps]:
                from .models.intel import GapDraft
                blueprint.gaps.append(GapDraft(text=gap))
        # 机械追加商机字段 claim(仅 v2 提单:procurement_project_id 带 PRJ- 前缀;
        # 世界断言,等 ccgp 详情正文级证实;id 续接 S1b)。v1 老 fixture(project_id
        # 如 MEDAI-2026-001)走老路径,回归基线零扰动。
        if (envelope.kind == "tender" and envelope.tender
                and (envelope.tender.project_id or "").startswith("PRJ-")):
            extra = tender_field_claims(envelope.tender)
            next_no = _next_claim_no(blueprint.claims)
            for i, c in enumerate(extra):
                c.id = f"C{next_no + i}"
                blueprint.claims.append(c)
        self._ledger.install_blueprint(
            eid, blueprint, 0, aliases=self._domain.aliases)
        # 回溯挂账:信封证据(轮 0)按实体交集挂到新生成的 claim 上
        self._writer5.associate_backlog(eid, 0)
        # 信封自证 claim(corpus_bound)挂账后立即裁定一次(轮 0):
        # 自证例外"语料这么说单根即证"不依赖采集,无需等首轮 touched——
        # tender 无种子形态下无采集,verdict_scope=touched 永不触及它们
        # (2026-10-06 实证:挂账修复后仍 0 判决,因 S6 只在轮 1+ 裁 touched)
        for cid in self._ledger.corpus_bound_claim_ids(eid):
            self._verdicts.verify(cid, 0)

        # 查证主循环
        stop_reason = ""
        ask_count = 0
        round_no = 0
        dry_streak = 0   # 连续零信息增量轮数(S2 停与换纪律的代码兜底)
        try:
            for round_no in range(1, self._config.max_rounds + 1):
                open_rows = self._ledger.open_claims(eid)
                open_ids = [r["draft_id"] for r in open_rows]
                if not open_ids:
                    stop_reason = "全部澄清"
                    break
                sheet = self._planner.plan(
                    round_no,
                    self._ledger.event_summary(
                        eid, include_disc=self._config.disc_enabled),
                    open_ids, plan_history(self._ledger, eid))
                self._ledger.log(
                    eid, round_no, "决策单",
                    sheet.model_dump(by_alias=True), degraded=sheet.degraded)

                if sheet.action.value == "停":
                    stop_reason = sheet.stop_reason or "规划器选择停止"
                    break
                if sheet.action.value == "问人":
                    # 门1 分叉实在性(P1-8,两层防线的第二道):台账已有主导假设
                    # → 拦下问询,决策单改写查工具,**不 continue**——落入当轮既有
                    # S3→S6 流程(拦截审计由门内落 kind='澄清' mode=askgate_blocked)
                    gate = (self._askgate.check(eid, round_no, sheet)
                            if self._askgate is not None else None)
                    if gate is not None and gate.blocked:
                        sheet = gate.sheet
                    else:
                        ask_count += 1   # 门后才计预算:拦截=用户没被打断,不耗上限
                        if ask_count > self._config.ask_max_per_task:
                            stop_reason = "问人上限(打断纪律)"
                            break
                        assert sheet.question_package is not None
                        answer = self._clarifier.ask(
                            eid, round_no, sheet.question_package,
                            context={"trigger": "s2_ask"})
                        # P0 答案→台账传导(2026-10-09 前返回值被丢弃=问完白问):
                        # 裁决落账(纯确定性)+受影响 claim 直接复验——照抄轮 0
                        # corpus_bound 先例(105-112);(b) 新条无证据早退零 LLM,
                        # (c) 目标 claim 按证据复验(已证实被新证据触及=既有矛盾翻转通道)
                        if self._adjudicator is not None:
                            result = self._adjudicator.apply(
                                eid, round_no, sheet.question_package, answer)
                            for cid in result.affected_claim_ids:
                                self._verdicts.verify(cid, round_no)
                        continue  # 问人不豁免轮数

                claim = self._ledger.claim_row(f"{eid}:{sheet.main_target}")
                claim_text = claim["text"] if claim else ""
                obs, quality = self._executor.execute(
                    sheet, round_no, claim_text,
                    unreachable_callback=lambda draft_id: self._ledger.mark_unreachable(
                        f"{eid}:{draft_id}"),
                    search_context=search_context)
                processed = self._observer.process(eid, round_no, obs)
                if quality == "poor" and self._config.craq_retry > 0:
                    fallback = self._domain.platform_rules.get(
                        "fallback_platforms", ["toutiao"])
                    obs2, _ = self._executor.requery(
                        sheet, round_no, claim_text, fallback,
                        search_context=search_context)
                    processed += self._observer.process(eid, round_no, obs2)
                self._writer5.commit(eid, round_no, processed)

                # 改造④(规划校准率):采集入账后对主目标验收标准判型回填
                # (先判后用,不改判证据;开关默认关,判型失败自动降级"未判")
                if self._calibrator is not None:
                    self._calibrator.assess(eid, round_no, sheet, obs)

                # 环⑦ 状态刷新注入(追踪模式/生命周期事件的同步接口)
                refreshed: list[str] = []
                if round_no in refresh_plan:
                    refreshed = self._writer5.commit_refresh(
                        eid, round_no, refresh_plan[round_no])

                # 死轮兜底停(S2 停与换纪律的第二道:模型提停,代码兜底;
                # 数据背书:12 轮实验 85% 轮次零新增证据,模型主动停仅 1/71)
                ev_new, claim_new = self._ledger.round_progress(eid, round_no)
                if ev_new == 0 and claim_new == 0:
                    dry_streak += 1
                else:
                    dry_streak = 0
                if (self._config.dry_stop_rounds
                        and dry_streak >= self._config.dry_stop_rounds):
                    stop_reason = (f"证据通道已穷尽(代码兜底:连续 {dry_streak} 轮"
                                   "零新增证据/新 claim)")
                    # 停前问人兜底(P3,--ask-final 默认关):证据通道穷尽且存在
                    # 未裁决分叉时,代码兜底发起一次裁决问询——问人版死轮兜底,
                    # 同构于"prompt 第一道不灵,代码第二道"(S2 三次 live 0 问人)
                    if self._config.ask_final_enabled and self._final_ask(
                            eid, round_no, ask_count):
                        stop_reason += ";停前已发起裁决问询(ask-final)"
                    break

                # S6 裁定(verdict_scope: touched=本轮涉证据+被刷新 / all=全量)
                if self._config.verdict_scope == "all":
                    scope = [r["claim_id"] for r in self._ledger.all_claims(eid)]
                else:
                    scope = sorted(set(
                        self._ledger.touched_claim_ids(eid, round_no)
                        + [f"{eid}:{d}" for d in refreshed]))
                for claim_id in scope:
                    self._verdicts.verify(claim_id, round_no)

                if self._config.interleave:
                    self._hooks.run_round_end(eid, round_no)
        except ModelBudgetExceeded as exc:
            stop_reason = f"预算护栏: {exc}"

        if not stop_reason:
            stop_reason = "轮数上限(硬停兜底;未决 claim 已进 Open Questions)"

        costs = {"llm_calls": budget_report(),
                 "collect_calls": self._executor.collect_calls}
        summary = self._reports.finalize(
            eid, max(round_no, 1), stop_reason, costs=costs,
            envelope_label=label,
            signal_type=qualification.signal_type.value,
            envelope=envelope)
        self._ledger.close_event(eid)
        summary["continued_event"] = continued
        return summary

    # ------------------------------------------------------------------

    def _final_ask(self, eid: str, round_no: int, ask_count: int) -> bool:
        """停前问人兜底(P3,--ask-final):构造确定性裁决问单问一次人。

        合格分叉 = ≥2 个"有成员(emerged)且未被裁决过"的假设,且 ask 预算有剩。
        问单不经 AskGate(代码自发起,非 S2 决策;分叉按构造未裁决)。
        问单确定性:候选按 registered_round 序(standings 既有排序),选项=
        假设标签。答案传导与 S2 问人分支同款:apply + affected 直接 verify。
        返回是否真的问了(不合格分叉/预算耗尽=不问,停因不加后缀)。
        """
        if ask_count >= self._config.ask_max_per_task:
            return False
        asked_before = {r["target_id"] for r in self._ledger.conn.execute(
            "SELECT DISTINCT target_id FROM adjudications"
            " WHERE event_id=? AND target_kind='hypothesis'", (eid,))}
        candidates = [s for s in self._ledger.hypothesis_standings(eid)
                      if s["emerged"] and s["hypothesis_id"] not in asked_before]
        if len(candidates) < 2:
            return False
        package = QuestionPackage(
            question="多假设竞争且证据通道已穷尽,请委托方裁决调查口径倾向:",
            options=[s["label"] for s in candidates],
            evidence_summary=";".join(
                f"{s['label']}(记分 {s['score']},待证实 {s['pending']})"
                for s in candidates),
            adjudication_target=AdjudicationTarget(kind="hypothesis", map=[
                AdjudicationMapping(option=s["label"],
                                    target_id=s["hypothesis_id"].rsplit(":", 1)[-1])
                for s in candidates]))
        answer = self._clarifier.ask(eid, round_no, package,
                                     context={"trigger": "final_ask"})
        if self._adjudicator is not None:
            result = self._adjudicator.apply(eid, round_no, package, answer)
            for cid in result.affected_claim_ids:
                self._verdicts.verify(cid, round_no)
        return True
