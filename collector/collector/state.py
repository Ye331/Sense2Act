"""本地采集状态:每个源已推送过的 URL 与详情补抓失败次数。

后端按 URL / 内容去重兜底;这里只为不在每轮重复抓同一批详情页(对源站礼貌)。
状态丢了也无害:最多把列表里的条目重抓一遍,入库仍会被后端判重。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_SEEN_PER_SOURCE = 20000


class CollectorState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self._data = {}
        self._sources: dict = self._data.setdefault("sources", {})

    def _source(self, source_id: str) -> dict:
        return self._sources.setdefault(source_id, {"seen": {}, "attempts": {}})

    def seen(self, source_id: str, url: str) -> bool:
        return url in self._source(source_id)["seen"]

    def attempts(self, source_id: str, url: str) -> int:
        return int(self._source(source_id)["attempts"].get(url, 0))

    def failed(self, source_id: str, url: str) -> int:
        """记一次详情补抓失败,返回累计次数。"""
        attempts = self._source(source_id)["attempts"]
        attempts[url] = int(attempts.get(url, 0)) + 1
        return attempts[url]

    def mark_seen(self, source_id: str, urls: list[str]) -> None:
        entry = self._source(source_id)
        now = datetime.now(timezone.utc).isoformat()
        for url in urls:
            entry["seen"][url] = now
            entry["attempts"].pop(url, None)
        if len(entry["seen"]) > MAX_SEEN_PER_SOURCE:   # 只留最近的,旧条目早已滚出源站列表
            keep = sorted(entry["seen"].items(), key=lambda kv: kv[1])[-MAX_SEEN_PER_SOURCE:]
            entry["seen"] = dict(keep)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)   # 原子替换:进程中途被杀也不留半截文件
