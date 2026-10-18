"""一轮采集:领取到期源 → 逐源抓取 → 分批推送 → 回报运行结果(契约 §9.1)。"""
from __future__ import annotations

import logging
import time

from sense2act.crawler import EgressHttpClient

from .backend import BackendError, IngestClient
from .documents import is_challenge, to_document
from .sources import ADAPTERS, SourceConfig, harvest
from .state import CollectorState

log = logging.getLogger("collector")
BATCH = 50              # 单次推送条数(后端上限 500;带 raw_html 时控制请求体大小)
MAX_ERRORS = 50         # 每次回报的错误明细上限
DETAIL_GIVE_UP = 3      # 同一条详情连续失败到此次数,按列表级入库,不再重试
# 适配器失败态 → ingest-runs 的错误阶段(后端词表 fetch/normalize/dedup/persist)
STAGE = {"parser_changed": "normalize"}


class PoliteHttp:
    """http_get 包装:两次真实出网之间至少间隔 interval 秒;同一 URL 本轮内由 egress 记忆,不重复出网。"""

    def __init__(self, egress, interval: float, sleep=time.sleep, clock=time.monotonic):
        self._egress, self._interval, self._sleep, self._clock = egress, interval, sleep, clock
        self._fetched: set[str] = set()
        self._last = float("-inf")

    def get(self, url: str, timeout: float | None = None):
        if url in self._fetched:
            return self._egress.get(url, timeout)
        wait = self._last + self._interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._fetched.add(url)
        try:
            return self._egress.get(url, timeout)
        finally:
            self._last = self._clock()


class Collector:
    def __init__(self, client: IngestClient, state: CollectorState, *, request_interval: float = 1.0,
                 http_factory=None):
        self.client, self.state = client, state
        # 每个源每轮一个新出网点:egress 的运行内记忆只在本轮有效,下一轮重新取列表
        self._http_factory = http_factory or (lambda: PoliteHttp(EgressHttpClient(timeout=15), request_interval).get)
        self._unknown: set[str] = set()

    def run_once(self) -> dict:
        due = self.client.ingest_queue()
        ran = 0
        for source in due:
            if source.get("adapter") not in ADAPTERS:
                if source["source_id"] not in self._unknown:   # 留给别的采集服务,每个源只提示一次
                    self._unknown.add(source["source_id"])
                    log.warning("source %s adapter=%r 未注册,跳过", source["source_id"], source.get("adapter"))
                continue
            self.run_source(source)
            ran += 1
        return {"due": len(due), "ran": ran}

    def run_source(self, source: dict) -> dict:
        source_id, adapter = source["source_id"], source["adapter"]
        spec, config = ADAPTERS[adapter], SourceConfig.parse(source.get("config"))
        http_get = self._http_factory()
        result = harvest(spec, http_get, config)
        if result.status not in ("success", "empty"):
            error = {"url": source.get("url"), "stage": STAGE.get(result.status, "fetch"),
                     "error": f"{result.status}: {result.message}"[:500]}
            self.client.report_run(source_id, False, {"fetched": 0}, [error])
            log.warning("source %s %s: %s", source_id, result.status, result.message)
            return {"ok": False, "status": result.status}

        candidates = [item for item in result.items if item.get("url") and config.accepts(item)]
        fresh = [item for item in candidates if not self.state.seen(source_id, item["url"])][: config.max_items]
        doc_type = config.doc_type or spec.doc_type
        fetch_detail = spec.detail if config.detail is None else config.detail
        documents, errors = [], []
        for item in fresh:
            page, error = self._detail(http_get, item["url"]) if fetch_detail else (None, None)
            if error:
                errors.append({"url": item["url"], "stage": "fetch", "error": error})
                if self.state.failed(source_id, item["url"]) < DETAIL_GIVE_UP:
                    continue                      # 下轮重试;到上限才按列表级入库
            documents.append(to_document(item, page, doc_type=doc_type, category=config.category,
                                         region=config.region, adapter=adapter))

        stats = {"fetched": len(result.items), "candidates": len(candidates), "new": len(fresh),
                 "accepted": 0, "duplicates": 0, "failed": 0}
        ok = bool(documents) or not fresh         # 有新条目却一条都没取到 = 本轮失败
        try:
            for start in range(0, len(documents), BATCH):
                batch = documents[start:start + BATCH]
                pushed = self.client.push_documents(source_id, batch)
                for key in ("accepted", "duplicates", "failed"):
                    stats[key] += pushed[key]
                self.state.mark_seen(source_id, [doc["url"] for doc in batch])
        except BackendError as exc:
            ok = False
            errors.append({"url": source.get("url"), "stage": "persist", "error": str(exc)[:500]})
        finally:
            self.state.save()
        if stats["failed"]:
            errors.append({"url": source.get("url"), "stage": "persist",
                           "error": f"{stats['failed']} 条被后端拒收(字段不合法)"})
        self.client.report_run(source_id, ok, stats, errors[:MAX_ERRORS])
        log.info("source %s ok=%s fetched=%d new=%d accepted=%d duplicates=%d failed=%d errors=%d",
                 source_id, ok, stats["fetched"], stats["new"], stats["accepted"], stats["duplicates"],
                 stats["failed"], len(errors))
        return {"ok": ok, **stats}

    @staticmethod
    def _detail(http_get, url: str) -> tuple[str | None, str | None]:
        try:
            resp = http_get(url)
        except Exception as exc:  # noqa: BLE001 —— 单条详情失败不拖垮整源
            return None, f"详情请求失败: {type(exc).__name__}"
        if resp.status_code is None or resp.status_code >= 400:
            return None, f"详情返回 HTTP {resp.status_code}"
        if is_challenge(resp.text):
            return None, "详情返回拦截/验证页,不对抗"
        return resp.text, None
