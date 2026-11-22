"""Red-team 反驳模式(方案三,2026-10-23):判决后对抗复核。

三时点对抗的最后一环:S1b 假设立法时(竞争假设+可区分性)-> S2 检索规划时
(D(c) 反事实翻转数)-> S6 判决形成后(本模块:主动找"我可能是错的"的证据)。
对 DeepTRACE 实证的单边性(Deep Research 产品 54.7%~94.8% 单边)的架构层回应。

分权纪律的落点:
- 触发器读**形态**不读分数(2026-10-18 拍板"诊断读形态"):已证实 ∧
  corpus_bound=0(世界断言)∧ 独立支持根=1——判决 payload 留痕即选靶数据源,
  S6 零改动;脆弱面=E1③ 自证漏报 99 条的同族(单根放行族);
- LLM 只出反驳假设(意见),反证是否成立仍走 S6 判决梯(verify 全链复用,
  含矛盾翻转语义通道)——不开平行判决路径;
- **空手不加分**:未找到反证 ≠ 证明结论(absence of evidence);反驳空手只
  留审计痕(decision_log kind='其他',CHECK 纪律复用),分数与判决永不动——
  "找不到反证→提高置信度"被明确否决(用搜索行为造佐证=Goodhart 口子)。

执行闭环:反驳查询构造为决策单(action=查工具,gap=反驳判别),走既有
S3 采集 -> S4 入账 -> S5 提交 -> S6 复验;靶 claim 状态被复验改变即"反证
生效"(翻正/回退/翻转均为合法结局,全有既有审计)。

开关 --red-team 默认 off 入 META_KEYS(行为参数:多采集+多 LLM+可能触发
复验;存量录制件保护+开关生命周期=重录波全开录制后逐个转正)。
RED_TEAM_PROMPT 内聚本模块(非 prompts.py):接线期热路径隔离
(red_team.py 为新增文件,runner 子进程加载不到;接线归第二段)。
"""
from __future__ import annotations

import json
from typing import Protocol

from ..llm import StructuredLLM, call_structured
from ..models.base import ActionType, SchemaModel
from ..models.decision import DecisionSheet, GapStep

RED_TEAM_PROMPT = """你是调查结论的反方检验员(red-team)。给定一条已判"已证实"的
结论与其支持证据概况,任务:**若此结论是错的,最可能的原因是什么?**每个原因配一条
判别性检索词——如果该原因成立,搜这个词应当能找到反证;如果反复搜不到,结论更可信。
要求:原因必须是可被证据检验的具体命题,不做泛泛怀疑;检索词带实体与判别词,
不要复述结论本身;最多 2 条。
输出 JSON: {"hypotheses": [{"reason": "<原因>", "query": "<判别检索词>"}]}。
纪律:你只出假设与检索词(意见);反证是否成立由判决环节按证据裁定,与你无关;
找不反证不等于结论被加强,你也不要暗示这一点。"""


class RebuttalHypothesis(SchemaModel):
    """反驳假设一条:若结论为错的最可能原因 + 判别性检索词。"""

    reason: str
    query: str


class RedTeamPlan(SchemaModel):
    hypotheses: list[RebuttalHypothesis] = []


class Verdicts(Protocol):
    def verify(self, claim_id: str, round_no: int) -> object: ...


def select_targets(ledger, event_id: str) -> list[dict]:
    """选靶(纯读台账):已证实 ∧ corpus_bound=0 ∧ 独立支持根=1。

    数据源=decision_log kind='判决' 的 payload 留痕(每 claim 取最新一条;
    support_roots 已是判决时折并后的根列表)。排序按 claim_id(确定性)。
    """
    latest: dict[str, dict] = {}
    for r in ledger.conn.execute(
            "SELECT payload_json FROM decision_log"
            " WHERE event_id=? AND kind='判决' ORDER BY id", (event_id,)):
        p = json.loads(r["payload_json"])
        if isinstance(p, dict) and "claim_id" in p:
            latest[p["claim_id"]] = p
    out: list[dict] = []
    for claim_id in sorted(latest):
        p = latest[claim_id]
        roots = p.get("support_roots") or []
        if p.get("final") != "已证实" or len(roots) != 1:
            continue
        row = ledger.conn.execute(
            "SELECT draft_id, text, corpus_bound FROM claims WHERE claim_id=?",
            (claim_id,)).fetchone()
        if row is None or row["corpus_bound"]:
            continue   # 自证/语料内断言:单根即证是设计内语义,不在反驳面
        out.append({"claim_id": claim_id, "draft_id": row["draft_id"],
                    "text": row["text"], "support_root": roots[0]})
    return out


