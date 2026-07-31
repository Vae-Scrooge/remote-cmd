"""
Async SSH 客户端与连接池实现（基于 asyncio 进行封装）

本文件实现 AsyncSSHClient，通过组合模式复用 Sync SSHClient 的实现，
避免阻塞事件循环。

特性：
- async connect / disconnect / execute / execute_sudo / upload_file / download_file
- 支持 async with 上下文管理器
- ConnectionPool 提供对 AsyncSSHClient 的连接复用与并发管理
"""

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Any, Optional

from .ssh_client import CommandResult, ConnectionConfig, SSHClient

logger = logging.getLogger(__name__)


class AsyncSSHClient:
    """Async 版本的 SSHClient，内部通过组合复用 Sync SSHClient 的实现。"""

    def __init__(self, config: ConnectionConfig, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.config = config
        self._sync: SSHClient = SSHClient(config)
        self._loop = loop or asyncio.get_event_loop()
        self._connected = False
        # 连接元数据（用于连接池管理）
        self._created_at: float = time.time()
        self._last_used: float = time.time()
        self._connection_id: str = uuid.uuid4().hex

    # ------------------------------------------------------------------
    # Async API (wrapping sync SSHClient calls in thread pool)
    # ------------------------------------------------------------------
    async def connect(self) -> "AsyncSSHClient":
        if self.is_connected():
            return self
        await self._loop.run_in_executor(None, self._connect_sync)
        return self

    async def disconnect(self) -> None:
        await self._loop.run_in_executor(None, self._disconnect_sync)

    def is_connected(self) -> bool:
        return self._connected

    async def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        return await self._loop.run_in_executor(
            None, self._execute_sync, command, timeout, environment
        )

    async def execute_sudo(
        self,
        command: str,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        return await self._loop.run_in_executor(
            None, self._execute_sudo_sync, command, password, timeout
        )

    async def upload_file(self, local_path: str, remote_path: str) -> None:
        return await self._loop.run_in_executor(None, self._upload_sync, local_path, remote_path)

    async def download_file(self, remote_path: str, local_path: str) -> None:
        return await self._loop.run_in_executor(None, self._download_sync, remote_path, local_path)

    async def list_remote_directory(self, remote_path: str = ".") -> list[dict[str, Any]]:
        return await self._loop.run_in_executor(None, self._list_dir_sync, remote_path)

    # ------------------------------------------------------------------
    # Sync helpers wrapping sync SSHClient for thread-pool execution
    # ------------------------------------------------------------------
    def _connect_sync(self) -> None:
        self._sync.connect()
        self._connected = True

    def _disconnect_sync(self) -> None:
        self._sync.disconnect()
        self._connected = False

    def _execute_sync(
        self,
        command: str,
        timeout: Optional[int] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        return self._sync.execute(command, timeout=timeout, environment=environment)

    def _execute_sudo_sync(
        self,
        command: str,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        return self._sync.execute_sudo(command, password=password, timeout=timeout)

    def _upload_sync(self, local_path: str, remote_path: str) -> None:
        self._sync.upload_file(local_path, remote_path)

    def _download_sync(self, remote_path: str, local_path: str) -> None:
        self._sync.download_file(remote_path, local_path)

    def _list_dir_sync(self, remote_path: str) -> list[dict[str, Any]]:
        return self._sync.list_remote_directory(remote_path)

    # ------------------------------------------------------------------
    # Async context manager support
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "AsyncSSHClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()


class ConnectionPool:
    """
    异步 SSH 连接池，复用 AsyncSSHClient 实例。

    支持：
    - 连接健康检查（获取时自动验证）
    - 自动重连（失效连接自动重建）
    - 最大连接生命周期（超时自动关闭）
    - Idle Timeout（空闲超时自动关闭）
    - 连接状态监控（周期性清理）
    - 池指标收集
    """

    def __init__(
        self,
        config: ConnectionConfig,
        max_connections: int = 10,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        max_lifetime: int = 3600,
        idle_timeout: int = 300,
        health_check_interval: int = 60,
    ):
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval
        self._loop = loop or asyncio.get_event_loop()
        self._connections: list[AsyncSSHClient] = []
        self._free: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()
        self._lock = asyncio.Lock()

        # 指标统计
        self._total_created = 0
        self._total_reconnects = 0
        self._total_failed = 0
        self._total_released = 0

        # 后台监控任务
        self._monitor_task: Optional[asyncio.Task] = None

    def get_metrics(self) -> dict[str, Any]:
        """获取连接池指标"""
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

    async def _create_connection(self) -> AsyncSSHClient:
        client = AsyncSSHClient(self.config, loop=self._loop)
        client._created_at = time.time()
        client._last_used = time.time()
        await client.connect()
        self._connections.append(client)
        self._total_created += 1
        return client

    async def acquire(self) -> AsyncSSHClient:
        """从池中获取连接（带健康检查和自动重连）"""
        # 尝试从空闲队列获取
        try:
            conn = self._free.get_nowait()
            # 健康检查：验证连接是否存活且在有效期内
            if await self._check_connection(conn):
                conn._last_used = time.time()
                return conn
            # 连接不健康，关闭并重试
            await self._close_connection(conn)
        except asyncio.QueueEmpty:
            pass

        # 尝试创建新连接
        async with self._lock:
            connected = [c for c in self._connections if c.is_connected()]
            if len(connected) < self._max:
                return await self._create_connection()

        # 阻塞等待可用连接
        conn = await self._free.get()
        if not await self._check_connection(conn):
            await self._close_connection(conn)
            return await self._create_connection()
        conn._last_used = time.time()
        return conn

    async def _check_connection(self, conn: AsyncSSHClient) -> bool:
        """检查连接是否可用"""
        if not conn.is_connected():
            return False

        # 检查连接生命周期
        age = time.time() - conn._created_at
        if age > self._max_lifetime:
            logger.debug(f"连接 {conn._connection_id[:8]} 已超过最大生命周期")
            return False

        # 尝试重连（如果连接标记为断开）
        try:
            if not conn._sync.is_connected():
                logger.debug(f"连接 {conn._connection_id[:8]} 已断开，尝试重连")
                await conn.connect()
                self._total_reconnects += 1
            return True
        except (OSError, Exception) as e:  # noqa: BLE001
            logger.debug(f"连接 {conn._connection_id[:8]} 检查失败: {e}")
            self._total_failed += 1
            return False

    async def _close_connection(self, conn: AsyncSSHClient) -> None:
        """安全关闭连接"""
        with contextlib.suppress(Exception):
            await conn.disconnect()
        if conn in self._connections:
            self._connections.remove(conn)

    async def release(self, conn: AsyncSSHClient) -> None:
        """释放连接回池中"""
        if conn and conn.is_connected():
            # 检查 idle timeout（需在刷新 last_used 之前计算）
            idle = time.time() - conn._last_used
            if idle > self._idle_timeout:
                logger.debug(f"连接 {conn._connection_id[:8]} 空闲超时，关闭")
                await self._close_connection(conn)
                self._total_released += 1
                return
            conn._last_used = time.time()
            try:
                self._free.put_nowait(conn)
                self._total_released += 1
            except asyncio.QueueFull:
                await self._close_connection(conn)
                self._total_released += 1
        else:
            with contextlib.suppress(Exception):
                await conn.disconnect()
            if conn in self._connections:
                self._connections.remove(conn)
            self._total_released += 1

    def _start_monitor(self) -> None:
        """启动后台监控任务"""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.debug("连接池监控任务已启动")

    def stop_monitor(self) -> None:
        """停止后台监控任务"""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            logger.debug("连接池监控任务已停止")

    async def _monitor_loop(self) -> None:
        """后台监控循环：定期清理过期连接"""
        while True:
            try:
                await asyncio.sleep(self._health_check_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"连接池监控异常: {e}")

    async def _cleanup_expired(self) -> None:
        """清理过期和空闲超时的连接"""
        now = time.time()
        new_queue: asyncio.Queue[AsyncSSHClient] = asyncio.Queue()

        # 遍历空闲队列，过滤掉过期连接
        while not self._free.empty():
            conn = await self._free.get()
            age = now - conn._created_at
            idle = now - conn._last_used

            if age < self._max_lifetime and idle < self._idle_timeout and conn.is_connected():
                await new_queue.put(conn)
            else:
                reason = (
                    "生命周期超时"
                    if age >= self._max_lifetime
                    else "空闲超时"
                    if idle >= self._idle_timeout
                    else "连接断开"
                )
                logger.debug(f"关闭过期连接 {conn._connection_id[:8]}: {reason}")
                await self._close_connection(conn)

        self._free = new_queue

    class _PoolContext:
        def __init__(self, pool: "ConnectionPool"):
            self._pool = pool
            self.conn: Optional[AsyncSSHClient] = None

        async def __aenter__(self) -> AsyncSSHClient:
            self.conn = await self._pool.acquire()
            return self.conn

        async def __aexit__(self, exc_type, exc, tb) -> None:
            if self.conn:
                await self._pool.release(self.conn)
                self.conn = None

    def acquire_context(self) -> "_PoolContext":
        return ConnectionPool._PoolContext(self)

    async def __aenter__(self):
        self._start_monitor()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.stop_monitor()
        for c in self._connections:
            with contextlib.suppress(Exception):
                await c.disconnect()
