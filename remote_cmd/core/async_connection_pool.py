"""原生异步 SSH 连接池（基于 asyncssh 实现）。

与 `remote_cmd.core.async_client.ConnectionPool`（内部包装同步 Paramiko）相比，
本连接池完全使用 asyncssh 原生异步 API，不依赖 `_sync` 句柄，因此不会触发线程
池调度，可在大规模并发批量执行场景下显著降低 CPU 与线程占用。

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
    ) -> None:
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval

        # 容器
        self._connections: list[AsyncSSHClient] = []
        self._free: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_connections)
        self._lock = asyncio.Lock()

        # 指标
        self._total_created = 0
        self._total_reconnects = 0
        self._total_failed = 0
        self._total_released = 0

        # 后台清理任务
        self._monitor_task: Optional[asyncio.Task[None]] = None

        # 连接元数据（副表，避免侵入 AsyncSSHClient 私有属性）
        self._meta: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------
    def get_metrics(self) -> dict[str, Any]:
        """获取连接池指标快照。"""
        return {
            "active": self._total_created - self._total_released,
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
        """
        await self._semaphore.acquire()
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
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta["last_used"] = time.time()

        if not conn.is_connected():
            await self._close_connection(conn)
            self._semaphore.release()
            return

        # 生命周期 / 空闲超时则关闭
        if meta and (
            time.time() - meta["created_at"] > self._max_lifetime
            or time.time() - meta["last_used"] > self._idle_timeout
        ):
            await self._close_connection(conn)
            self._semaphore.release()
            return

        try:
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
        client = AsyncSSHClient(self.config)
        try:
            await client.connect()
        except Exception:  # noqa: BLE001
            self._total_failed += 1
            self._semaphore.release()
            raise
        self._connections.append(client)
        now = time.time()
        self._meta[id(client)] = {
            "created_at": now,
            "last_used": now,
            "conn_id": uuid.uuid4().hex,
        }
        self._total_created += 1
        return client

    def _touch(self, conn: AsyncSSHClient) -> None:
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta["last_used"] = time.time()

    async def _check_connection(self, conn: AsyncSSHClient) -> bool:
        if not conn.is_connected():
            return False
        meta = self._meta.get(id(conn))
        if meta is None:
            return True
        age = time.time() - meta["created_at"]
        if age > self._max_lifetime:
            logger.debug("连接 %s 已超过最大生命周期", meta.get("conn_id", "?")[:8])
            return False
        # 触发轻量探活：发出一个无害命令
        try:
            result = await conn.execute("true", timeout=5)
            return result.success
        except Exception as e:  # noqa: BLE001
            self._total_reconnects += 1
            logger.debug("连接探活失败: %s", e)
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
                logger.warning("连接池监控异常")

    async def _cleanup_expired(self) -> None:
        now = time.time()
        kept: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()
        while not self._free.empty():
            conn = self._free.get_nowait()
            meta = self._meta.get(id(conn))
            if meta is None:
                await self._close_connection(conn)
                continue
            age = now - meta["created_at"]
            idle = now - meta["last_used"]
            if age < self._max_lifetime and idle < self._idle_timeout and conn.is_connected():
                await kept.put(conn)
            else:
                await self._close_connection(conn)
        self._free = kept

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
