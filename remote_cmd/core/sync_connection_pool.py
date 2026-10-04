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
from types import TracebackType
from typing import Any, Optional

from remote_cmd.core.budget import ConnectionBudget
from remote_cmd.core.pool_policy import (
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)
from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.utils.exceptions import PoolClosedError

logger = logging.getLogger(__name__)


class SyncConnectionPool:
    """同步 SSH 连接池。

    Args:
        config: 用于建立 SSH 连接的配置
        max_connections: 最大连接数（同一配置可复用）
        max_lifetime: 连接最大生命周期（秒），超过自动关闭
        idle_timeout: 空闲超时（秒），超过自动关闭
        health_check_interval: 后台清理线程周期（秒）
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
        if isinstance(max_connections, bool) or not isinstance(max_connections, int):
            raise ValueError("max_connections must be a positive integer")
        if max_connections <= 0:
            raise ValueError("max_connections must be a positive integer")
        self.config = config
        self._max = max_connections
        self._max_lifetime = max_lifetime
        self._idle_timeout = idle_timeout
        self._health_check_interval = health_check_interval
        # 客户端工厂：默认为 SSHClient；测试可注入 mock
        self._client_factory = client_factory or SSHClient
        self._connection_budget = connection_budget

        # 容器
        self._connections: list[SSHClient] = []
        self._free: queue.Queue[SSHClient] = queue.Queue()
        self._semaphore = threading.Semaphore(max_connections)
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
    def acquire(self) -> SSHClient:
        """从池中获取一个可用连接，必要时创建新连接。

        Returns:
            SSHClient: 可用的同步客户端

        Raises:
            SSHConnectionError: 创建连接失败
            PoolClosedError: 连接池已关闭（close_all 之后），
                同时是 RuntimeError 子类（既有捕获行为不变）
        """
        with self._lock:
            if self._closed:
                raise PoolClosedError("connection pool is closed")
            self._acquire_waiters += 1
        try:
            self._semaphore.acquire()
        finally:
            with self._lock:
                self._acquire_waiters -= 1

        # close_all() 为已登记的 semaphore waiter 发放关闭唤醒许可。
        # 已关闭池不再归还该许可：池生命周期已结束，且归还会制造虚假容量。
        with self._lock:
            if self._closed:
                raise PoolClosedError("connection pool is closed")
        try:
            # 优先复用空闲连接
            while True:
                with self._lock:
                    if self._closed:
                        raise PoolClosedError("connection pool is closed")
                    try:
                        conn = self._free.get_nowait()
                    except queue.Empty:
                        conn = None
                if conn is None:
                    break

                try:
                    healthy = self._check_connection(conn)
                except BaseException:
                    self._close_connection(conn)
                    raise
                if healthy:
                    with self._lock:
                        if not self._closed:
                            self._touch(conn)
                            self._leased.add(id(conn))
                            return conn
                    self._close_connection(conn)
                    raise PoolClosedError("connection pool is closed")
                self._close_connection(conn)

            # 创建新连接（信号量已保证未超额）
            conn = self._create_connection()
            with self._lock:
                if self._closed or id(conn) not in self._meta:
                    raise PoolClosedError("connection pool is closed")
                self._leased.add(id(conn))
            return conn
        except BaseException:
            with self._lock:
                pool_closed = self._closed
            if not pool_closed:
                self._semaphore.release()
            raise

    def release(self, conn: Optional[SSHClient]) -> None:
        """归还连接到池中（如已断开/超时则关闭）。"""
        if conn is None:
            return
        conn_id = id(conn)
        with self._lock:
            # 防止重复 release 把 semaphore 计数抬高，或把同一连接重复塞入
            # free queue。外部/已归还连接不是当前 pool 的有效 lease。
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
            self._close_connection(conn)
            return

        if meta is not None:
            meta.last_used = time.time()
        try:
            connected = conn.is_connected()
        except BaseException:
            self._close_connection(conn)
            with self._lock:
                if not self._closed:
                    self._semaphore.release()
            raise

        close_conn = not connected or (
            meta is not None and should_close(meta, self._max_lifetime, self._idle_timeout, True)
        )
        if close_conn:
            self._close_connection(conn)
            with self._lock:
                if not self._closed:
                    self._semaphore.release()
            return

        # release 与 close_all 必须在同一把锁下决定：否则 close_all 可能
        # drain free queue 后，release 再把连接放进一个已关闭池的队列。
        with self._lock:
            if self._closed:
                close_conn = True
            else:
                try:
                    self._free.put_nowait(conn)
                except queue.Full:
                    close_conn = True
                self._semaphore.release()
        if close_conn:
            self._close_connection(conn)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _create_connection(self) -> SSHClient:
        client = self._client_factory(self.config)
        budget_held = False
        try:
            if self._connection_budget is not None:
                self._connection_budget.acquire(cancel_event=self._closed_event)
                budget_held = True
            if self._closed_event.is_set():
                raise PoolClosedError("connection pool is closed")
            client.connect()
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
                # budget slot ownership transfers to the tracked connection.
                budget_held = False
        except BaseException as exc:
            with contextlib.suppress(Exception):
                client.disconnect()
            if budget_held and self._connection_budget is not None:
                self._connection_budget.release()
            if isinstance(exc, Exception):
                with self._lock:
                    self._total_failed += 1
            raise
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
        with self._lock:
            self._meta.pop(id(conn), None)
            tracked = any(existing is conn for existing in self._connections)
            if tracked:
                self._connections = [existing for existing in self._connections if existing is not conn]
        # exactly-once：仅对仍被池追踪的连接释放预算。
        # close_all 与 release 可能对同一连接重复调用 _close_connection，
        # 用 tracked 守卫避免预算被超额释放（BoundedSemaphore 会抛 ValueError）
        if tracked and self._connection_budget is not None:
            self._connection_budget.release()

    # ------------------------------------------------------------------
    # 后台监控
    # ------------------------------------------------------------------
    def start_monitor(self) -> None:
        """启动后台清理线程（幂等）。"""
        with self._lock:
            if self._closed:
                raise PoolClosedError("connection pool is closed")
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
                # 后台清理线程必须吞掉一切异常以保持存活（守护线程；
                # 单次清理失败不应终止后续周期）；保留完整堆栈便于定位
                logger.warning("connection pool monitor error", exc_info=True)

    def _cleanup_expired(self) -> None:
        now = time.time()
        # 在锁保护下排空 _free 快照，绝不替换队列对象。
        # 旧实现 self._free = kept 会替换队列对象，在替换窗口内 release()
        # 可能 put 到旧队列导致连接泄漏（未关闭、信号量已释放、池中不可见）。
        with self._lock:
            snapshot: list[SSHClient] = []
            while True:
                try:
                    snapshot.append(self._free.get_nowait())
                except queue.Empty:
                    break
        keep: list[SSHClient] = []
        for conn in snapshot:
            meta = self._meta.get(id(conn))
            if should_close(meta, self._max_lifetime, self._idle_timeout, conn.is_connected(), now):
                self._close_connection(conn)
                continue
            keep.append(conn)
        # 将存活连接放回同一队列对象
        close_after_snapshot: list[SSHClient] = []
        with self._lock:
            if self._closed:
                close_after_snapshot = keep
            else:
                for conn in keep:
                    self._free.put_nowait(conn)
        for conn in close_after_snapshot:
            self._close_connection(conn)

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

        def __exit__(
            self,
            exc_type: Optional[type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType],
        ) -> None:
            self._pool.release(self._conn)
            self._conn = None

    def close_all(self) -> None:
        """关闭池中所有连接并停止监控。"""
        with self._lock:
            first_close = not self._closed
            self._closed = True
            if first_close:
                self._closed_event.set()
                # semaphore 没有原生 close/wakeup 接口。为已登记的 acquire
                # waiter 发放临时唤醒许可；它们醒来会检查 _closed 并退出。
                for _ in range(self._acquire_waiters):
                    self._semaphore.release()
                self._closed_leases.update(self._leased)
                self._leased.clear()
                while True:
                    try:
                        self._free.get_nowait()
                    except queue.Empty:
                        break
            conns = list(self._connections)
        if first_close and self._connection_budget is not None:
            self._connection_budget._notify_waiters()
        self.stop_monitor()
        for conn in conns:
            self._close_connection(conn)

    def __enter__(self) -> "SyncConnectionPool":
        self.start_monitor()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close_all()


__all__ = ["SyncConnectionPool"]
