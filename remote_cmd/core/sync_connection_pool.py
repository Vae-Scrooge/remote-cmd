"""同步 SSH 连接池（基于 paramiko）。

与 AsyncConnectionPool 对称的同步实现。为反复执行短命令的场景
（批量执行、连接测试、脚本自动化）复用 SSH 连接，避免每次操作都
建立新连接带来的握手开销。

设计要点：
- `queue.Queue` 维护空闲连接；`threading.Semaphore` 控制最大连接数。
- 支持连接健康检查、最大生命周期、空闲超时与后台周期清理线程。
- 暴露 `acquire_context()` 上下文管理器，便于 `with pool.acquire_context() as conn`。
- 通过 `get_metrics()` 暴露池指标，便于上层观测与回归测试。
"""

import contextlib
import logging
import queue
import threading
import time
import uuid
from typing import Any, Optional

from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.service._pool_policy import (
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)

logger = logging.getLogger(__name__)


class SyncConnectionPool:
    """同步 SSH 连接池。

    Args:
        config: 用于建立 SSH 连接的配置
        max_connections: 最大连接数（同一配置可复用）
        max_lifetime: 连接最大生命周期（秒），超过自动关闭
        idle_timeout: 空闲超时（秒），超过自动关闭
        health_check_interval: 后台清理线程周期（秒）
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
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval
        # 客户端工厂：默认为 SSHClient；测试可注入 mock
        self._client_factory = client_factory or SSHClient

        # 容器
        self._connections: list[SSHClient] = []
        self._free: queue.Queue[SSHClient] = queue.Queue()
        self._semaphore = threading.Semaphore(max_connections)
        self._lock = threading.Lock()

        # 生命周期状态：close_all() 后置 True，禁止再借用/归还
        self._closed = False

        # 指标
        self._total_created = 0
        self._total_reconnects = 0
        self._total_failed = 0
        self._total_released = 0

        # 后台清理线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 连接元数据（副表，避免侵入 SSHClient 私有属性）
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
    def acquire(self) -> SSHClient:
        """从池中获取一个可用连接，必要时创建新连接。

        Returns:
            SSHClient: 可用的同步客户端

        Raises:
            SSHConnectionError: 创建连接失败
            RuntimeError: 连接池已关闭（close_all 之后）
        """
        if self._closed:
            raise RuntimeError("connection pool is closed")
        self._semaphore.acquire()
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
                if self._check_connection(conn):
                    self._touch(conn)
                    return conn
                self._close_connection(conn)

            # 创建新连接（信号量已保证未超额）
            return self._create_connection()
        except BaseException:
            self._semaphore.release()
            raise

    def release(self, conn: Optional[SSHClient]) -> None:
        """归还连接到池中（如已断开/超时则关闭）。"""
        if conn is None:
            return
        # 池已关闭：不把连接放回空闲队列（避免游离连接），直接关闭并释放槽位
        if self._closed:
            self._close_connection(conn)
            self._semaphore.release()
            self._total_released += 1
            return
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta.last_used = time.time()

        if not conn.is_connected():
            self._close_connection(conn)
            self._semaphore.release()
            return

        # 生命周期 / 空闲超时则关闭
        if meta and should_close(meta, self._max_lifetime, self._idle_timeout, True):
            self._close_connection(conn)
            self._semaphore.release()
            return

        try:
            with self._lock:
                self._free.put_nowait(conn)
            # 放回 free 后释放许可：free 中的连接不再占用并发槽位，
            # 后续 acquire 会从 free 直接复用（无需再次获取许可）
            self._semaphore.release()
        except queue.Full:
            self._close_connection(conn)
            self._semaphore.release()
        finally:
            self._total_released += 1

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _create_connection(self) -> SSHClient:
        client = self._client_factory(self.config)
        try:
            client.connect()
        except Exception:  # noqa: BLE001
            # 信号量由 acquire() 的 except 统一释放，此处不再释放
            self._total_failed += 1
            raise
        with self._lock:
            self._connections.append(client)
        now = time.time()
        self._meta[id(client)] = ConnectionMeta(
            created_at=now,
            last_used=now,
            conn_id=uuid.uuid4().hex,
        )
        self._total_created += 1
        return client

    def _touch(self, conn: SSHClient) -> None:
        meta = self._meta.get(id(conn))
        if meta is not None:
            meta.last_used = time.time()

    def _check_connection(self, conn: SSHClient) -> bool:
        if not conn.is_connected():
            return False
        meta = self._meta.get(id(conn))
        if meta is None:
            return True
        if lifetime_expired(meta.created_at, self._max_lifetime):
            logger.debug("connection %s exceeded max lifetime", meta.conn_id[:8])
            return False
        # 连接刚使用过（空闲未超时）则信任其状态，避免频繁探活开销
        if not idle_expired(meta.last_used, self._idle_timeout):
            return True
        # 空闲较久才触发轻量探活：发出一个无害命令
        try:
            result = conn.execute("true", timeout=5)
            return result.success
        except Exception as e:  # noqa: BLE001
            self._total_reconnects += 1
            logger.debug("connection liveness check failed: %s", e)
            return False

    def _close_connection(self, conn: SSHClient) -> None:
        with contextlib.suppress(Exception):
            conn.disconnect()
        self._meta.pop(id(conn), None)
        with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)

    # ------------------------------------------------------------------
    # 后台监控
    # ------------------------------------------------------------------
    def start_monitor(self) -> None:
        """启动后台清理线程（幂等）。"""
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="sync-connection-pool-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        """停止后台清理线程。"""
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._stop_event.wait(self._health_check_interval)
                if self._stop_event.is_set():
                    break
                self._cleanup_expired()
            except Exception:  # noqa: BLE001
                logger.warning("connection pool monitor error")

    def _cleanup_expired(self) -> None:
        now = time.time()
        # 在锁保护下排空 _free 快照，绝不替换队列对象。
        # 旧实现 self._free = kept 会替换队列对象，在替换窗口内 release()
        # 可能 put 到旧队列导致连接泄漏（未关闭、信号量已释放、池中不可见）。
        with self._lock:
            snapshot: list[SSHClient] = []
            while not self._free.empty():
                snapshot.append(self._free.get_nowait())
        keep: list[SSHClient] = []
        for conn in snapshot:
            meta = self._meta.get(id(conn))
            if should_close(meta, self._max_lifetime, self._idle_timeout, conn.is_connected(), now):
                self._close_connection(conn)
                continue
            keep.append(conn)
        # 将存活连接放回同一队列对象
        with self._lock:
            for conn in keep:
                self._free.put_nowait(conn)

    # ------------------------------------------------------------------
    # 上下文管理
    # ------------------------------------------------------------------
    def acquire_context(self) -> "_AcquireContext":
        """获取连接的上下文管理器。"""
        return SyncConnectionPool._AcquireContext(self)

    class _AcquireContext:
        def __init__(self, pool: "SyncConnectionPool") -> None:
            self._pool = pool
            self._conn: Optional[SSHClient] = None

        def __enter__(self) -> SSHClient:
            self._conn = self._pool.acquire()
            return self._conn

        def __exit__(self, exc_type, exc, tb) -> None:
            self._pool.release(self._conn)
            self._conn = None

    def close_all(self) -> None:
        """关闭池中所有连接并停止监控。"""
        self._closed = True
        self.stop_monitor()
        with self._lock:
            conns = list(self._connections)
        for conn in conns:
            self._close_connection(conn)
        # 释放所有信号量
        while not self._free.empty():
            self._free.get_nowait()

    def __enter__(self) -> "SyncConnectionPool":
        self.start_monitor()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_all()


__all__ = ["SyncConnectionPool"]
