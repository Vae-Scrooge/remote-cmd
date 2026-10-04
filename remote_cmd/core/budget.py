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

同一实例可被同步与异步执行器共享：容量与全局 FIFO 等待队列由单个
``threading.Condition`` 保护；异步等待者注册 ``asyncio.Future``，
释放时通过 ``loop.call_soon_threadsafe`` 精准唤醒：
取代 v2.6 的 10→50ms 轮询，无空转 wakeup）。

唤醒语义（condition-variable 模式）：
- 同步和异步等待者共用 FIFO 队列，容量在唤醒前即被预留；新到达者
  不能抢占已等待者，因此持续流量下也不会造成 waiter starvation。
- 等待者超时/取消会从队列移除；若槽位已预留，则原子归还并派给下一个
  waiter，不存在丢失唤醒窗口。

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
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Deque, Iterator, Optional

from remote_cmd.utils.exceptions import BudgetTimeoutError, PoolClosedError, ValidationError


@dataclass(eq=False)
class _BudgetWaiter:
    """同步/异步共用的 FIFO 等待者记录。"""

    cancel_event: Optional[threading.Event]
    future: Optional[asyncio.Future[None]] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    granted: bool = False


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
            or (isinstance(acquire_timeout, float) and not math.isfinite(acquire_timeout))
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
        self._waiters: Deque[_BudgetWaiter] = deque()

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
                "waiters": len(self._waiters),
            }

    # ------------------------------------------------------------------
    # 同步接口
    # ------------------------------------------------------------------
    def acquire(self, cancel_event: Optional[threading.Event] = None) -> None:
        """获取一个连接预算槽位（阻塞，受 ``acquire_timeout`` 约束）。

        ``cancel_event`` 是池内部使用的可选生命周期信号；池关闭时会通知
        条件变量，使等待者立即以 ``PoolClosedError`` 退出，而不是一直等到
        其他池释放容量。

        Raises:
            BudgetTimeoutError: 等待超过 ``acquire_timeout``
            PoolClosedError: ``cancel_event`` 在等待期间被置位
        """
        timeout = self._acquire_timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            if cancel_event is not None and cancel_event.is_set():
                raise PoolClosedError("connection pool is closed")
            if self._in_use < self._max and not self._waiters:
                self._reserve_slot_locked()
                return

            waiter = _BudgetWaiter(cancel_event=cancel_event)
            self._waiters.append(waiter)
            self._dispatch_waiters_locked()
            try:
                while not waiter.granted:
                    if cancel_event is not None and cancel_event.is_set():
                        self._cancel_waiter_locked(waiter)
                        self._dispatch_waiters_locked()
                        raise PoolClosedError("connection pool is closed")
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        self._total_timeouts += 1
                        self._cancel_waiter_locked(waiter)
                        self._dispatch_waiters_locked()
                        raise self._timeout_error(timeout)
                    self._cond.wait(remaining)
                if cancel_event is not None and cancel_event.is_set():
                    self._cancel_waiter_locked(waiter)
                    self._dispatch_waiters_locked()
                    raise PoolClosedError("connection pool is closed")
            except (BudgetTimeoutError, PoolClosedError):
                raise
            except BaseException:
                self._cancel_waiter_locked(waiter)
                self._dispatch_waiters_locked()
                raise

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
            self._dispatch_waiters_locked()

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
    async def acquire_async(self, cancel_event: Optional[threading.Event] = None) -> None:
        """获取一个连接预算槽位（asyncio 原生等待，受 ``acquire_timeout`` 约束）。

        通过注册 ``asyncio.Future`` 等待释放方唤醒，不使用轮询。
        ``cancel_event`` 为池关闭信号；配合 :meth:`_notify_waiters` 可取消
        正在等待共享预算的建连操作。
        """
        timeout = self._acquire_timeout
        deadline = None if timeout is None else time.monotonic() + timeout
        loop = asyncio.get_running_loop()
        with self._cond:
            if cancel_event is not None and cancel_event.is_set():
                raise PoolClosedError("connection pool is closed")
            if self._in_use < self._max and not self._waiters:
                self._reserve_slot_locked()
                return
            waiter = _BudgetWaiter(
                cancel_event=cancel_event,
                future=loop.create_future(),
                loop=loop,
            )
            self._waiters.append(waiter)
            self._dispatch_waiters_locked()
            granted_immediately = waiter.granted

        if not granted_immediately:
            future = waiter.future
            assert future is not None
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                if remaining is None:
                    await future
                else:
                    await asyncio.wait_for(future, remaining)
            except asyncio.TimeoutError:
                with self._cond:
                    self._total_timeouts += 1
                    self._cancel_waiter_locked(waiter)
                    self._dispatch_waiters_locked()
                raise self._timeout_error(timeout) from None
            except asyncio.CancelledError:
                with self._cond:
                    self._cancel_waiter_locked(waiter)
                    self._dispatch_waiters_locked()
                raise

        with self._cond:
            if cancel_event is not None and cancel_event.is_set():
                self._cancel_waiter_locked(waiter)
                self._dispatch_waiters_locked()
                raise PoolClosedError("connection pool is closed")
            if not waiter.granted:
                # close notification may complete the future without granting a
                # slot; the cancel_event check above normally consumes this path.
                self._cancel_waiter_locked(waiter)
                self._dispatch_waiters_locked()
                raise RuntimeError("connection budget waiter woke without a reserved slot")

    async def release_async(self) -> None:
        """释放一个连接预算槽位（异步接口；与 :meth:`release` 等价）。"""
        self.release()

    def _notify_waiters(self) -> None:
        """通知预算等待者重新检查容量或池生命周期。

        由同步/异步连接池在关闭时调用；关闭 token 置位后，等待者会被
        唤醒并检查 ``cancel_event``，已派发槽位则由 acquire 取消路径归还。
        """
        with self._cond:
            self._cond.notify_all()
            for waiter in tuple(self._waiters):
                if waiter.cancel_event is not None and waiter.cancel_event.is_set():
                    if waiter.future is None:
                        self._cond.notify_all()
                    else:
                        self._schedule_async_waiter_locked(waiter)

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
    def _reserve_slot_locked(self) -> None:
        """在持锁状态下预留一个连接预算槽位。"""
        self._in_use += 1
        self._total_acquired += 1

    def _cancel_waiter_locked(self, waiter: _BudgetWaiter) -> None:
        """移除 waiter；若容量已预留，则原子归还该槽位。"""
        if waiter.granted:
            waiter.granted = False
            self._in_use -= 1
            self._total_released += 1
        with contextlib.suppress(ValueError):
            self._waiters.remove(waiter)

    def _dispatch_waiters_locked(self) -> None:
        """按全局 FIFO 顺序预留可用槽位并唤醒 waiter。"""
        while self._in_use < self._max and self._waiters:
            waiter = self._waiters.popleft()
            if waiter.cancel_event is not None and waiter.cancel_event.is_set():
                if waiter.future is None:
                    self._cond.notify_all()
                else:
                    self._schedule_async_waiter_locked(waiter)
                continue
            if waiter.future is not None and (
                waiter.future.done() or not self._schedule_async_waiter_locked(waiter)
            ):
                continue
            waiter.granted = True
            self._reserve_slot_locked()
            if waiter.future is None:
                self._cond.notify_all()

    def _schedule_async_waiter_locked(self, waiter: _BudgetWaiter) -> bool:
        """在 waiter 所属事件循环中完成 Future；必须持 budget 锁调用。"""
        future = waiter.future
        loop = waiter.loop
        if future is None or loop is None or future.done() or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(self._resolve_waiter, future)
        except RuntimeError:
            return False
        return True

    def _timeout_error(self, timeout: Optional[float]) -> BudgetTimeoutError:
        return BudgetTimeoutError(
            "connection budget exhausted: "
            f"waited {timeout}s for a slot "
            f"(max_connections={self._max})"
        )

    def _resolve_waiter(self, fut: asyncio.Future[None]) -> None:
        """在事件循环线程内完成已派发 waiter 的 Future。"""
        if not fut.done():
            fut.set_result(None)


__all__ = ["ConnectionBudget"]
