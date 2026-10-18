"""LLM 封装:协议 + 回放/真实/录制三实现 + 预算护栏。

分权纪律的物理基础:所有 LLM 槽位只依赖 StructuredLLM 协议,
回放(ReplayLLM)与真实(DeepSeekLLM)走同一套 pydantic 校验
——换模型 = 换注入,协议层零改动(回放-真实同构,L2)。
温度 0 为回放可复现的前提(DeepSeekLLM 硬编码)。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol

_budget_used: ContextVar[int] = ContextVar("sense2act_budget_used", default=0)
_budget_limit: ContextVar[int] = ContextVar("sense2act_budget_limit", default=-1)


class ModelBudgetExceeded(RuntimeError):
    """预算护栏(L6):耗尽后优雅停止,按现有台账出部分报告,绝不烧穿。"""


@contextmanager
def model_call_budget(limit: int):
    token = _budget_limit.set(limit)
    used = _budget_used.set(0)
    try:
        yield
    finally:
        _budget_limit.reset(token)
        _budget_used.reset(used)


class StructuredLLM(Protocol):
    model_name: str

    def generate_json(self, system_prompt: str, user_prompt: str, *,
                      stage: str = "") -> dict: ...


def budget_report() -> int:
    return _budget_used.get()


def generate_json(llm: StructuredLLM, system: str, user: str, *, stage: str = "") -> dict:
    limit = _budget_limit.get()
    if limit >= 0 and _budget_used.get() >= limit:
        raise ModelBudgetExceeded(f"模型调用预算耗尽(上限 {limit})")
    _budget_used.set(_budget_used.get() + 1)
    return llm.generate_json(system, user, stage=stage)


def parse_json_object(text: str) -> dict:
    """剥代码围栏 -> json.loads -> 失败截取首尾大括号再试 -> 必须 dict。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型输出不是合法 JSON 对象")
        obj = json.loads(cleaned[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("模型输出必须是 JSON 对象")
    return obj


def validation_feedback(exc: Exception) -> str:
    """ValidationError -> 紧凑 JSON 反馈(不回传正文);其他异常 -> str。"""
    try:
        from pydantic import ValidationError
        if isinstance(exc, ValidationError):
            return json.dumps(exc.errors(include_url=False, include_context=False,
                                         include_input=False), ensure_ascii=False)
    except Exception:
        pass
    return str(exc)[:500]


def schema_snippet(model_cls) -> str:
    """把 pydantic 模型的 JSON Schema 嵌进 user prompt(signalx 惯例)。"""
    return json.dumps(model_cls.model_json_schema(by_alias=True), ensure_ascii=False)


def call_structured(llm, *, stage: str, system: str, user: str, model_cls, retry: int = 2):
    """统一模板:Schema 嵌 prompt -> generate_json -> pydantic 校验 -> 失败带反馈重试。

    重试耗尽抛最后一次异常,由调用方走降级路径(planner_degraded / 维持原态等),
    循环不卡死。ModelBudgetExceeded 不重试直接上抛(预算护栏优先)。
    """
    feedback = ""
    last: Exception | None = None
    for _ in range(retry + 1):
        prompt = user + "\n\n输出 JSON Schema:\n" + schema_snippet(model_cls)
        if feedback:
            prompt += "\n\n上一次输出校验失败,请修正(只改错误处,保持其余字段):\n" + feedback
        try:
            return model_cls.model_validate(
                generate_json(llm, system, prompt, stage=stage))
        except ModelBudgetExceeded:
            raise
        except Exception as exc:  # ValidationError / ValueError / API 错误统一反馈重试
            last = exc
            feedback = validation_feedback(exc)
    assert last is not None
    raise last


class ReplayLLM:
    """离线回放:按 stage 顺序消费固定响应;耗尽/错位即 RuntimeError(fail-fast)。"""

    model_name = "replay"

    def __init__(self, entries: list[dict]):
        self._entries = list(entries)
        self._pos = 0

    def generate_json(self, system_prompt: str, user_prompt: str, *,
                      stage: str = "") -> dict:
        if self._pos >= len(self._entries):
            raise RuntimeError(f"回放响应耗尽(stage={stage})")
        entry = self._entries[self._pos]
        if entry.get("stage") != stage:
            raise RuntimeError(
                f"回放错位: 期望 stage={stage}, 下一条是 {entry.get('stage')}"
                f"(第 {self._pos + 1} 条)——fixture 与流程不一致")
        self._pos += 1
        resp = entry.get("response")
        if isinstance(resp, str):
            return parse_json_object(resp)
        return dict(resp)

    @property
    def remaining(self) -> int:
        return len(self._entries) - self._pos


class RecordingLLM:
    """真跑时顺手把 (stage, response) 录成 responses.json,跑完即得回归 fixture。"""

    def __init__(self, inner: StructuredLLM):
        self._inner = inner
        self.model_name = getattr(inner, "model_name", "recorded")
        self.records: list[dict] = []

    def generate_json(self, system_prompt: str, user_prompt: str, *,
                      stage: str = "") -> dict:
        obj = self._inner.generate_json(system_prompt, user_prompt, stage=stage)
        self.records.append({"stage": stage, "response": obj})
        return obj

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.records, ensure_ascii=False, indent=2),
            encoding="utf-8")


def _load_env_file(path: Path) -> dict[str, str]:
    """轻量 .env 解析(UTF-8-SIG 容 BOM,不污染进程环境,不插值)。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


class DeepSeekLLM:
    """真实模型(OpenAI 兼容 SDK);--live 专用,离线不触发。

    温度 0 硬编码(回放可复现承诺);finish_reason=length 抛可诊断错误。
    依赖注入顺序: 构造参数 > 环境变量 > 项目根 .env。
    """

    model_name = "deepseek-chat"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        env_path: str | Path | None = None,
    ):
        root = Path(env_path) if env_path else Path.cwd() / ".env"
        env = _load_env_file(root)
        self.api_key = api_key or env.get("DEEPSEEK_API_KEY") or ""
        self.base_url = base_url or env.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        self.model_name = model or env.get("DEEPSEEK_MODEL") or "deepseek-chat"
        if not self.api_key:
            raise ValueError(
                "缺少 DEEPSEEK_API_KEY(构造参数 / 环境变量 / .env 三处均未找到;"
                "离线回放模式不需要该配置)")
        from openai import OpenAI  # 延迟导入:离线环境无需安装即被触发
        self._client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, max_retries=2)

    def generate_json(self, system_prompt: str, user_prompt: str, *,
                      stage: str = "") -> dict:
        resp = self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        # 供调用方记账(后端调查预算按真实 token 累计)
        self.last_total_tokens = getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise ValueError(f"[{stage}] 模型输出被截断(max_tokens=8192),请精简上下文")
        return parse_json_object(choice.message.content or "")
