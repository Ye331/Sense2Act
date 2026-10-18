"""python -m collector:常驻采集 worker。

环境变量:BACKEND_URL、INTERNAL_API_KEY(与后端一致)、COLLECTOR_POLL_SECONDS(轮询间隔,默认 60)、
COLLECTOR_REQUEST_INTERVAL(对源站两次请求的最小间隔秒数,默认 1)、COLLECTOR_STATE(本地状态文件)。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from .backend import BackendError, IngestClient
from .state import CollectorState
from .worker import Collector

log = logging.getLogger("collector")
ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m collector", description="领取到期信息源,抓取并推送文档到后端")
    parser.add_argument("--backend", default=os.getenv("BACKEND_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--interval", type=float, default=float(os.getenv("COLLECTOR_POLL_SECONDS", "60")))
    parser.add_argument("--request-interval", type=float, default=float(os.getenv("COLLECTOR_REQUEST_INTERVAL", "1")))
    parser.add_argument("--state", default=os.getenv("COLLECTOR_STATE", str(ROOT / "data" / "state.json")))
    parser.add_argument("--once", action="store_true", help="处理一轮到期源后退出")
    opts = parser.parse_args(argv)
    key = os.getenv("INTERNAL_API_KEY")
    if not key:
        parser.error("需要环境变量 INTERNAL_API_KEY(与后端一致)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    collector = Collector(IngestClient(opts.backend, key), CollectorState(opts.state),
                          request_interval=opts.request_interval)
    log.info("collector up: backend=%s poll=%.0fs", opts.backend, opts.interval)
    while True:
        try:
            result = collector.run_once()
            if result["ran"]:
                log.info("ran %(ran)d of %(due)d due sources", result)
        except (BackendError, OSError) as exc:   # 后端没起来或暂时不可达:下轮再试
            log.warning("backend unavailable: %s", exc)
        except Exception:  # noqa: BLE001 —— 单轮失败不退出常驻进程
            log.exception("collect round failed")
        if opts.once:
            return 0
        time.sleep(opts.interval)


if __name__ == "__main__":
    raise SystemExit(main())
