"""就绪探测：确认平台可达且代理 API Key 有效，结果带缓存。"""

from __future__ import annotations

import asyncio
import logging
import time

from .errors import UpstreamError
from .platform import PlatformClient

logger = logging.getLogger(__name__)


class ReadinessProbe:
    def __init__(self, cache_seconds: float) -> None:
        self._cache_seconds = cache_seconds
        self._lock = asyncio.Lock()
        self._checked_at: float | None = None
        self._ok = False

    def _cached(self) -> bool | None:
        if self._checked_at is None:
            return None
        if time.monotonic() - self._checked_at < self._cache_seconds:
            return self._ok
        return None

    async def check(self, platform: PlatformClient) -> bool:
        cached = self._cached()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._cached()
            if cached is not None:
                return cached
            self._ok = await self._probe(platform)
            self._checked_at = time.monotonic()
            return self._ok

    @staticmethod
    async def _probe(platform: PlatformClient) -> bool:
        try:
            resp = await platform.payments({"page_size": "1"})
        except UpstreamError as exc:
            logger.warning("readiness: 平台探测失败 %s", exc.code)
            return False
        if resp.status_code == 401:
            logger.error("readiness: 平台返回 401，代理 API Key 无效或已过期")
            return False
        if resp.status_code >= 500:
            logger.warning("readiness: 平台返回 %s", resp.status_code)
            return False
        return True
