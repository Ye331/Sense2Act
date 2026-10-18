"""统一提问机制(三触发点同一实现:选择题+带上下文+写日志)。

触发点: ①入口(默认退役,极严条件才触发) ②循环内 S2"问人" ③S7 打回。
三个实现共用 AskChannel 协议(cli 按 --ask-mode 选择注入):
  HeadlessClarifier   自动作答(首选项),离线/回放基线;
  InteractiveClarifier 终端人答(档A,P1-8);mixed=非 TTY 自动回退 headless;
  ReplayClarifier     回放录制人答(按序弹出,错位硬失败——配对纪律延伸到人答)。
"""
from __future__ import annotations

import sys
from typing import Protocol

from ..models.decision import QuestionPackage


class AskChannel(Protocol):
    def ask(self, event_id: str, round_no: int, package: QuestionPackage,
            context: dict | None = None) -> str: ...


class HeadlessClarifier:
    """自动取第一选项(确定性),把问题与作答全部落 decision_log。"""

    def __init__(self, ledger, default_choice: int = 0):
        self._ledger = ledger
        self._default = default_choice
        self.asked: list[str] = []

    def ask(self, event_id: str, round_no: int, package: QuestionPackage,
            context: dict | None = None) -> str:
        answer = package.options[self._default % len(package.options)]
        self._ledger.log(event_id, round_no, "澄清", {
            "question": package.question,
            "options": package.options,
            "evidence_summary": package.evidence_summary,
            "answer": answer,
            "mode": "headless_auto",
            "context": context or {},
        })
        self.asked.append(package.question)
        return answer


class InteractiveClarifier:
    """交互终端(档A):打印问题+选项+证据摘要,input 收编号;落账 mode 记实态。

    --ask-mode 三档:interactive=必交互;mixed=流均为 TTY 才交互,否则自动回退
    headless(落账 mode=mixed_fallback,诚实记录"这次其实没问到人")。
    输入以数字编号为主(规避 Windows GBK 控制台中文回显问题),原文精确匹配其次;
    3 轮无效或 EOF → 确定性回退首选项(不卡死,回退如实落账)。
    self.answers 供 cli 写 meta 旁车 ask_answers(录制/回放配对纪律延伸到人答)。
    """

    def __init__(self, ledger, mode: str = "mixed",
                 in_stream=None, out_stream=None):
        self._ledger = ledger
        self._mode = mode
        self._in = in_stream if in_stream is not None else sys.stdin
        self._out = out_stream if out_stream is not None else sys.stdout
        self.answers: list[dict] = []
        self.asked: list[str] = []

    def ask(self, event_id: str, round_no: int, package: QuestionPackage,
            context: dict | None = None) -> str:
        mode = self._mode
        if mode == "mixed":
            tty = all(hasattr(s, "isatty") and s.isatty()
                      for s in (self._in, self._out))
            mode = "interactive" if tty else "mixed_fallback"
        if mode == "interactive":
            answer, mode = self._prompt(package)
        else:                                    # headless / mixed_fallback
            answer = package.options[0]
        self._ledger.log(event_id, round_no, "澄清", {
            "question": package.question,
            "options": package.options,
            "evidence_summary": package.evidence_summary,
            "answer": answer,
            "mode": mode,
            "context": context or {},
        })
        self.asked.append(package.question)
        self.answers.append({"round": round_no,
                             "question": package.question, "answer": answer})
        return answer

    def _prompt(self, package: QuestionPackage) -> tuple[str, str]:
        print("", file=self._out)
        print(f"[澄清] {package.question}", file=self._out)
        print(f"(证据摘要) {package.evidence_summary}", file=self._out)
        for i, opt in enumerate(package.options, 1):
            print(f"  {i}. {opt}", file=self._out)
        for _ in range(3):                       # 3 轮无效→确定性回退,不卡死
            print(f"请输入选项编号(1-{len(package.options)}): ",
                  file=self._out, end="", flush=True)
            try:
                raw = self._in.readline()
            except EOFError:
                break
            if not raw:                          # EOF(流关闭)
                break
            raw = raw.strip()
            if raw.isdigit() and 1 <= int(raw) <= len(package.options):
                return package.options[int(raw) - 1], "interactive"
            if raw in package.options:           # 原文精确匹配其次
                return raw, "interactive"
            print(f"无效输入 {raw!r},请输入 1-{len(package.options)} 的编号",
                  file=self._out)
        print("(未收到有效输入,回退首选项)", file=self._out)
        return package.options[0], "interactive_eof_fallback"


class ReplayClarifier:
    """回放录制人答:按序弹出;问题不符/耗尽 → RuntimeError(cli 捕获 rc=2)。

    与 ReplayLLM 错位语义同构——meta 与流程不一致是配对失败,不允许带病回放。
    """

    def __init__(self, ledger, answers: list[dict]):
        self._ledger = ledger
        self._answers = list(answers)
        self._pos = 0
        self.asked: list[str] = []

    def ask(self, event_id: str, round_no: int, package: QuestionPackage,
            context: dict | None = None) -> str:
        if self._pos >= len(self._answers):
            raise RuntimeError(
                f"人答回放耗尽: 第 {round_no} 轮问人无录制答案"
                "(meta.ask_answers 与流程不一致)")
        rec = self._answers[self._pos]
        if rec.get("question") != package.question:
            raise RuntimeError(
                "人答回放错位: "
                f"期望问「{(rec.get('question') or '')[:40]}」, "
                f"实际问「{package.question[:40]}」"
                f"(第 {self._pos + 1} 条)——meta.ask_answers 与流程不一致")
        self._pos += 1
        answer = rec["answer"]
        self._ledger.log(event_id, round_no, "澄清", {
            "question": package.question,
            "options": package.options,
            "evidence_summary": package.evidence_summary,
            "answer": answer,
            "mode": "replay",
            "context": context or {},
        })
        self.asked.append(package.question)
        return answer
