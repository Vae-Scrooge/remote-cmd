"""
全局连接预算（v2.6 P2.5；同步 / 异步内核共用）

用于在**多个连接池 / 多个执行器**之间封顶进程内同时存活的 SSH 连接数，
避免大规模 fleet 场景下 per-pool 上限叠加导致的 fd/socket 耗尽：

    池 A(max=10) + 池 B(max=10) + …   无预算时可无限叠加
    预算(max=50)                       全局硬上限 50 条存活连接

预算语义（评审定稿：存活连接预算）：
- 预算单位 = **存活连接**（含池中空闲连接）。
- 获取时机：连接创建（``connect``）前；释放时机：连接断开 / 关闭 /
  被清理 / 池 ``close_all``。
- 池归还空闲连接**不**释放预算（连接仍存活）；``acquire`` 复用空闲连接
  **不**重复获取预算。

双接口：
- 同步：:meth:`acquire` / :meth:`release` / :meth:`acquire_context`
- 异步：:meth:`acquire_async` / :meth:`release_async` /
  :meth:`acquire_context_async`

同一实例可被同步与异步执行器共享（底层是单个
``threading.BoundedSemaphore``；异步侧以非阻塞轮询等待，轮询间隔
10ms 起步、指数退避至 50ms——相对于 SSH 建连耗时（数十到数百 ms）
开销可忽略）。

超时：``acquire_timeout=None``（默认）无限等待；配置正整数/浮点秒数时
超时抛出 :class:`~remote_cmd.utils.exceptions.BudgetTimeoutError`。

用法:
    >>> from remote_cmd.core.budget import ConnectionBudget
    >>> budget = ConnectionBudget(max_connections=50)
    >>> with budget.acquire_context():
    ...     pass  # 持有预算期间创建/保有连接
    >>> budget.get_metrics()["in_use"]
    0
    >>>
    >>> async def use():
    ...     async with budget.acquire_context_async():
    ...         pass
"""

import asyncio
import contextlib
import threading
import time
from typing import Any, AsyncIterator, Iterator, Optional

from remote_cmd.utils.exceptions import BudgetTimeoutError, ValidationError

# 异步轮询等待参数（秒）：起步 10ms，指数退避至 50ms 上限
_DEFAULT_POLL_INTERVAL = 0.01
_MAX_POLL_INTERVAL = 0.05


class ConnectionBudget:
    """全局连接预算（同步 / 异步共用）。

    Args:
        max_connections: 全局最大存活连接数（正整数；bool 被拒绝）
        acquire_timeout: 获取预算的等待上限（秒）。``None``（默认）
            表示无限等待；正数表示超时后抛 ``BudgetTimeoutError``

    Raises:
        ValidationError: 参数非法（构造时校验）
    """

    def __init__(
        self,
        max_connections: int,
        acquire_timeout: Optional[float] = None,
    ) -> None:
        if (
            isinstance(max_connections, bool)
            or not isinstance(max_connections, int)
            or max_connections <= 0
        ):
            raise ValidationError(
                f"max_connections must be a positive integer, got: {max_connections!r}"
            )
        if acquire_timeout is not None and (
            isinstance(acquire_timeout, bool)
            or not isinstance(acquire_timeout, (int, float))
            or acquire_timeout <= 0
        ):
            raise ValidationError(
                f"acquire_timeout must be None or a positive number, got: {acquire_timeout!r}"
            )

        self._max = max_connections
        self._acquire_timeout = acquire_timeout
        # 单一底层信号量：同步/异步共享同一个全局上限
        self._semaphore = threading.BoundedSemaphore(max_connections)
        self._lock = threading.Lock()

        # 指标
        self._in_use = 0
        self._total_acquired = 0
        self._total_released = 0
        self._total_timeouts = 0

    # ------------------------------------------------------------------
    # 属性 / 指标
    # ------------------------------------------------------------------
    @property
    def max_connections(self) -> int:
        """全局最大存活连接数。"""
        return self._max

    @property
    def acquire_timeout(self) -> Optional[float]:
        """获取预算的等待上限（秒）；``None`` 表示无限等待。"""
        return self._acquire_timeout

    def get_metrics(self) -> dict[str, Any]:
        """预算指标快照。"""
        with self._lock:
            return {
                "max_connections": self._max,
                "in_use": self._in_use,
                "available": self._max - self._in_use,
                "total_acquired": self._total_acquired,
                "total_released": self._total_released,
                "total_timeouts": self._total_timeouts,
            }

    # ------------------------------------------------------------------
    # 同步接口
    # ------------------------------------------------------------------
    def acquire(self) -> None:
        """获取一个连接预算槽位（阻塞，受 ``acquire_timeout`` 约束）。

        Raises:
            BudgetTimeoutError: 等待超过 ``acquire_timeout``
        """
        acquired = self._semaphore.acquire(timeout=self._acquire_timeout)
        if not acquired:
            with self._lock:
                self._total_timeouts += 1
            raise BudgetTimeoutError(
                "connection budget exhausted: "
                f"waited {self._acquire_timeout}s for a slot "
                f"(max_connections={self._max})"
            )
        with self._lock:
            self._in_use += 1
            self._total_acquired += 1

    def release(self) -> None:
        """释放一个连接预算槽位。

        Raises:
            ValueError: 释放次数超过获取次数（BoundedSemaphore 保护）
        """
        # 先释放信号量：超额释放时 BoundedSemaphore 抛出 ValueError，
        # 指标不会进入不一致状态
        self._semaphore.release()
        with self._lock:
            self._in_use -= 1
            self._total_released += 1

    @contextlib.contextmanager
    def acquire_context(self) -> Iterator[None]:
        """同步上下文管理器：进入获取预算，退出释放。"""
        self.acquire()
        try:
            yield
        finally:
            self.release()

    # ------------------------------------------------------------------
    # 异步接口
    # ------------------------------------------------------------------
    async def acquire_async(self) -> None:
        """获取一个连接预算槽位（asyncio 友好，受 ``acquire_timeout`` 约束）。

        以非阻塞尝试 + 指数退避轮询等待，不阻塞事件循环。
        """
        timeout = self._acquire_timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        interval = _DEFAULT_POLL_INTERVAL
        while not self._semaphore.acquire(blocking=False):
            if deadline is not None and time.monotonic() >= deadline:
                with self._lock:
                    self._total_timeouts += 1
                raise BudgetTimeoutError(
                    "connection budget exhausted: "
                    f"waited {timeout}s for a slot "
                    f"(max_connections={self._max})"
                )
            await asyncio.sleep(interval)
            interval = min(interval * 2, _MAX_POLL_INTERVAL)
        with self._lock:
            self._in_use += 1
            self._total_acquired += 1

    async def release_async(self) -> None:
        """释放一个连接预算槽位（异步接口；与 :meth:`release` 等价）。"""
        self.release()

    @contextlib.asynccontextmanager
    async def acquire_context_async(self) -> AsyncIterator[None]:
        """异步上下文管理器：进入获取预算，退出释放。"""
        await self.acquire_async()
        try:
            yield
        finally:
            await self.release_async()


__all__ = ["ConnectionBudget"]
