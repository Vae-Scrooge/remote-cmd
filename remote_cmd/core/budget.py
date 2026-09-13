"""
全局连接预算（v2.6 P2.5；v2.7 真异步等待队列）

用于在**多个连接池 / 多个执行器**之间封顶进程内同时存活的 SSH 连接数，
避免大规模 fleet 场景下 per-pool 上限叠加导致的 fd/socket 耗尽：

    池 A(max=10) + 池 B(max=10) + …   无预算时可无限叠加
    预算(max=50)                       全局硬上限 50 条存活连接

预算语义（存活连接预算）：
- 预算单位 = **存活连接**（含池中空闲连接）。
- 获取时机：连接创建（``connect``）前；释放时机：连接断开 / 关闭 /
  被清理 / 池 ``close_all``。
- 池归还空闲连接**不**释放预算（连接仍存活）；``acquire`` 复用空闲连接
  **不**重复获取预算。

双接口：
- 同步：:meth:`acquire` / :meth:`release` / :meth:`acquire_context`
- 异步：:meth:`acquire_async` / :meth:`release_async` /
  :meth:`acquire_context_async`

同一实例可被同步与异步执行器共享：容量与等待队列由单个
``threading.Condition`` 保护；异步等待者注册 ``asyncio.Future``，
释放时通过 ``loop.call_soon_threadsafe`` 精准唤醒（v2.7 P2.5 优化：
取代 v2.6 的 10→50ms 轮询，无空转 wakeup）。

唤醒语义（condition-variable 模式）：
- 唤醒只是"提示"而非预留：被唤醒者需重新竞争容量；释放方不预占槽位。
- 异步等待者之间 FIFO；同步等待者之间 FIFO；跨类型顺序不作保证。
- 等待者超时/取消时会从队列移除；若唤醒权已派发给它，
  ``_resolve_waiter`` 检测到 future 已完成会补唤醒下一个等待者，
  不会因取消竞态丢失唤醒。

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
from collections import deque
from typing import Any, AsyncIterator, Deque, Iterator, Optional

from remote_cmd.utils.exceptions import BudgetTimeoutError, ValidationError


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

        # 单一互斥 + 条件变量：保护容量计数与等待队列（同步/异步共享）
        self._cond = threading.Condition(threading.Lock())
        self._in_use = 0
        self._async_waiters: Deque[asyncio.Future[None]] = deque()

        # 指标
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
        with self._cond:
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
        timeout = self._acquire_timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._in_use >= self._max:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._total_timeouts += 1
                    # 超时者退出前让出一次唤醒权，避免其他同步等待者
                    # 因槽位空闲却无人被通知而饥饿
                    self._cond.notify()
                    raise BudgetTimeoutError(
                        "connection budget exhausted: "
                        f"waited {timeout}s for a slot "
                        f"(max_connections={self._max})"
                    )
                self._cond.wait(remaining)
            self._in_use += 1
            self._total_acquired += 1

    def release(self) -> None:
        """释放一个连接预算槽位并唤醒一个等待者。

        Raises:
            ValueError: 释放次数超过获取次数
        """
        with self._cond:
            if self._in_use <= 0:
                raise ValueError("release without a matching acquire")
            self._in_use -= 1
            self._total_released += 1
            self._wake_next_locked()

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
        """获取一个连接预算槽位（asyncio 原生等待，受 ``acquire_timeout`` 约束）。

        通过注册 ``asyncio.Future`` 等待释放方唤醒，不使用轮询。
        """
        timeout = self._acquire_timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        loop = asyncio.get_running_loop()
        while True:
            with self._cond:
                if self._in_use < self._max:
                    self._in_use += 1
                    self._total_acquired += 1
                    return
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._total_timeouts += 1
                    self._cond.notify()
                    raise BudgetTimeoutError(
                        "connection budget exhausted: "
                        f"waited {timeout}s for a slot "
                        f"(max_connections={self._max})"
                    )
                fut: asyncio.Future[None] = loop.create_future()
                self._async_waiters.append(fut)
            try:
                if remaining is None:
                    await fut
                else:
                    await asyncio.wait_for(fut, remaining)
            except asyncio.TimeoutError:
                with self._cond:
                    self._discard_waiter_locked(fut)
                    self._total_timeouts += 1
                    # 若唤醒权已被派发给本 future（弹出后 resolve 前超时），
                    # _resolve_waiter 会发现 fut 已完成并补唤醒下一个等待者
                raise BudgetTimeoutError(
                    "connection budget exhausted: "
                    f"waited {timeout}s for a slot "
                    f"(max_connections={self._max})"
                ) from None
            except asyncio.CancelledError:
                with self._cond:
                    self._discard_waiter_locked(fut)
                raise
            # 被唤醒后回到循环顶部重新竞争容量（唤醒不预留槽位）

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

    # ------------------------------------------------------------------
    # 内部：等待队列
    # ------------------------------------------------------------------
    def _discard_waiter_locked(self, fut: asyncio.Future[None]) -> None:
        """从异步等待队列移除 future（不存在则忽略）。须持锁调用。"""
        with contextlib.suppress(ValueError):
            self._async_waiters.remove(fut)

    def _wake_next_locked(self) -> None:
        """唤醒一个等待者（异步优先 FIFO，否则通知一个同步等待者）。

        唤醒不预留槽位；被唤醒者会重新检查容量。须持锁调用。
        """
        while self._async_waiters:
            fut = self._async_waiters.popleft()
            if fut.done():
                continue
            loop = fut.get_loop()
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(self._resolve_waiter, fut)
            except RuntimeError:
                # 事件循环正在关闭：跳过该等待者，尝试下一个
                continue
            return
        self._cond.notify()

    def _resolve_waiter(self, fut: asyncio.Future[None]) -> None:
        """在事件循环线程内完成 future（或补唤醒下一个等待者）。"""
        if fut.done():
            # 等待者已超时/取消：唤醒权未被消费，补唤醒下一个
            with self._cond:
                self._wake_next_locked()
            return
        fut.set_result(None)


__all__ = ["ConnectionBudget"]
