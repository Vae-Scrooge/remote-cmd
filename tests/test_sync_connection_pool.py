"""SyncConnectionPool 同步连接池单元测试"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.core.sync_connection_pool import SyncConnectionPool
from remote_cmd.utils.exceptions import SSHConnectionError


def _client_mock(connected=True):
    c = MagicMock()
    c.is_connected.return_value = connected
    c.connect.return_value = c
    c.disconnect.return_value = None
    c.execute.return_value = CommandResult(command="true", stdout="", stderr="", exit_code=0)
    return c


@pytest.fixture
def config():
    return ConnectionConfig(hostname="h", username="u")


@pytest.fixture
def patched_client():
    """patch SyncConnectionPool 内部使用的 SSHClient 构造路径。"""
    created: list[MagicMock] = []

    def factory(cfg):
        c = _client_mock()
        created.append(c)
        return c

    with patch("remote_cmd.core.sync_connection_pool.SSHClient", side_effect=factory):
        yield created


class TestSyncConnectionPool:
    def test_acquire_release(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=5)
        try:
            conn = pool.acquire()
            pool.release(conn)
            assert pool._free.qsize() == 1
            assert pool.get_metrics()["total_created"] == 1
        finally:
            pool.close_all()

    def test_max_connections_enforced(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=2)
        try:
            c1 = pool.acquire()
            c2 = pool.acquire()
            assert len(patched_client) == 2
            pool.release(c1)
            # 释放后空闲队列有连接，再获取应复用而非新建
            c3 = pool.acquire()
            assert c3 is c1
            pool.release(c2)
            pool.release(c3)
        finally:
            pool.close_all()

    def test_max_lifetime_expiry(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=2, max_lifetime=0)
        try:
            conn = pool.acquire()
            pool.release(conn)
            # max_lifetime=0 使下次获取时一定过期
            conn2 = pool.acquire()
            assert conn is not conn2
        finally:
            pool.close_all()

    def test_acquire_context(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=3)
        try:
            with pool.acquire_context() as conn:
                assert conn is not None
                assert conn.is_connected()
            assert pool._free.qsize() == 1
        finally:
            pool.close_all()

    def test_metrics_keys(self, config, patched_client):
        pool = SyncConnectionPool(config=config)
        try:
            pool.acquire()
            m = pool.get_metrics()
            for k in ("active", "idle", "total_connections", "total_created", "max_connections"):
                assert k in m
        finally:
            pool.close_all()

    def test_close_all_clears(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=3)
        pool.acquire()
        pool.close_all()
        assert pool.get_metrics()["total_connections"] == 0

    def test_reuse_free_connection(self, config, patched_client):
        """测试：空闲队列中有健康连接时直接复用，不新建"""
        pool = SyncConnectionPool(config=config, max_connections=3)
        try:
            c1 = pool.acquire()
            pool.release(c1)
            c2 = pool.acquire()
            assert c1 is c2
            assert len(patched_client) == 1
        finally:
            pool.close_all()

    def test_create_failure_releases_semaphore(self, config):
        """测试：连接创建失败时释放信号量，且不残留连接"""
        pool = SyncConnectionPool(config=config, max_connections=2)

        def failing_factory(cfg):
            c = _client_mock()
            c.connect.side_effect = SSHConnectionError("authentication failed")
            return c

        with patch.object(pool, "_client_factory", side_effect=failing_factory):
            with pytest.raises(SSHConnectionError):
                pool.acquire()
            assert pool.get_metrics()["failed"] == 1
            # 信号量已释放：后续可再获取
            pool.close_all()

    def test_concurrent_acquire_release(self, config, patched_client):
        """并发获取/归还：连接数不超过上限，且全部线程正常完成。"""
        pool = SyncConnectionPool(config=config, max_connections=4)
        n_threads = 20
        start = threading.Barrier(n_threads)
        errors: list[BaseException] = []

        def worker():
            try:
                start.wait(timeout=5)
                for _ in range(5):
                    with pool.acquire_context() as conn:
                        assert conn is not None and conn.is_connected()
                        time.sleep(0.001)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        pool.close_all()

        assert not errors
        m = pool.get_metrics()
        assert m["total_connections"] <= 4
        assert m["total_created"] <= 4
        assert m["idle"] == 0

    def test_capacity_blocks_until_release(self, config, patched_client):
        """容量限制：占满后 acquire 阻塞，直到有连接归还。"""
        pool = SyncConnectionPool(config=config, max_connections=1)
        c1 = pool.acquire()
        barrier = threading.Barrier(2)
        acquired_event = threading.Event()
        acquired_box: dict[str, bool] = {"reused": False}
        result: list[BaseException] = []

        def worker():
            try:
                barrier.wait(timeout=5)  # 确保 acquire 在释放之前发起
                conn = pool.acquire()
                acquired_box["reused"] = conn is c1
                acquired_event.set()
            except BaseException as e:  # noqa: BLE001
                result.append(e)

        t = threading.Thread(target=worker)
        t.start()
        barrier.wait(timeout=5)
        time.sleep(0.05)
        assert not acquired_event.is_set()  # 仍被信号量阻塞，未获取到连接

        pool.release(c1)
        t.join(timeout=5)
        assert not result
        assert acquired_event.is_set()
        assert acquired_box["reused"]
        pool.close_all()

    def test_capacity_limit_no_overshoot(self, config, patched_client):
        """并发下创建的连接总数严格不超过 max_connections。"""
        pool = SyncConnectionPool(config=config, max_connections=3)
        n_threads = 10
        start_ready = threading.Barrier(n_threads)
        errors: list[BaseException] = []

        def worker():
            try:
                start_ready.wait(timeout=5)
                with pool.acquire_context():
                    time.sleep(0.02)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        pool.close_all()
        assert not errors
        assert len(patched_client) <= 3

    def test_lifecycle_full(self, config, patched_client):
        """生命周期：创建->获取->归还->关闭。"""
        pool = SyncConnectionPool(config=config, max_connections=2)
        conn = pool.acquire()
        assert pool.get_metrics()["total_connections"] == 1
        assert conn.is_connected()
        assert conn.connect.called

        pool.release(conn)
        assert pool._free.qsize() == 1
        assert pool._total_released == 1

        # 再次获取复用同一连接
        conn2 = pool.acquire()
        assert conn2 is conn
        pool.release(conn2)

        created = list(patched_client)
        pool.close_all()
        assert pool.get_metrics()["total_connections"] == 0
        for c in created:
            c.disconnect.assert_called()

    def test_exception_inside_context_releases(self, config, patched_client):
        """上下文内部抛异常，连接仍归还/释放，不泄漏。"""
        pool = SyncConnectionPool(config=config, max_connections=2)
        try:
            with pytest.raises(RuntimeError), pool.acquire_context():
                raise RuntimeError("boom")
            c2 = pool.acquire()
            assert c2 is not None
            pool.release(c2)
        finally:
            pool.close_all()
        m = pool.get_metrics()
        assert m["total_connections"] == 0

    def test_reuse_liveness_probe(self, config, patched_client):
        """空闲连接触发探活：execute 成功则复用，失败则关闭并重建。"""
        pool = SyncConnectionPool(config=config, max_connections=2, idle_timeout=300)
        conn = pool.acquire()
        pool.release(conn)

        # 强行使 conn 空闲超时
        pool._meta[id(conn)]["last_used"] = time.time() - 1000

        conn.execute.return_value = CommandResult(command="true", stdout="", stderr="", exit_code=0)
        conn2 = pool.acquire()
        assert conn2 is conn  # 探活成功，复用
        pool.close_all()

    def test_liveness_probe_failure_recreates(self, config, patched_client):
        """探活失败：关闭旧连接并新建，reconnects 计数递增。"""
        pool = SyncConnectionPool(config=config, max_connections=2, idle_timeout=300)
        conn = pool.acquire()
        pool.release(conn)
        pool._meta[id(conn)]["last_used"] = time.time() - 1000

        def bad_execute(*args, **kwargs):
            raise SSHConnectionError("down")

        conn.execute.side_effect = bad_execute
        conn2 = pool.acquire()
        assert conn2 is not conn
        assert pool.get_metrics()["reconnects"] == 1
        assert pool.get_metrics()["failed"] == 0
        pool.close_all()

    def test_timeout_expired_conn_not_reused(self, config, patched_client):
        """获取时 active-ish连接超 max_lifetime 应被关闭并重建。"""
        pool = SyncConnectionPool(config=config, max_connections=2, max_lifetime=1)
        conn = pool.acquire()
        pool.release(conn)
        assert pool._free.qsize() == 1

        pool._meta[id(conn)]["created_at"] = time.time() - 100
        conn2 = pool.acquire()
        assert conn2 is not conn
        assert conn.disconnect.called
        pool.close_all()

    def test_idle_conn_never_split_negative_active(self, config, patched_client):
        """同一连接多次归还，metric 不被复用埋没（回归保护）。"""
        pool = SyncConnectionPool(config=config, max_connections=1)
        conn = pool.acquire()
        pool.release(conn)
        pool.acquire()
        pool.release(conn)
        m = pool.get_metrics()
        # total_connections 反映实际创建数
        assert m["total_connections"] == 1

    def test_active_metric_nonnegative_on_reuse(self, config, patched_client):
        """回归：连接复用多次后 active 不得为负，最终归还后回到 0。"""
        pool = SyncConnectionPool(config=config, max_connections=1)
        conn = pool.acquire()
        # 持有中：active 应为 1
        assert pool.get_metrics()["active"] == 1
        pool.release(conn)
        # 归还后：active 回到 0
        assert pool.get_metrics()["active"] == 0

        # 同一连接反复复用 5 次
        for _ in range(5):
            c = pool.acquire()
            assert pool.get_metrics()["active"] == 1
            pool.release(c)
            assert pool.get_metrics()["active"] == 0

        # 全程 active 不得为负
        assert pool.get_metrics()["active"] == 0
        assert pool.get_metrics()["total_connections"] == 1
        pool.close_all()

    def test_monitor_start_stop(self, config, patched_client):
        """监控线程启动、周期清理、停止退出。"""
        pool = SyncConnectionPool(config=config, health_check_interval=0.01)
        pool.start_monitor()
        thread = pool._monitor_thread
        assert thread is not None and thread.is_alive()

        with patch.object(pool, "_cleanup_expired") as cleanup:
            time.sleep(0.05)
            assert cleanup.call_count >= 1

        pool.stop_monitor()
        assert not thread.is_alive()

    def test_monitor_start_idempotent(self, config, patched_client):
        pool = SyncConnectionPool(config=config)
        pool.start_monitor()
        first = pool._monitor_thread
        pool.start_monitor()
        assert pool._monitor_thread is first
        pool.stop_monitor()

    def test_cleanup_expired_removes_and_keeps(self, config, patched_client):
        """清理：过期/断开连接被关闭，健康的保留。"""
        pool = SyncConnectionPool(config=config, max_connections=10, idle_timeout=100)
        healthy = pool.acquire()
        stale = pool.acquire()
        dead = pool.acquire()
        pool.release(healthy)
        pool.release(stale)
        pool.release(dead)

        pool._meta[id(stale)]["last_used"] = time.time() - 10_000
        dead.is_connected.return_value = False

        pool._cleanup_expired()

        free_conns = list(pool._free.queue)
        assert healthy in free_conns
        assert stale not in free_conns
        assert dead not in free_conns
        assert stale.disconnect.called
        assert dead.disconnect.called
        assert healthy.disconnect.call_count == 0
        pool.close_all()

    def test_release_expired_conn_closed(self, config, patched_client):
        """释放时已超过生命周期的连接直接关闭，不进入空闲队列。"""
        pool = SyncConnectionPool(config=config, max_connections=2, max_lifetime=1)
        conn = pool.acquire()
        pool._meta[id(conn)]["created_at"] = time.time() - 100
        pool.release(conn)
        assert pool._free.qsize() == 0
        assert conn.disconnect.called
        assert pool.get_metrics()["total_connections"] == 0
        pool.close_all()

    def test_metrics_snapshot(self, config, patched_client):
        """指标快照含关键字段。"""
        pool = SyncConnectionPool(
            config=config, max_connections=7, max_lifetime=100, idle_timeout=10
        )
        try:
            pool.acquire()
            m = pool.get_metrics()
            for key in (
                "active",
                "idle",
                "total_connections",
                "total_created",
                "reconnects",
                "failed",
                "max_connections",
                "max_lifetime",
                "idle_timeout",
            ):
                assert key in m
            assert m["max_connections"] == 7
            assert m["max_lifetime"] == 100
            assert m["idle_timeout"] == 10
        finally:
            pool.close_all()

    def test_release_none_is_noop(self, config, patched_client):
        pool = SyncConnectionPool(config=config, max_connections=2)
        try:
            pool.release(None)
            assert pool._total_released == 0
        finally:
            pool.close_all()

    def test_disconnected_release_releases_slot(self, config, patched_client):
        """归还断开连接：关闭并释放信号量槽位。"""
        pool = SyncConnectionPool(config=config, max_connections=1)
        conn = pool.acquire()
        conn.is_connected.return_value = False
        pool.release(conn)
        assert conn.disconnect.called
        # 槽位已释放，可再次获取
        conn2 = pool.acquire()
        assert conn2 is not None
        pool.close_all()
