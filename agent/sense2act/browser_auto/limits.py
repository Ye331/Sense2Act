"""浏览器捕获硬限协议(沿 F 盘 browser_auto 纪律,决策 #14)。

单次≤5 条 / 同词每日≤2 次 / 每日总额 / 最小间隔 / 遇阻当日自锁。
状态落 JSON 文件,当日自锁次日自动解除。纯代码、可离线测试;
违规在 check 阶段拒绝,不靠执行器自觉。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class CaptureBlocked(RuntimeError):
    """硬限拒绝(含自锁)。原因在 message 里。"""


@dataclass
class CaptureLimits:
    per_run: int = 5              # 单次捕获条数上限
    per_word_daily: int = 2       # 同一查询词每日次数
    daily_max: int = 20           # 每日总次数
    min_interval_s: int = 60      # 两次捕获最小间隔
    lockout_on_block: bool = True  # 任何阻塞性事件 -> 当日自锁


@dataclass
class CaptureState:
    day: str = ""
    daily_count: int = 0
    words: dict[str, int] = field(default_factory=dict)
    last_ts: str = ""
    locked: bool = False


class CaptureGate:
    """硬限闸门:check(word) 通过后才允许发起捕获;record(word) 记账。"""

    def __init__(self, state_path: str | Path,
                 limits: CaptureLimits | None = None,
                 clock=None):
        self._path = Path(state_path)
        self.limits = limits or CaptureLimits()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = self._load()

    # ------------------------------------------------------------------

    def check(self, word: str) -> None:
        self._rollover()
        s = self.state
        if s.locked:
            raise CaptureBlocked("当日已自锁(此前遇阻),次日自动解除")
        if s.daily_count >= self.limits.daily_max:
            raise CaptureBlocked(f"每日总额已用尽({s.daily_count}/{self.limits.daily_max})")
        if s.words.get(word, 0) >= self.limits.per_word_daily:
            raise CaptureBlocked(
                f"查询词『{word}』今日已达上限({self.limits.per_word_daily} 次)")
        if s.last_ts:
            last = datetime.fromisoformat(s.last_ts)
            elapsed = (self._clock() - last).total_seconds()
            if elapsed < self.limits.min_interval_s:
                raise CaptureBlocked(
                    f"距上次捕获仅 {int(elapsed)} 秒(<{self.limits.min_interval_s}s)")

    def record(self, word: str, *, blocked: bool = False) -> None:
        self._rollover()
        s = self.state
        s.daily_count += 1
        s.words[word] = s.words.get(word, 0) + 1
        s.last_ts = self._clock().isoformat()
        if blocked and self.limits.lockout_on_block:
            s.locked = True       # 即停当日自锁
        self._save()

    def unlock_next_day(self) -> None:
        """次日解锁(测试用;生产由 _rollover 自动处理)。"""
        self.state.locked = False
        self._save()

    # ------------------------------------------------------------------

    def _rollover(self) -> None:
        today = self._clock().strftime("%Y-%m-%d")
        if self.state.day != today:
            self.state = CaptureState(day=today)

    def _load(self) -> CaptureState:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8-sig"))
                return CaptureState(
                    day=data.get("day", ""), daily_count=data.get("daily_count", 0),
                    words=data.get("words", {}), last_ts=data.get("last_ts", ""),
                    locked=data.get("locked", False))
            except (json.JSONDecodeError, TypeError):
                pass               # 半损坏容忍:当作全新状态
        return CaptureState(day=self._clock().strftime("%Y-%m-%d"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({
                "day": self.state.day, "daily_count": self.state.daily_count,
                "words": self.state.words, "last_ts": self.state.last_ts,
                "locked": self.state.locked,
            }, ensure_ascii=False, indent=1),
            encoding="utf-8")
