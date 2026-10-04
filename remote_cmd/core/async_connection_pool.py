"""原生异步 SSH 连接池（基于 asyncssh 实现）。

本连接池完全使用 asyncssh 原生异步 API，不依赖线程池调度，可在大规模并发
批量执行场景下显著降低 CPU 与线程占用。它是项目中唯一的连接池实现
（旧的包装同步 Paramiko 版本已在 P2 合并中移除）。

设计要点：
- `asyncio.Queue` 维护空闲连接；`asyncio.Semaphore` 控制最大连接数。
- 支持连接健康检查、最大生命周期、空闲超时与后台周期清理任务。
- 暴露 `acquire_context()` 上下文管理器，便于 `async with pool.acquire_context() as conn`。
- 通过 `get_metrics()` 暴露池指标，便于上层观测与回归测试。
"""

import asyncio
import contextlib
import logging
import threading
import time
import uuid
from types import TracebackType
from typing import Any, Optional

from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.budget import ConnectionBudget
from remote_cmd.core.pool_policy import (
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.utils.exceptions import PoolClosedError

logger = logging.getLogger(__name__)


class AsyncConnectionPool:
    """原生异步 SSH 连接池。

    Args:
        config: 用于建立 SSH 连接的配置
        max_connections: 同一最大连接数（同一配置可复用）
        max_lifetime: 连接最大生命周期（秒），超过自动关闭
        idle_timeout: 空闲超时（秒），超过自动关闭
        health_check_interval: 后台清理任务周期（秒）
        connection_budget: 可选的全局连接预算（v2.6）。提供时本池创建的
            每条存活连接都占用一个预算槽位（含空闲连接），连接关闭/清理/
            close_all 时释放；跨多个池共享同一预算实例可获得全局连接上限
    """

    def __init__(
        self,
        config: ConnectionConfig,
        max_connections: int = 10,
        max_lifetime: int = 3600,
        idle_timeout: int = 300,
        health_check_interval: int = 60,
        client_factory: Optional[Any] = None,
        connection_budget: Optional[ConnectionBudget] = None,
    ) -> None:
        """
        Args:
            config: 用于建立 SSH 连接的配置
            max_connections: 同一最大连接数（同一配置可复用）
            max_lifetime: 连接最大生命周期（秒），超过自动关闭
            idle_timeout: 空闲超时（秒），超过自动关闭
            health_check_interval: 后台清理任务周期（秒）
            client_factory: 客户端工厂，默认为 AsyncSSHClient；测试可注入
                mock（与 SyncConnectionPool 对齐）
            connection_budget: 可选的全局连接预算（ConnectionBudget）
        """
        if isinstance(max_connections, bool) or not isinstance(max_connections, int):
            raise ValueError("max_connections must be a positive integer")
        if max_connections <= 0:
            raise ValueError("max_connections must be a positive integer")
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval
        # 客户端工厂：默认为 AsyncSSHClient；测试可注入 mock
        self._client_factory = client_factory or AsyncSSHClient
        self._connection_budget = connection_budget

        # 容器
        self._connections: list[AsyncSSHClient] = []
        self._free: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_connections)
        # Bookkeeping only; all mutations happen between await points on the
        # owning event loop. A regular lock keeps release/close accounting
        # non-cancellable and also protects synchronous metrics snapshots.
        self._lock = threading.Lock()
        self._acquire_waiters = 0
        self._leased: set[int] = set()
        self._closed_leases: set[int] = set()

        # 生命周期状态：close_all() 后置 True，禁止再借用/归还
        self._closed = False
        self._closed_event = threading.Event()

        # 指标
        self._total_created = 0
        self._total_reconnects = 0
        self._total_failed = 0
        self._total_released = 0

        # 后台清理任务
        self._monitor_task: Optional[asyncio.Task[None]] = None
        self._close_task: Optional[asyncio.Task[None]] = None
        self._close_in_progress = False
        self._close_finished: Optional[asyncio.Event] = None

        # 连接元数据（副表，避免侵入 AsyncSSHClient 私有属性）
        self._meta: dict[int, ConnectionMeta] = {}

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------
    def get_metrics(self) -> dict[str, Any]:
        """获取连接池指标快照。"""
        with self._lock:
            idle = self._free.qsize()
            total = len(self._connections)
            return {
                # 当前在用的连接数 = 存活连接总数 - 空闲连接数。
                # 不能用 total_created - total_released：复用连接时
                # total_released 会超过 total_created，导致 active 为负。
                "active": total - idle,
                "idle": idle,
                "total_connections": total,
                "total_created": self._total_created,
                "reconnects": self._total_reconnects,
                "failed": self._total_failed,
                "max_connections": self._max,
                "max_lifetime": self._max_lifetime,
                "idle_timeout": self._idle_timeout,
            }

    # ------------------------------------------------------------------
    # 获取 / 释放
    # ------------------------------------------------------------------
    async def acquire(self) -> AsyncSSHClient:
        """从池中获取一个可用连接，必要时创建新连接。

        Returns:
            AsyncSSHClient: 可用的异步客户端

        Raises:
            SSHConnectionError: 创建连接失败
            PoolClosedError: 连接池已关闭（close_all 之后），
                同时是 RuntimeError 子类（既有捕获行为不变）
        """
        if self._closed:
            raise PoolClosedError("connection pool is closed")
        self._acquire_waiters += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._acquire_waiters -= 1

        # close_all() 为已登记 waiter 发放关闭唤醒许可；已关闭池不再
        # 归还该许可，避免在生命周期结束后制造虚假容量。
        if self._closed:
            raise PoolClosedError("connection pool is closed")
        try:
            # 优先复用空闲连接
            while True:
                if self._closed:
                    raise PoolClosedError("connection pool is closed")
                try:
                    conn = self._free.get_nowait()
                except asyncio.QueueEmpty:
                    conn = None
                if conn is None:
                    break

                try:
                    healthy = await self._check_connection(conn)
                except BaseException:
                    await self._close_connection(conn)
                    raise
                if healthy:
                    if not self._closed:
                        self._touch(conn)
                        self._leased.add(id(conn))
                        return conn
                    await self._close_connection(conn)
                    raise PoolClosedError("connection pool is closed")
                await self._close_connection(conn)

            # 创建新连接（信号量已保证未超额）
            conn = await self._create_connection()
            if self._closed or id(conn) not in self._meta:
                raise PoolClosedError("connection pool is closed")
            self._leased.add(id(conn))
            return conn
        except BaseException:
            if not self._closed:
                self._semaphore.release()
            raise

    async def release(self, conn: Optional[AsyncSSHClient]) -> None:
        """归还连接到池中（如已断开/超时则关闭）。"""
        if conn is None:
            return
        conn_id = id(conn)
        with self._lock:
            if conn_id in self._leased:
                self._leased.remove(conn_id)
                self._total_released += 1
            elif conn_id in self._closed_leases:
                self._closed_leases.remove(conn_id)
                self._total_released += 1
            else:
                return
            pool_closed = self._closed
            meta = self._meta.get(conn_id)
        if pool_closed:
            await self._close_connection(conn)
            return

        if meta is not None:
            meta.last_used = time.time()

        try:
            connected = conn.is_connected()
        except BaseException:
            await self._close_connection(conn)
            if not self._closed:
                self._semaphore.release()
            raise

        close_conn = not connected or (
            meta is not None and should_close(meta, self._max_lifetime, self._idle_timeout, True)
        )
        if close_conn:
            try:
                await self._close_connection(conn)
            finally:
                if not self._closed:
                    self._semaphore.release()
            return

        try:
            with self._lock:
                if self._closed:
                    close_conn = True
                else:
                    self._free.put_nowait(conn)
                    # 放回 free 后释放许可：free 中的连接不再占用并发槽位，
                    # 后续 acquire 会从 free 直接复用（无需再次获取许可）
                    self._semaphore.release()
        except asyncio.QueueFull:
            close_conn = True
            if not self._closed:
                self._semaphore.release()
        if close_conn:
            await self._close_connection(conn)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _create_connection(self) -> AsyncSSHClient:
        client = self._client_factory(self.config)
        budget_held = False
        try:
            if self._connection_budget is not None:
                await self._connection_budget.acquire_async(cancel_event=self._closed_event)
                budget_held = True
            if self._closed:
                raise PoolClosedError("connection pool is closed")
            await client.connect()
            now = time.time()
            with self._lock:
                if self._closed:
                    raise PoolClosedError("connection pool is closed")
                self._connections.append(client)
                self._meta[id(client)] = ConnectionMeta(
                    created_at=now,
                    last_used=now,
                    conn_id=uuid.uuid4().hex,
                )
                self._total_created += 1
                # Budget 所有权转移至由 pool 跟踪的 live connection。
                budget_held = False
        except BaseException as exc:
            # connect() 可能在取消或 close race 时已创建底层资源；即使
            # connect 失败也显式 disconnect，并只归还尚未转移的预算槽位。
            cleanup = asyncio.create_task(client.disconnect())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # 保证清理结束后再传播原始取消/异常。
                    continue
                except Exception:  # noqa: BLE001 - best-effort close of an untracked client
                    break
            with contextlib.suppress(Exception, asyncio.CancelledError):
                cleanup.result()
            if budget_held and self._connection_budget is not None:
                self._connection_budget.release()
            if isinstance(exc, Exception):
                self._total_failed += 1
            raise
        return client

    def _touch(self, conn: AsyncSSHClient) -> None:
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta.last_used = time.time()

    async def _check_connection(self, conn: AsyncSSHClient) -> bool:
        if not conn.is_connected():
            return False
        meta = self._meta.get(id(conn))
        if meta is None:
            return True
        if lifetime_expired(meta.created_at, self._max_lifetime):
            logger.debug("connection %s exceeded max lifetime", meta.conn_id[:8])
            return False
        # 连接刚使用过（空闲未超时）则信任其状态，避免频繁探活开销
        # （与 SyncConnectionPool._check_connection 保持一致）
        if not idle_expired(meta.last_used, self._idle_timeout):
            return True
        # 空闲较久才触发轻量探活：发出一个无害命令
        try:
            result = await conn.execute("true", timeout=5)
            return result.success
        except Exception as e:  # noqa: BLE001
            self._total_reconnects += 1
            logger.debug("connection liveness check failed: %s", e)
            return False

    async def _close_connection(self, conn: AsyncSSHClient) -> None:
        # 在首个 await 前摘除状态，确保 close_all / release / monitor 并发
        # 关闭同一连接时，budget 只释放一次。
        with self._lock:
            self._meta.pop(id(conn), None)
            tracked = any(existing is conn for existing in self._connections)
            if tracked:
                self._connections = [
                    existing for existing in self._connections if existing is not conn
                ]
            self._leased.discard(id(conn))
        try:
            await conn.disconnect()
        except asyncio.CancelledError:
            if tracked and self._connection_budget is not None:
                self._connection_budget.release()
            raise
        except Exception:  # noqa: BLE001 - cleanup must continue
            logger.debug("error closing pooled SSH connection", exc_info=True)
        # exactly-once：仅对仍被池追踪的连接释放预算。
        # close_all 与 release 可能对同一连接重复调用 _close_connection，
        # 用 tracked 守卫避免预算被超额释放（BoundedSemaphore 会抛 ValueError）
        if tracked and self._connection_budget is not None:
            self._connection_budget.release()

    # ------------------------------------------------------------------
    # 后台监控
    # ------------------------------------------------------------------
    def _start_monitor(self) -> None:
        if self._closed:
            raise PoolClosedError("connection pool is closed")
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    def stop_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                # 后台清理任务必须吞掉一切非取消异常以保持存活（单次清理
                # 失败不应终止后续周期）；保留完整堆栈便于定位
                logger.warning("connection pool monitor error", exc_info=True)

    async def _cleanup_expired(self) -> None:
        now = time.time()
        # 在锁保护下排空 _free 快照，绝不替换队列对象。
        # 旧实现 self._free = kept 会替换队列对象，在 await 让出点 release()
        # 可能 put 到旧队列导致连接泄漏（asyncio 协程交错使竞态比同步版更易触发）。
        with self._lock:
            snapshot: list[AsyncSSHClient] = []
            while True:
                try:
                    snapshot.append(self._free.get_nowait())
                except asyncio.QueueEmpty:
                    break
        keep: list[AsyncSSHClient] = []
        for conn in snapshot:
            meta = self._meta.get(id(conn))
            if should_close(meta, self._max_lifetime, self._idle_timeout, conn.is_connected(), now):
                await self._close_connection(conn)
                continue
            keep.append(conn)
        # 将存活连接放回同一队列对象
        close_after_snapshot: list[AsyncSSHClient] = []
        with self._lock:
            if self._closed:
                close_after_snapshot = keep
            else:
                for conn in keep:
                    self._free.put_nowait(conn)
        for conn in close_after_snapshot:
            await self._close_connection(conn)

    # ------------------------------------------------------------------
    # 上下文管理
    # ------------------------------------------------------------------
    class _AcquireContext:
        def __init__(self, pool: "AsyncConnectionPool") -> None:
            self._pool = pool
            self._conn: Optional[AsyncSSHClient] = None

        async def __aenter__(self) -> AsyncSSHClient:
            self._conn = await self._pool.acquire()
            return self._conn

        async def __aexit__(
            self,
            exc_type: Optional[type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType],
        ) -> None:
            await self._pool.release(self._conn)
            self._conn = None

    def acquire_context(self) -> "_AcquireContext":
        """获取连接的上下文管理器。"""
        return AsyncConnectionPool._AcquireContext(self)

    async def close_all(self) -> None:
        """关闭池中所有连接并停止监控。"""
        if self._close_finished is None:
            self._close_finished = asyncio.Event()
        if self._close_task is not None:
            await asyncio.shield(self._close_task)
            return
        if self._close_in_progress:
            await self._close_finished.wait()
            if self._close_task is not None:
                await asyncio.shield(self._close_task)
            return
        if self._closed and self._close_finished.is_set():
            return

        self._close_in_progress = True
        if self._close_task is None:
            self._closed = True
            self._closed_event.set()
            # 唤醒 per-pool semaphore waiter；它们醒来后检查 _closed 并退出。
            for _ in range(self._acquire_waiters):
                self._semaphore.release()
            self._closed_leases.update(self._leased)
            self._leased.clear()
            with self._lock:
                while True:
                    try:
                        self._free.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                conns = list(self._connections)
            if self._connection_budget is not None:
                self._connection_budget._notify_waiters()
            self.stop_monitor()
        monitor = self._monitor_task
        try:
            if monitor is not None:
                await asyncio.gather(monitor, return_exceptions=True)
            for conn in conns:
                await self._close_connection(conn)
        except asyncio.CancelledError:
            # Normal close uses no auxiliary Task. If caller cancellation
            # interrupts cleanup, shield a recovery Task so resources are not
            # orphaned, then propagate cancellation.
            self._close_task = asyncio.create_task(self._finish_close_all(conns, monitor))
            cleanup = self._close_task
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(Exception):
                cleanup.result()
            self._close_in_progress = False
            self._close_finished.set()
            raise
        else:
            self._close_in_progress = False
            self._close_finished.set()

    async def _finish_close_all(
        self,
        connections: list[AsyncSSHClient],
        monitor: Optional[asyncio.Task[None]],
    ) -> None:
        if monitor is not None:
            await asyncio.gather(monitor, return_exceptions=True)
        for conn in connections:
            await self._close_connection(conn)

    async def __aenter__(self) -> "AsyncConnectionPool":
        self._start_monitor()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close_all()


__all__ = ["AsyncConnectionPool"]
