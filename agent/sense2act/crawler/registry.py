"""适配器注册表(显式注册,无自动发现——组合根唯一注册点)。"""
from __future__ import annotations

from typing import Callable

from .base import SourceAdapter


class SourceRegistry:
    def __init__(self, factories: dict[str, Callable[[], SourceAdapter]],
                 defaults: list[str]):
        self._factories = factories
        self._defaults = defaults

    def create(self, platform: str) -> SourceAdapter:
        if platform not in self._factories:
            raise KeyError(f"未注册的采集平台: {platform}")
        return self._factories[platform]()

    def defaults(self) -> list[str]:
        return list(self._defaults)

    def platforms(self) -> list[str]:
        return list(self._factories)
