"""S2 查证规划(Thought):每轮一次结构化调用,决策单只放判断不放参数。

流水线: 代码拼输入(台账摘要,不喂全量) -> rubric 推理(轨迹落 thought 字段)
      -> 代码硬校验(主目标防幻觉/缺口链必带验收/问人必带选项)
      -> 失败重试<=2 -> planner_degraded 保守单(代码生成,循环不卡死)。
"""
from __future__ import annotations

import json
from typing import Protocol

from ..llm import StructuredLLM, call_structured
from ..models.decision import DecisionSheet, conservative_sheet, validate_decision_sheet
from ..prompts import S2_PROMPT


class Planner(Protocol):
    def plan(self, round_no: int, summary: dict, open_ids: list[str],
             history: list[dict]) -> DecisionSheet: ...


class LLMPlanner:
    def __init__(self, llm: StructuredLLM, retry: int = 2):
        self._llm = llm
        self._retry = retry

    def plan(self, round_no: int, summary: dict, open_ids: list[str],
             history: list[dict]) -> DecisionSheet:
        user = (
            f"当前轮次: 第 {round_no} 轮\n\n台账摘要(claim 三态/假设记分/关系/调查圈):\n"
            + json.dumps(summary, ensure_ascii=False, indent=1)
            + "\n\n未决 claim 的草案 id(主目标只能从中选):\n"
            + json.dumps(open_ids, ensure_ascii=False)
            + "\n\n历史决策单摘要(避免重复查证):\n"
            + json.dumps(history[-3:], ensure_ascii=False)
        )
        try:
            sheet = call_structured(
                self._llm, stage="s2", system=S2_PROMPT, user=user,
                model_cls=DecisionSheet, retry=self._retry)
            sheet.round = round_no
            validate_decision_sheet(sheet, open_ids)
            return sheet
        except Exception as exc:
            # planner_degraded:保守决策单(查最高优先级未决 claim),标记降级;
            # 失败原因不进决策单,由 pipeline 落 decision_log(决策单只放判断)。
            fallback_top = open_ids[0] if open_ids else ""
            sheet = conservative_sheet(round_no, fallback_top, summary.get(
                "claims_by_status", {}).get("待证实", [{}])[0].get("text", ""))
            sheet.stop_reason = ""  # 降级单不选停,保守继续
            sheet.degraded = True
            return sheet


def plan_history(ledger, event_id: str) -> list[dict]:
    """历史决策单摘要(决策日志 -> 下轮 S2 的输入;轨迹日志回喂)。"""
    rows = ledger.decision_log_rows(event_id)
    out = []
    for r in rows:
        if r["kind"] == "决策单":
            payload = json.loads(r["payload_json"])
            out.append({
                "round": r["round"],
                "main_target": payload.get("mainTarget"),
                "action": payload.get("action"),
                "degraded": bool(r["degraded"]),
            })
    return out
