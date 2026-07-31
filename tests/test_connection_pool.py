"""ConnectionPool 连接池测试"""

import asyncio
import time
import uuid
from unittest.mock import patch

import pytest

from remote_cmd.core.async_client import ConnectionPool
from remote_cmd.core.ssh_client import ConnectionConfig


class MockAsyncClient:
    """模拟 AsyncSSHClient，不真实连接 SSH"""

    def __init__(self, config, loop=None):  # noqa: ARG002
        self.config = config
        self._connected = True
        self._created_at = time.time()
        self._last_used = time.time()
        self._connection_id = uuid.uuid4().hex
        self._sync = _SyncStub()

    async def connect(self):
        self._connected = True
        return self

    async def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected


class _SyncStub:
    """模拟同步 SSHClient 的健康状态"""

    def __init__(self):
        self._connected = True

    def is_connected(self):
        return self._connected


@pytest.fixture(autouse=True)
def mock_ssh():
    with patch("remote_cmd.core.async_client.AsyncSSHClient", MockAsyncClient):
        yield


@pytest.fixture
def config():
    return ConnectionConfig(hostname="test-host", username="test-user")


pytestmark = pytest.mark.asyncio(loop_scope="module")


class TestConnectionPool:
    """ConnectionPool 增强功能测试"""

    @pytest.mark.asyncio
    async def test_max_lifetime(self, config):
        pool = ConnectionPool(
            config=config,
            max_connections=5,
            max_lifetime=1,
            idle_timeout=300,
            health_check_interval=600,
        )
        try:
            conn = await pool.acquire()
            conn._created_at = 0
            await pool.release(conn)

            conn2 = await pool.acquire()
            assert conn is not conn2
            assert conn2._created_at > 0
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_acquire_release(self, config):
        pool = ConnectionPool(config=config, max_connections=5)
        try:
            conn = await pool.acquire()
            await pool.release(conn)
            assert pool._free.qsize() == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_max_connections(self, config):
        pool = ConnectionPool(config=config, max_connections=2)
        try:
            conn1 = await pool.acquire()
            conn2 = await pool.acquire()

            acquire_task = asyncio.ensure_future(pool.acquire())
            await asyncio.sleep(0.1)
            assert not acquire_task.done()

            await pool.release(conn1)
            await asyncio.sleep(0.1)
            assert acquire_task.done()
            conn3 = acquire_task.result()

            await pool.release(conn2)
            await pool.release(conn3)
        finally:
            pool.stop_monitor()

    async def test_get_metrics(self):
        pool = ConnectionPool(config=ConnectionConfig(hostname="h", username="u"))
        try:
            metrics = pool.get_metrics()
            expected_keys = (
                "active",
                "idle",
                "total_connections",
                "total_created",
                "max_connections",
            )
            for key in expected_keys:
                assert key in metrics
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_connection_id(self, config):
        pool = ConnectionPool(config=config)
        try:
            conn = await pool.acquire()
            assert conn._connection_id is not None
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_pool_metrics(self, config):
        pool = ConnectionPool(config=config)
        try:
            await pool.acquire()
            metrics = pool.get_metrics()
            assert metrics["active"] > 0
            assert metrics["total_created"] > 0
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_reuse_healthy_free_connection(self, config):
        """测试：空闲连接健康时直接复用"""
        pool = ConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c2 = await pool.acquire()
            assert c1 is c2
            assert pool.get_metrics()["total_created"] == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_acquire_reconnects_stale_sync(self, config):
        """测试：同步连接已断开时自动重连"""
        pool = ConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c1._sync._connected = False  # 同步连接断开
            c2 = await pool.acquire()
            assert c1 is c2
            assert pool.get_metrics()["reconnects"] >= 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_acquire_closes_unhealthy_free(self, config):
        """测试：空闲连接检查失败时关闭并新建"""
        pool = ConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c1._connected = False  # 异步层断开 → 检查失败
            c2 = await pool.acquire()
            assert c1 is not c2
            assert pool.get_metrics()["total_created"] == 2
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_release_idle_timeout_closes(self, config):
        """测试：空闲超时的连接释放时被关闭"""
        pool = ConnectionPool(config=config, idle_timeout=0)
        try:
            c1 = await pool.acquire()
            c1._last_used = 0  # 保证空闲时长超过 0
            await pool.release(c1)
            assert pool._free.qsize() == 0
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_release_disconnected_closes(self, config):
        """测试：释放已断开的连接时关闭并计数"""
        pool = ConnectionPool(config=config)
        try:
            c1 = await pool.acquire()
            c1._connected = False
            await pool.release(c1)
            assert pool.get_metrics()["total_connections"] == 0
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_acquire_blocking_waits_for_free(self, config):
        """测试：无空闲连接且达到上限时阻塞等待"""
        pool = ConnectionPool(config=config, max_connections=1)
        try:
            c1 = await pool.acquire()
            task = asyncio.ensure_future(pool.acquire())
            await asyncio.sleep(0.05)
            assert not task.done()
            await pool.release(c1)
            await asyncio.sleep(0.05)
            assert task.done()
            c2 = task.result()
            assert c2 is c1
            await pool.release(c2)
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_acquire_context_manager(self, config):
        """测试：acquire_context 获取/归还连接"""
        pool = ConnectionPool(config=config)
        try:
            async with pool.acquire_context() as conn:
                assert conn is not None
                assert conn.is_connected()
            assert pool._free.qsize() == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_monitor_start_stop(self, config):
        """测试：监控任务启动与停止"""
        pool = ConnectionPool(config=config, health_check_interval=0.01)
        pool._start_monitor()
        assert pool._monitor_task is not None
        await asyncio.sleep(0.05)
        pool.stop_monitor()
        await asyncio.sleep(0)  # 让取消完成
        assert pool._monitor_task.done()

    @pytest.mark.asyncio
    async def test_monitor_start_idempotent(self, config):
        """测试：重复启动监控不重复创建"""
        pool = ConnectionPool(config=config, health_check_interval=60)
        pool._start_monitor()
        first = pool._monitor_task
        pool._start_monitor()
        assert pool._monitor_task is first
        pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_cleanup_expired_connections(self, config):
        """测试：清理生命周期超时连接"""
        pool = ConnectionPool(config=config, max_connections=3, max_lifetime=1, idle_timeout=1)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c1._created_at = 0  # 生命周期过期
            await pool._cleanup_expired()
            assert pool._free.qsize() == 0
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_cleanup_keeps_healthy(self, config):
        """测试：健康连接在清理后保留"""
        pool = ConnectionPool(config=config, max_connections=3)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            await pool._cleanup_expired()
            assert pool._free.qsize() == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_pool_async_context_manager(self, config):
        """测试：async with pool 启动监控并在退出时断开所有连接"""
        pool = ConnectionPool(config=config, max_connections=2)
        async with pool:
            assert pool._monitor_task is not None
            conn = await pool.acquire()
        await asyncio.sleep(0)  # 让取消完成
        assert pool._monitor_task is None or pool._monitor_task.done()
        # __aexit__ 断开但不移除连接引用
        assert conn.is_connected() is False

    @pytest.mark.asyncio
    async def test_acquire_blocking_creates_new_after_unhealthy(self, config):
        """测试：阻塞等待拿到的空闲连接不健康时关闭并新建"""
        pool = ConnectionPool(config=config, max_connections=1)
        try:
            c1 = await pool.acquire()

            async def failing_connect():
                raise OSError("dead")

            task = asyncio.ensure_future(pool.acquire())
            await asyncio.sleep(0.05)
            assert not task.done()
            # 向空闲队列注入不健康连接
            c2 = MockAsyncClient(config)
            c2._connected = False
            c1.connect = failing_connect  # type: ignore[method-assign]
            pool._free.put_nowait(c2)
            await asyncio.sleep(0.05)
            assert task.done()
            c3 = task.result()
            assert c3 is not c2
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_check_connection_exception_counts_failed(self, config):
        """测试：重连异常累计失败数并返回 False"""
        pool = ConnectionPool(config=config)
        try:
            c1 = await pool.acquire()
            c1._sync._connected = False  # 触发重连路径

            async def failing_connect():
                raise OSError("reconnect failed")

            c1.connect = failing_connect  # type: ignore[method-assign]
            assert await pool._check_connection(c1) is False
            assert pool.get_metrics()["failed"] == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_release_queue_full(self, config):
        """测试：空闲队列满时关闭连接"""
        pool = ConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            pool._free = asyncio.Queue(maxsize=1)
            await pool.release(c1)
            await pool.release(c2)  # put 失败 → 关闭
            assert pool.get_metrics()["total_connections"] == 1
        finally:
            pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_monitor_loop_handles_exception(self, config):
        """测试：监控循环异常被捕获并继续"""
        pool = ConnectionPool(config=config, health_check_interval=0.01)

        async def failing_cleanup():
            raise RuntimeError("boom")

        pool._cleanup_expired = failing_cleanup  # type: ignore[method-assign]
        pool._start_monitor()
        await asyncio.sleep(0.05)
        pool.stop_monitor()
        await asyncio.sleep(0)
        assert pool._monitor_task.done()
