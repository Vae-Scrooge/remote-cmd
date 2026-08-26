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
import time
import uuid
from typing import Any, Optional

from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.service._pool_policy import (
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)

logger = logging.getLogger(__name__)


class AsyncConnectionPool:
    """原生异步 SSH 连接池。

    Args:
        config: 用于建立 SSH 连接的配置
        max_connections: 同一最大连接数（同一配置可复用）
        max_lifetime: 连接最大生命周期（秒），超过自动关闭
        idle_timeout: 空闲超时（秒），超过自动关闭
        health_check_interval: 后台清理任务周期（秒）
    """

    def __init__(
        self,
        config: ConnectionConfig,
        max_connections: int = 10,
        max_lifetime: int = 3600,
        idle_timeout: int = 300,
        health_check_interval: int = 60,
        client_factory: Optional[Any] = None,
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
        """
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval
        # 客户端工厂：默认为 AsyncSSHClient；测试可注入 mock
        self._client_factory = client_factory or AsyncSSHClient

        # 容器
        self._connections: list[AsyncSSHClient] = []
        self._free: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_connections)
        self._lock = asyncio.Lock()

        # 生命周期状态：close_all() 后置 True，禁止再借用/归还
        self._closed = False

        # 指标
        self._total_created = 0
        self._total_reconnects = 0
        self._total_failed = 0
        self._total_released = 0

        # 后台清理任务
        self._monitor_task: Optional[asyncio.Task[None]] = None

        # 连接元数据（副表，避免侵入 AsyncSSHClient 私有属性）
        self._meta: dict[int, ConnectionMeta] = {}

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------
    def get_metrics(self) -> dict[str, Any]:
        """获取连接池指标快照。"""
        return {
            # 当前在用的连接数 = 存活连接总数 - 空闲连接数。
            # 不能用 total_created - total_released：复用连接时
            # total_released 会超过 total_created，导致 active 为负。
            "active": len(self._connections) - self._free.qsize(),
            "idle": self._free.qsize(),
            "total_connections": len(self._connections),
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
            RuntimeError: 连接池已关闭（close_all 之后）
        """
        if self._closed:
            raise RuntimeError("connection pool is closed")
        await self._semaphore.acquire()
        # 竞态守卫：等待信号量期间 close_all() 可能已完成——
        # 取得槽位后必须复查，已关闭则归还槽位并抛出既有错误，
        # 否则会向调用方发放来自已关闭池的连接
        if self._closed:
            self._semaphore.release()
            raise RuntimeError("connection pool is closed")
        try:
            # 优先复用空闲连接
            while not self._free.empty():
                conn = self._free.get_nowait()
                if await self._check_connection(conn):
                    self._touch(conn)
                    return conn
                await self._close_connection(conn)

            # 创建新连接（信号量已保证未超额）
            return await self._create_connection()
        except BaseException:
            self._semaphore.release()
            raise

    async def release(self, conn: Optional[AsyncSSHClient]) -> None:
        """归还连接到池中（如已断开/超时则关闭）。"""
        if conn is None:
            return
        # 池已关闭：不把连接放回空闲队列（避免游离连接），直接关闭并释放槽位
        if self._closed:
            await self._close_connection(conn)
            self._semaphore.release()
            self._total_released += 1
            return
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta.last_used = time.time()

        if not conn.is_connected():
            await self._close_connection(conn)
            self._semaphore.release()
            return

        # 生命周期 / 空闲超时则关闭
        if meta and should_close(meta, self._max_lifetime, self._idle_timeout, True):
            await self._close_connection(conn)
            self._semaphore.release()
            return

        try:
            async with self._lock:
                self._free.put_nowait(conn)
            # 放回 free 后释放许可：free 中的连接不再占用并发槽位，
            # 后续 acquire 会从 free 直接复用（无需再次获取许可）
            self._semaphore.release()
        except asyncio.QueueFull:
            await self._close_connection(conn)
            self._semaphore.release()
        finally:
            self._total_released += 1

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    async def _create_connection(self) -> AsyncSSHClient:
        client = self._client_factory(self.config)
        try:
            await client.connect()
        except Exception:  # noqa: BLE001
            # 信号量由 acquire() 的 except 统一释放，此处不再释放
            self._total_failed += 1
            raise
        self._connections.append(client)
        now = time.time()
        self._meta[id(client)] = ConnectionMeta(
            created_at=now,
            last_used=now,
            conn_id=uuid.uuid4().hex,
        )
        self._total_created += 1
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
        with contextlib.suppress(Exception):
            await conn.disconnect()
        self._meta.pop(id(conn), None)
        if conn in self._connections:
            self._connections.remove(conn)

    # ------------------------------------------------------------------
    # 后台监控
    # ------------------------------------------------------------------
    def _start_monitor(self) -> None:
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
                logger.warning("connection pool monitor error")

    async def _cleanup_expired(self) -> None:
        now = time.time()
        # 在锁保护下排空 _free 快照，绝不替换队列对象。
        # 旧实现 self._free = kept 会替换队列对象，在 await 让出点 release()
        # 可能 put 到旧队列导致连接泄漏（asyncio 协程交错使竞态比同步版更易触发）。
        async with self._lock:
            snapshot: list[AsyncSSHClient] = []
            while not self._free.empty():
                snapshot.append(self._free.get_nowait())
        keep: list[AsyncSSHClient] = []
        for conn in snapshot:
            meta = self._meta.get(id(conn))
            if should_close(meta, self._max_lifetime, self._idle_timeout, conn.is_connected(), now):
                await self._close_connection(conn)
                continue
            keep.append(conn)
        # 将存活连接放回同一队列对象
        async with self._lock:
            for conn in keep:
                self._free.put_nowait(conn)

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

        async def __aexit__(self, exc_type, exc, tb) -> None:
            await self._pool.release(self._conn)
            self._conn = None

    def acquire_context(self) -> "_AcquireContext":
        """获取连接的上下文管理器。"""
        return AsyncConnectionPool._AcquireContext(self)

    async def close_all(self) -> None:
        """关闭池中所有连接并停止监控。"""
        self._closed = True
        self.stop_monitor()
        for conn in list(self._connections):
            await self._close_connection(conn)
        # 释放所有信号量
        while self._free.empty() is False:
            self._free.get_nowait()

    async def __aenter__(self) -> "AsyncConnectionPool":
        self._start_monitor()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_all()


__all__ = ["AsyncConnectionPool"]