class RedTeam:
    """反驳模式编排器(选靶 -> 生成 -> 定向查询 -> 复验 -> 留痕)。"""

    def __init__(self, ledger, llm: StructuredLLM, verdicts: Verdicts,
                 budget_per_claim: int = 2, retry: int = 2):
        self._ledger = ledger
        self._llm = llm
        self._verdicts = verdicts
        self._budget = max(1, int(budget_per_claim))
        self._retry = retry

    # ------------------------------------------------------------------ 生成

    def plan_rebuttals(self, target: dict, round_no: int) -> tuple[list[str], bool]:
        """反驳假设生成(LLM 意见,温度 0 结构化)。

        返回 (判别检索词列表, degraded)。生成失败/超限降级为空列表——
        反驳生成失败不阻塞流水线,但留 degraded 痕(失败不伪装)。
        """
        user = (
            "结论(草案 id " + target["draft_id"] + "):\n"
            + json.dumps({"draft_id": target["draft_id"],
                          "text": target["text"],
                          "support_root": target["support_root"]},
                         ensure_ascii=False)
            + "\n\n为它生成反驳假设与判别检索词(最多 2 条)。")
        try:
            plan = call_structured(
                self._llm, stage="red_team", system=RED_TEAM_PROMPT,
                user=user, model_cls=RedTeamPlan, retry=self._retry)
        except Exception:
            return [], True
        return [h.query for h in plan.hypotheses][:self._budget], False

    # ------------------------------------------------------------------ 闭环

    def run(self, event_id: str, round_no: int, executor, observer, writer5) -> dict:
        """对抗复核一轮(主循环后、finalize 前调用;off 时调用方不进来)。

        返回 summary(呈现层消费):{"attempted", "found", "claims": [...]}。
        反证生效的判定=复验后靶 claim 状态发生变化(翻正/回退/翻转均为
        合法结局,判决与成因归 S6 与既有审计,本模块不解释不加工)。
        """
        targets = select_targets(self._ledger, event_id)
        summary: dict = {"attempted": len(targets), "found": 0, "claims": []}
        for t in targets:
            queries, degraded = self.plan_rebuttals(t, round_no)
            before = self._ledger.claim_row(t["claim_id"])["status"]
            for i, q in enumerate(queries, 1):
                sheet = DecisionSheet(
                    round=round_no, main_target=t["draft_id"],
                    action=ActionType.TOOL,
                    gap_chain=[GapStep(
                        gap=f"反驳假设{i}判别:{q}",
                        acceptance="采到与该反驳假设相关的证据,或空手回执(亦为有效结论)")],
                    search_hints=[q])
                obs, _quality = executor.execute(
                    sheet, round_no, t["text"])
                processed = observer.process(event_id, round_no, obs)
                writer5.commit(event_id, round_no, processed)
            self._verdicts.verify(t["claim_id"], round_no)
            after = self._ledger.claim_row(t["claim_id"])["status"]
            found = after != before
            if found:
                summary["found"] += 1
            # 空手/生效都只留痕(kind='其他' CHECK 纪律,同 ESS 概要先例)
            self._ledger.log(event_id, round_no, "其他", {
                "red_team": {"claim_id": t["claim_id"], "queries": queries,
                             "found": found, "status_before": before,
                             "status_after": after, "degraded": degraded}})
            summary["claims"].append({
                "claim_id": t["claim_id"], "queries": queries,
                "found": found, "degraded": degraded})
        return summary
