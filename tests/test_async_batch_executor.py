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
from remote_cmd.service.batch_executor import BatchExecutor

# ============================================================================
# AsyncConnectionPool 测试
# ============================================================================


def _client_mock(connected=True):
    c = MagicMock(spec=AsyncSSHClient)
    c.is_connected.return_value = connected
    c.connect = AsyncMock(return_value=c)
    c.disconnect = AsyncMock()
    c.execute = AsyncMock(return_value=CommandResult(command="true", stdout="", stderr="", exit_code=0))
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


# ============================================================================
# AsyncBatchExecutor 测试
# ============================================================================


def make_mock_service(hosts: list[Host]):
    service = MagicMock()
    host_dict = {h.name: h for h in hosts}
    service._resolve_host = lambda name: host_dict[name] if name in host_dict else (_ for _ in ()).throw(KeyError(name))
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
        with pytest.raises(ValueError, match="主机列表不能为空"):
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
        assert "不存在" in (result.results["ghost"].error or "")

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
        with pytest.raises(ValueError, match="主机列表不能为空"):
            ex.execute([], "uptime")
