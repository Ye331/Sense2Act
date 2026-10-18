"""浏览器自动化隔离通道(决策 #14 红线例外的工程化落点)。

纪律:
- 默认关:--browser-capture 显式开启;绝不进 S3 自动扇出(适配器/探索器
  不驱动浏览器——硬限协议在此层强制,而非靠调用方自觉);
- 捕获信封 -> 证据入账时带 content_from_browser_auto 标记(审计可辨);
- 现状:Windows/WSL 均未装 playwright,执行器 dry_run 默认开,
  环境装好后 --no-dry-run 即插。
"""
from .envelopes import (
    CaptureEnvelopeError,
    toutiao_detail_to_lite,
    xhs_items_to_lite,
)
from .limits import CaptureBlocked, CaptureGate, CaptureLimits, CaptureState
from .runner import CaptureRunner, RunnerConfig

__all__ = [
    "CaptureEnvelopeError", "toutiao_detail_to_lite", "xhs_items_to_lite",
    "CaptureBlocked", "CaptureGate", "CaptureLimits", "CaptureState",
    "CaptureRunner", "RunnerConfig",
]
