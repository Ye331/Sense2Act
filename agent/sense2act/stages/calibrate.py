"""改造④(规划校准率,HESP 导出,2026-10-06):验收标准预注册回填。

S2 决策单的缺口链每环带验收标准(="什么算查到了"的预注册预测),但采集回来后
无机制对照——预测-观察闭环缺最后一环(HESP:每预测先于观察留档,审计重放查
顺序;decision_log 已有顺序,只缺预测件与回填)。本环:采集入账后对主目标缺口链
首环调 LLM 封闭三选一判型(达标/部分相关/不相关,须引结果原句),落 decision_log。

分权与纪律:
- **先判后用**——判定供 S2 下轮规划参考,不改判证据本身(判型是意见,证据的
  终态仍由 S6 代码判决);
- 判型失败不阻断主流程(辅助环),如实标"未判";
- 开关 config.calibration_enabled 默认关:判型是新 stage(calibrate),旧录制件
  零影响;开启录制后回放须同开(run_meta 配对契约自动对齐)。
新指标:**规划校准率** = 验收标准被判"达标"的比例(evaluate 侧从台账取数,
过程指标,作解释变量不作准绳——沿启发②纪律)。
"""
from __future__ import annotations

import json

from ..llm import generate_json

CALIBRATE_STAGE = "calibrate"

VALID_VERDICTS = ("达标", "部分相关", "不相关")


class Calibrator:
    """采集结果的验收标准判型器(模型判型/代码落档,先判后用)。"""

    def __init__(self, llm, ledger, system_prompt: str):
        self._llm = llm
        self._ledger = ledger
        self._prompt = system_prompt

    def assess(self, eid: str, round_no: int, sheet, obs) -> None:
        """对主目标缺口链首环判型并落档;任何失败降级为"未判",不抛出。"""
        step = sheet.gap_chain[0]
        items = [(i.title or "")[:60] for i in (getattr(obs, "items", None) or [])][:5]
        receipts = [(r.platform, r.status)
                    for r in (getattr(obs, "source_reports", None) or [])]
        user = (f"查证缺口: {step.gap}\n"
                f"验收标准: {step.acceptance}\n"
                f"采集结果: items={json.dumps(items, ensure_ascii=False)}; "
                f"回执={json.dumps(receipts, ensure_ascii=False)}")
        verdict, quote = "未判", ""
        try:
            out = generate_json(self._llm, self._prompt, user,
                                stage=CALIBRATE_STAGE)
            v = out.get("verdict")
            if v in VALID_VERDICTS:
                verdict, quote = v, str(out.get("quote", ""))[:200]
        except Exception:                     # 判型失败不阻断主流程(辅助环)
            pass
        self._ledger.log(eid, round_no, "其他", {"calibration": {
            "claim_id": f"{eid}:{sheet.main_target}",
            "gap": step.gap,
            "acceptance": step.acceptance,
            "verdict": verdict,
            "quote": quote,
        }})
