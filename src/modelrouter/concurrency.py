"""等待支付接口的本地并发保护与调用方断连处理。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from fastapi import Request

from .errors import ServiceError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class WaitSlots:
    """本进程的等待支付并发槽位。

    平台对每个 worker 只允许有限个同步等待，超出会返回 429。这里在本地先做一层限制，
    满了直接返回 429 并带 Retry-After，不再占用平台配额。
    事件循环是单线程的，计数在无 await 的情况下增减，无需锁。
    """

    def __init__(self, limit: int, retry_after_seconds: int) -> None:
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        self._in_use = 0

    @property
    def in_use(self) -> int:
        return self._in_use

    @contextmanager
    def acquire(self) -> Iterator[None]:
        if self._in_use >= self.limit:
            raise ServiceError(
                429,
                "WAIT_CONCURRENCY_LIMIT",
                f"等待支付并发已达上限（{self.limit}），请稍后重试",
                headers={"Retry-After": str(self.retry_after_seconds)},
            )
        self._in_use += 1
        try:
            yield
        finally:
            self._in_use -= 1


async def run_until_disconnect(
    request: Request, awaitable: Awaitable[T], *, poll_seconds: float = 1.0
) -> T:
    """执行一个长耗时的平台调用；若调用方中途断开连接，则取消该调用并释放资源。"""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                return task.result()
            if await request.is_disconnected():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                logger.info("调用方已断开，已取消对平台的等待调用")
                raise ServiceError(499, "CLIENT_DISCONNECTED", "调用方已断开连接，已取消等待")
    except asyncio.CancelledError:
        task.cancel()
        raise
