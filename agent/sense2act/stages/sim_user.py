"""模拟用户器(档C 澄清实验台,P1-8):按人格回答问题包。

人格族映射 = AskToAct(EMNLP 2025)的模拟评测用户【文献依据】;
机械人格零 LLM 保离线确定性(受控合成路线;仅合作型可选 LLM 判断——
离线无 llm 时退化首选项,与 headless 等价)。

只实现 AskChannel 协议:人格只影响"答案本身",裁决器/门/落账零改动——
敷衍/拒答/答非所问/沉默的答案不在映射内 → 既有 adjudication_blocked 路径
(答案不静默消失,F2 故障注入的回归对象)。
cli 注入:--ask-mode simulated --sim-persona <名>(--sim-seed 定版随机种子)。
"""
from __future__ import annotations

import random

from pydantic import Field

from ..llm import StructuredLLM, call_structured
from ..models.base import SchemaModel
from ..models.decision import QuestionPackage
from ..prompts import SIM_USER_PROMPT


class SimChoice(SchemaModel):
    """合作型人格的 LLM 作答:选项编号(1 起)+一句话理由。"""

    choice: int = Field(ge=1)
    note: str = ""


PERSONAS = ("cooperative", "careless", "irrelevant", "refuse", "regret", "silence")

_IRRELEVANT = "我没听懂这个问题,你先按你的判断查,之后给我看结果。"
_REFUSE = "不知道,这个我无法判断。"


class SimulatedClarifier:
    """按人格回答问题包;payload mode=simulated_<人格>(落账诚实记录是模拟)。"""

    def __init__(self, ledger, persona: str = "cooperative",
                 llm: StructuredLLM | None = None, seed: int = 20261009):
        if persona not in PERSONAS:
            raise ValueError(
                f"未知人格 {persona!r}(可用: {','.join(PERSONAS)})")
        self._ledger = ledger
        self._persona = persona
        self._llm = llm
        self._rng = random.Random(seed)          # 定版种子:敷衍型可复现
        self._asked_questions: list[str] = []    # 反悔型依据:同题二问换答案
        self.answers: list[dict] = []            # 供 cli 写 meta.ask_answers
        self.asked: list[str] = []

    def ask(self, event_id: str, round_no: int, package: QuestionPackage,
            context: dict | None = None) -> str:
        answer = self._answer(package)
        self._ledger.log(event_id, round_no, "澄清", {
            "question": package.question,
            "options": package.options,
            "evidence_summary": package.evidence_summary,
            "answer": answer,
            "mode": f"simulated_{self._persona}",
            "context": context or {},
        })
        self.asked.append(package.question)
        self._asked_questions.append(package.question)
        self.answers.append({"round": round_no,
                             "question": package.question, "answer": answer})
        return answer

    # ------------------------------------------------------------------

    def _answer(self, package: QuestionPackage) -> str:
        persona = self._persona
        if persona == "cooperative":
            return self._cooperative(package)
        if persona == "careless":
            return self._rng.choice(package.options)      # 乱选(可复现)
        if persona == "irrelevant":
            return _IRRELEVANT                            # 不在选项内→blocked
        if persona == "refuse":
            return _REFUSE                                # 不在选项内→blocked
        if persona == "regret":
            if package.question in self._asked_questions:
                return package.options[-1]                # 反悔:换答案(latest-wins)
            return package.options[0]
        return ""                                         # silence 超时

    def _cooperative(self, package: QuestionPackage) -> str:
        if self._llm is None:                             # 离线退化=首选项(确定性)
            return package.options[0]
        user = (
            "问题:" + package.question + "\n\n证据摘要:"
            + package.evidence_summary + "\n\n选项:\n"
            + "\n".join(f"{i}. {opt}" for i, opt in enumerate(package.options, 1))
        )
        choice = call_structured(
            self._llm, stage="sim_user", system=SIM_USER_PROMPT, user=user,
            model_cls=SimChoice, retry=2)
        if 1 <= choice.choice <= len(package.options):
            return package.options[choice.choice - 1]
        return package.options[0]                         # 越界编号:保守回退
