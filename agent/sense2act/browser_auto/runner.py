"""捕获执行器:子进程调用捕获脚本(默认经 WSL),信封经 stdout 返回。

现状(2026-09-27):Windows Python 与 WSL 系统 python3 均未装 playwright
——执行器留成薄接口,环境装好即插。F 盘脚本位置(WSL 路径):
  /mnt/f/monitor/new/Crawler-Agent/scripts/browser_auto/
脚本 CLI 参数未核对,首次真跑前需对参(dry_run=True 只打印将执行的命令)。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

F_SCRIPTS_WSL = "/mnt/f/monitor/new/Crawler-Agent/scripts/browser_auto"
SCRIPTS = {
    "toutiao": "toutiao_browser_capture.py",
    "xiaohongshu": "xhs_browser_capture.py",
}


@dataclass
class RunnerConfig:
    wsl_distro: str = "Ubuntu-22.04"
    python: str = "python3"
    script_dir: str = F_SCRIPTS_WSL
    dry_run: bool = True           # 默认演练:只打印命令,不真跑(显式关闭)


class CaptureRunner:
    def __init__(self, config: RunnerConfig | None = None):
        self.cfg = config or RunnerConfig()

    def command(self, platform: str, **kwargs) -> list[str]:
        """拼 WSL 命令;kwargs 原样作为 --key value 传给脚本(待对参)。"""
        script = SCRIPTS.get(platform)
        if script is None:
            raise ValueError(f"未支持的捕获平台: {platform}")
        inner = f"cd {self.cfg.script_dir} && {self.cfg.python} {script}"
        for key, value in kwargs.items():
            if value is not None:
                inner += f" --{key} {value}"
        return ["wsl", "-d", self.cfg.wsl_distro, "--", "bash", "-lc", inner]

    def run(self, platform: str, **kwargs) -> dict:
        """执行捕获并解析信封(stdout JSON)。dry_run 只打印命令。"""
        cmd = self.command(platform, **kwargs)
        if self.cfg.dry_run:
            print("[browser_auto:dry-run] 将执行:", " ".join(cmd))
            return {}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"捕获脚本失败({proc.returncode}): "
                               f"{proc.stderr[-300:]}")
        return json.loads(_extract_json(proc.stdout))


def _extract_json(stdout: str) -> str:
    """从 stdout 提取首个 JSON 对象(脚本可能有日志前缀)。"""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"捕获脚本 stdout 中没有 JSON: {stdout[:200]}")
    return stdout[start:end + 1]
