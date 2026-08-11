"""AsyncConnectionPool 与 AsyncBatchExecutor 单元测试"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_cmd.core.async_connection_pool import AsyncConnectionPool
from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.batch_executor import BatchExecutor, BatchHostResult

# ============================================================================
# AsyncConnectionPool 测试
# ============================================================================


def _client_mock(connected=True):
    c = MagicMock(spec=AsyncSSHClient)
    c.is_connected.return_value = connected
    c.connect = AsyncMock(return_value=c)
    c.disconnect = AsyncMock()
    c.execute = AsyncMock(
        return_value=CommandResult(command="true", stdout="", stderr="", exit_code=0)
    )
    return c


@pytest.fixture
def config():
    return ConnectionConfig(hostname="h", username="u")


@pytest.fixture
def patched_client():
    """patch AsyncConnectionPool 内部使用的 AsyncSSHClient 构造路径。"""
    created: list[MagicMock] = []

    def factory(cfg):
        c = _client_mock()
        created.append(c)
        return c

    with patch("remote_cmd.core.async_connection_pool.AsyncSSHClient", side_effect=factory):
        yield created


class TestAsyncConnectionPool:
    @pytest.mark.asyncio
    async def test_acquire_release(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=5)
        try:
            conn = await pool.acquire()
            await pool.release(conn)
            assert pool._free.qsize() == 1
            assert pool.get_metrics()["total_created"] == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_max_connections_enforced(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            # 第三次应阻塞
            t = asyncio.ensure_future(pool.acquire())
            await asyncio.sleep(0.05)
            assert not t.done()
            await pool.release(c1)
            await asyncio.sleep(0.05)
            assert t.done()
            c3 = await t
            await pool.release(c2)
            await pool.release(c3)
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_max_lifetime_expiry(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=2, max_lifetime=0)
        try:
            conn = await pool.acquire()
            await pool.release(conn)
            # max_lifetime=0 使下次获取时一定过期
            conn2 = await pool.acquire()
            assert conn is not conn2
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_acquire_context(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=3)
        try:
            async with pool.acquire_context() as conn:
                assert conn is not None
                assert conn.is_connected()
            assert pool._free.qsize() == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_metrics_keys(self, config, patched_client):
        pool = AsyncConnectionPool(config=config)
        try:
            await pool.acquire()
            m = pool.get_metrics()
            for k in ("active", "idle", "total_connections", "total_created", "max_connections"):
                assert k in m
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_close_all_clears(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=3)
        await pool.acquire()
        await pool.close_all()
        assert pool.get_metrics()["total_connections"] == 0

    @pytest.mark.asyncio
    async def test_reuse_free_connection(self, config, patched_client):
        """测试：空闲队列中有健康连接时直接复用，不新建"""
        pool = AsyncConnectionPool(config=config, max_connections=3)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c2 = await pool.acquire()
            assert c1 is c2
            assert pool.get_metrics()["total_created"] == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_acquire_skips_unhealthy_free(self, config, patched_client):
        """测试：空闲连接探活失败时关闭并新建"""
        pool = AsyncConnectionPool(config=config, max_connections=3)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            # 让连接空闲超过 idle_timeout，强制触发探活路径
            import time as _time

            pool._meta[id(c1)]["last_used"] = _time.time() - pool._idle_timeout - 1
            # 让探活失败：execute 抛异常
            c1.execute = AsyncMock(side_effect=OSError("connection dead"))
            c2 = await pool.acquire()
            assert c1 is not c2
            assert pool.get_metrics()["reconnects"] >= 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_acquire_disconnected_free(self, config, patched_client):
        """测试：空闲连接已断开时关闭并新建"""
        pool = AsyncConnectionPool(config=config, max_connections=3)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            c1.is_connected.return_value = False
            c2 = await pool.acquire()
            assert c1 is not c2
            assert pool.get_metrics()["total_created"] == 2
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_release_disconnected_closes(self, config, patched_client):
        """测试：释放已断开的连接时关闭并释放信号量"""
        pool = AsyncConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            c1.is_connected.return_value = False
            await pool.release(c1)
            # 释放后槽位可用，可再获取
            c3 = await pool.acquire()
            assert c3 is not None
            await pool.release(c2)
            await pool.release(c3)
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_release_expired_meta(self, config, patched_client):
        """测试：释放生命周期过期的连接时关闭"""
        pool = AsyncConnectionPool(config=config, max_connections=2, max_lifetime=1)
        try:
            c1 = await pool.acquire()
            pool._meta[id(c1)]["created_at"] = 0
            await pool.release(c1)
            assert pool._free.qsize() == 0
            assert pool._free.empty()
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_release_none(self, config, patched_client):
        """测试：release(None) 安全返回"""
        pool = AsyncConnectionPool(config=config)
        try:
            await pool.release(None)
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_release_queue_full(self, config, patched_client):
        """测试：空闲队列满时关闭连接"""
        pool = AsyncConnectionPool(config=config, max_connections=2)
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            pool._free = asyncio.Queue(maxsize=1)  # 容量为 1
            await pool.release(c1)  # 占满队列
            await pool.release(c2)  # put_nowait 失败 → 关闭
            assert pool.get_metrics()["total_connections"] == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_check_connection_no_meta(self, config, patched_client):
        """测试：无元数据（非池内创建）的连接探活直接放行"""
        pool = AsyncConnectionPool(config=config)
        try:
            conn = _client_mock()
            assert await pool._check_connection(conn) is True
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_expired_no_meta(self, config, patched_client):
        """测试：清理无元数据的空闲连接"""
        pool = AsyncConnectionPool(config=config, max_connections=2)
        try:
            conn = await pool.acquire()
            pool._free.put_nowait(conn)
            pool._meta.pop(id(conn))  # 模拟元数据丢失
            await pool._cleanup_expired()
            assert pool._free.qsize() == 0
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_monitor_loop_handles_exception(self, config, patched_client):
        """测试：监控循环中异常被捕获并继续运行"""
        pool = AsyncConnectionPool(config=config, health_check_interval=0.01)
        pool._cleanup_expired = AsyncMock(
            side_effect=[RuntimeError("boom"), None, None, None]
        )
        pool._start_monitor()
        await asyncio.sleep(0.05)
        pool.stop_monitor()
        await asyncio.sleep(0)
        assert pool._monitor_task.done()

    @pytest.mark.asyncio
    async def test_create_connection_failure(self, config):
        """测试：创建连接失败时释放信号量并累计失败数"""
        def failing_factory(cfg):
            client = _client_mock()
            client.connect = AsyncMock(side_effect=OSError("auth failed"))
            return client

        pool = AsyncConnectionPool(config=config, max_connections=1)
        try:
            with patch(
                "remote_cmd.core.async_connection_pool.AsyncSSHClient",
                side_effect=failing_factory,
            ), pytest.raises(OSError):
                await pool.acquire()
            assert pool.get_metrics()["failed"] == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_check_connection_lifetime(self, config, patched_client):
        """测试：探活检查生命周期超时返回 False"""
        pool = AsyncConnectionPool(config=config, max_lifetime=1)
        try:
            conn = await pool.acquire()
            pool._meta[id(conn)]["created_at"] = 0
            assert await pool._check_connection(conn) is False
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_check_connection_probe_success(self, config, patched_client):
        """测试：探活命令成功返回 True"""
        pool = AsyncConnectionPool(config=config)
        try:
            conn = await pool.acquire()
            assert await pool._check_connection(conn) is True
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_monitor_loop_cancelled(self, config, patched_client):
        """测试：监控任务可被取消"""
        pool = AsyncConnectionPool(
            config=config, health_check_interval=0.01
        )
        pool._start_monitor()
        assert pool._monitor_task is not None
        await asyncio.sleep(0.05)
        pool.stop_monitor()
        await asyncio.sleep(0)  # 让取消完成
        assert pool._monitor_task.done()

    @pytest.mark.asyncio
    async def test_monitor_start_idempotent(self, config, patched_client):
        """测试：重复启动监控不重复创建任务"""
        pool = AsyncConnectionPool(config=config, health_check_interval=60)
        pool._start_monitor()
        first = pool._monitor_task
        pool._start_monitor()
        assert pool._monitor_task is first
        pool.stop_monitor()

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, config, patched_client):
        """测试：清理过期与空闲超时连接"""
        pool = AsyncConnectionPool(
            config=config, max_connections=3, max_lifetime=1, idle_timeout=1
        )
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            await pool.release(c1)
            await pool.release(c2)
            pool._meta[id(c1)]["created_at"] = 0  # 生命周期过期
            pool._meta[id(c2)]["last_used"] = 0  # 空闲超时
            await pool._cleanup_expired()
            assert pool._free.qsize() == 0
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_expired_keeps_healthy(self, config, patched_client):
        """测试：健康连接在清理后保留"""
        pool = AsyncConnectionPool(config=config, max_connections=3)
        try:
            c1 = await pool.acquire()
            await pool.release(c1)
            await pool._cleanup_expired()
            assert pool._free.qsize() == 1
        finally:
            await pool.close_all()

    @pytest.mark.asyncio
    async def test_async_context_manager(self, config, patched_client):
        """测试：async with pool 启动监控并在退出时关闭所有连接"""
        pool = AsyncConnectionPool(config=config, max_connections=2)
        async with pool:
            assert pool._monitor_task is not None
            await pool.acquire()
        await asyncio.sleep(0)  # 让取消完成
        assert pool._monitor_task is None or pool._monitor_task.done()
        assert pool.get_metrics()["total_connections"] == 0


# ============================================================================
# AsyncBatchExecutor 测试
# ============================================================================


def make_mock_service(hosts: list[Host]):
    service = MagicMock()
    host_dict = {h.name: h for h in hosts}

    def _resolve(name):
        if name in host_dict:
            return host_dict[name]
        raise KeyError(name)

    service.resolve_host = _resolve
    return service


@pytest.fixture
def mock_async_client_class():
    """patch AsyncBatchExecutor 使用的 AsyncSSHClient（类层级）。"""
    instance = MagicMock()
    instance.connect = AsyncMock(return_value=instance)
    instance.disconnect = AsyncMock()
    instance.execute = AsyncMock(return_value=CommandResult("uptime", "OK", "", 0))
    cm = MagicMock(return_value=instance)
    cm.__aenter__ = AsyncMock(return_value=instance)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", return_value=cm):
        yield cm, instance


class TestAsyncBatchExecutor:
    @pytest.mark.asyncio
    async def test_empty_raises(self):
        ex = AsyncBatchExecutor(host_service=MagicMock())
        with pytest.raises(ValueError, match="host_names must not be empty"):
            await ex.execute([], "uptime")

    @pytest.mark.asyncio
    async def test_single_host(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1"], "uptime")
        assert result.total == 1
        assert result.success == 1
        assert result.results["srv1"].success
        instance.execute.assert_awaited()

    @pytest.mark.asyncio
    async def test_multiple_hosts_concurrency(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        hosts = [Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(5)]
        ex = AsyncBatchExecutor(host_service=make_mock_service(hosts), max_concurrency=3)
        result = await ex.execute([h.name for h in hosts], "uptime")
        assert result.success == 5
        assert instance.execute.await_count == 5

    @pytest.mark.asyncio
    async def test_host_not_found(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        ex = AsyncBatchExecutor(host_service=make_mock_service([]))
        result = await ex.execute(["ghost"], "uptime")
        assert result.failed == 1
        assert "not found" in (result.results["ghost"].error or "")

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        # 第一次抛异常，第二次成功
        instance.execute = AsyncMock(
            side_effect=[Exception("conn reset"), CommandResult("uptime", "OK", "", 0)]
        )
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1"], "uptime", retry_count=1, retry_delay=0.01)
        assert result.success == 1
        assert instance.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_progress_callback_sync(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        events = []

        def cb(completed, total, name):
            events.append((completed, total, name))

        await ex.execute(["srv1"], "uptime", progress_callback=cb)
        assert events == [(1, 1, "srv1")]

    @pytest.mark.asyncio
    async def test_progress_callback_async(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        events = []

        async def cb(completed, total, name):
            events.append((completed, total, name))

        await ex.execute(["srv1"], "uptime", progress_callback=cb)
        assert events == [(1, 1, "srv1")]

    @pytest.mark.asyncio
    async def test_cancel_and_mark_interrupted(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        ex = AsyncBatchExecutor(host_service=MagicMock())

        async def never_done():
            await asyncio.Event().wait()

        tasks = [asyncio.create_task(never_done()) for _ in range(3)]
        results = {"srv1": BatchHostResult(host="srv1", success=True, command="uptime")}
        await ex._cancel_and_mark_interrupted(tasks, ["srv1", "srv2", "srv3"], results, "uptime")
        assert all(t.cancelled() for t in tasks)
        assert results["srv1"].success
        assert results["srv2"].error == "user interrupted"
        assert results["srv3"].error == "user interrupted"


# ============================================================================
# BatchExecutor use_async 委托路径
# ============================================================================


class TestBatchExecutorUseAsyncSwitch:
    def test_construct_with_use_async(self):
        ex = BatchExecutor(host_service=MagicMock(), use_async=True)
        assert ex._async_executor is not None
        assert isinstance(ex._async_executor, AsyncBatchExecutor)

    def test_construct_without_use_async(self):
        ex = BatchExecutor(host_service=MagicMock())
        assert ex._async_executor is None

    def test_execute_delegates_to_async(self, mock_async_client_class):
        cm, instance = mock_async_client_class
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = BatchExecutor(host_service=make_mock_service([host]), use_async=True)
        result = ex.execute(["srv1"], "uptime")
        assert result.success == 1
        instance.execute.assert_awaited()

    def test_execute_empty_raises_sync(self):
        ex = BatchExecutor(host_service=MagicMock(), use_async=True)
        with pytest.raises(ValueError, match="host_names must not be empty"):
            ex.execute([], "uptime")
