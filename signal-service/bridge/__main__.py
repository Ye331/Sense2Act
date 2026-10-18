"""python -m bridge:检测 worker。

环境变量:BACKEND_URL、INTERNAL_API_KEY(与后端一致)、SIGNAL_COMPANY_PROFILE(无关注画像时的兜底画像)、
SIGNAL_POLL_SECONDS、SIGNAL_CONFIG、SIGNAL_MODEL_PATH(语义模型目录)。
"""
import logging
import os
import time

import httpx

from core.config import load_config
from signals.common.task_client import TaskClient
from signals.tender.service import TenderService
from .detection import BackendClient, DetectionBridge

log = logging.getLogger("signal.bridge")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # 轮询请求不刷屏
    key = os.environ.get("INTERNAL_API_KEY")
    if not key:
        raise SystemExit("需要环境变量 INTERNAL_API_KEY(与后端一致)")
    config = load_config(os.getenv("SIGNAL_CONFIG"))
    interval = float(os.getenv("SIGNAL_POLL_SECONDS", "10"))
    # 信号经 bridge 回推后端,TenderService 自带的任务提交保持关闭
    service = TenderService(config, task_client=TaskClient(url=""))
    bridge = DetectionBridge(BackendClient(os.getenv("BACKEND_URL", "http://127.0.0.1:8080"), key), service,
                             os.getenv("SIGNAL_COMPANY_PROFILE") or config["bridge"]["company_profile"])
    log.info("detection worker up, polling every %.0fs", interval)
    while True:
        try:
            result = bridge.run_once()
            if result["scanned"]:
                log.info("scanned %(scanned)d documents, pushed %(signals)d signals", result)
                continue            # 队列可能还有积压,立即再取
        except httpx.TransportError as exc:
            log.warning("backend unreachable (%s), retrying", exc)
        except (FileNotFoundError, ImportError, OSError) as exc:
            # 语义模型缺失/后端不可达:不标记已扫描,文档留在队列里等恢复
            log.error("detection unavailable: %s", exc)
        except Exception:  # noqa: BLE001 —— 单批失败不退出常驻进程
            log.exception("detection batch failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
