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
from remote_cmd.utils.exceptions import CredentialError, SSHAuthenticationError, SSHTimeoutError

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
    async def test_acquire_after_close_raises(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=2)
        await pool.close_all()
        with pytest.raises(RuntimeError, match="connection pool is closed"):
            await pool.acquire()

    @pytest.mark.asyncio
    async def test_release_after_close_no_residue(self, config, patched_client):
        pool = AsyncConnectionPool(config=config, max_connections=2)
        conn = await pool.acquire()
        await pool.close_all()
        await pool.release(conn)
        assert pool._free.qsize() == 0
        assert pool.get_metrics()["total_connections"] == 0
        assert pool._total_released == 1

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

            pool._meta[id(c1)].last_used = _time.time() - pool._idle_timeout - 1
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
            pool._meta[id(c1)].created_at = 0
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
        pool._cleanup_expired = AsyncMock(side_effect=[RuntimeError("boom"), None, None, None])
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

        # 池必须在 patch 生效后构造：client_factory 默认在 __init__ 时
        # 从模块命名空间解析（与 SyncConnectionPool 行为一致）
        with (
            patch(
                "remote_cmd.core.async_connection_pool.AsyncSSHClient",
                side_effect=failing_factory,
            ),
        ):
            pool = AsyncConnectionPool(config=config, max_connections=1)
            try:
                with pytest.raises(OSError):
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
            pool._meta[id(conn)].created_at = 0
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
        pool = AsyncConnectionPool(config=config, health_check_interval=0.01)
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
        pool = AsyncConnectionPool(config=config, max_connections=3, max_lifetime=1, idle_timeout=1)
        try:
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            await pool.release(c1)
            await pool.release(c2)
            pool._meta[id(c1)].created_at = 0  # 生命周期过期
            pool._meta[id(c2)].last_used = 0  # 空闲超时
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
    """patch AsyncBatchExecutor 使用的 AsyncSSHClient（类层级）。

    返回的 mock 同时支持两种执行路径：
    - 直连路径：``async with AsyncSSHClient(config) as client``
    - 连接池路径：``pool.acquire_context()`` 借出的连接
      （connect / execute / disconnect / is_connected 均可用）
    """
    instance = MagicMock()
    instance.connect = AsyncMock(return_value=instance)
    instance.disconnect = AsyncMock()
    instance.execute = AsyncMock(return_value=CommandResult("uptime", "OK", "", 0))
    instance.is_connected.return_value = True
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    cls = MagicMock(return_value=instance)
    with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", cls):
        yield cls, instance


class TestAsyncBatchExecutor:
    @pytest.mark.asyncio
    async def test_empty_raises(self):
        ex = AsyncBatchExecutor(host_service=MagicMock())
        with pytest.raises(ValueError, match="host_names must not be empty"):
            await ex.execute([], "uptime")

    def test_invalid_max_concurrency_raises(self):
        with pytest.raises(ValueError, match="max_concurrency must be >= 1"):
            AsyncBatchExecutor(host_service=MagicMock(), max_concurrency=0)

    def test_invalid_command_timeout_raises(self):
        with pytest.raises(ValueError, match="command_timeout must be > 0"):
            AsyncBatchExecutor(host_service=MagicMock(), command_timeout=0)

    @pytest.mark.asyncio
    async def test_invalid_retry_params_raise(self):
        ex = AsyncBatchExecutor(host_service=MagicMock())
        with pytest.raises(ValueError, match="retry_count must be >= 0"):
            await ex.execute(["srv1"], "uptime", retry_count=-1)
        with pytest.raises(ValueError, match="retry_delay must be >= 0"):
            await ex.execute(["srv1"], "uptime", retry_delay=-0.5)

    @pytest.mark.asyncio
    async def test_duplicate_host_deduped(self, mock_async_client_class):
        """测试：重复主机名去重，只执行一次且统计正确"""
        cm, instance = mock_async_client_class
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1", "srv1", "srv1"], "uptime")
        assert result.total == 1
        assert result.success == 1
        assert result.failed == 0
        assert instance.execute.await_count == 1

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


# ============================================================================
# v2.1：重试分类 + 连接池集成（内部池生命周期 / 外部池所有权）
# ============================================================================


class TestAsyncBatchExecutorRetryClassification:
    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self, mock_async_client_class):
        """认证失败是永久性错误：即便 retry_count>0 也只执行一次"""
        cm, instance = mock_async_client_class
        instance.execute = AsyncMock(side_effect=SSHAuthenticationError("authentication failed"))
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1"], "uptime", retry_count=3, retry_delay=0.01)
        assert result.success == 0
        assert instance.execute.await_count == 1
        assert "authentication failed" in (result.results["srv1"].error or "")

    @pytest.mark.asyncio
    async def test_credential_error_not_retried(self, mock_async_client_class):
        """凭据解析失败是永久性错误：不重试"""
        cm, instance = mock_async_client_class
        instance.execute = AsyncMock(side_effect=CredentialError("decrypt failed"))
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1"], "uptime", retry_count=3, retry_delay=0.01)
        assert result.success == 0
        assert instance.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_timeout_error_retried(self, mock_async_client_class):
        """SSHTimeoutError 是瞬态错误：保持重试"""
        cm, instance = mock_async_client_class
        instance.execute = AsyncMock(
            side_effect=[SSHTimeoutError("timeout"), CommandResult("uptime", "OK", "", 0)]
        )
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
        result = await ex.execute(["srv1"], "uptime", retry_count=2, retry_delay=0.01)
        assert result.success == 1
        assert instance.execute.await_count == 2


class TestAsyncBatchExecutorPoolIntegration:
    """AsyncConnectionPool 集成（v2.1）"""

    @pytest.mark.asyncio
    async def test_retry_reuses_pooled_connection(self):
        """重试通过池复用连接：3 次尝试只有 1 次 connect 握手"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        clients = []

        def factory(cfg):
            c = _client_mock()
            c.execute = AsyncMock(
                side_effect=[
                    OSError("connection reset"),
                    OSError("connection reset"),
                    CommandResult("uptime", "OK", "", 0),
                ]
            )
            clients.append(c)
            return c

        with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", side_effect=factory):
            ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
            result = await ex.execute(["srv1"], "uptime", retry_count=2, retry_delay=0.01)

        assert result.success == 1
        assert len(clients) == 1  # 池只创建了一个连接
        assert clients[0].connect.await_count == 1  # 只握手一次
        assert clients[0].execute.await_count == 3  # 三次尝试复用同一连接

    @pytest.mark.asyncio
    async def test_internal_pools_closed_after_batch(self):
        """内部池在批次结束后自动 close_all（连接被 disconnect）"""
        hosts = [Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(3)]
        clients = []

        def factory(cfg):
            c = _client_mock()
            clients.append(c)
            return c

        with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", side_effect=factory):
            ex = AsyncBatchExecutor(host_service=make_mock_service(hosts))
            result = await ex.execute([h.name for h in hosts], "uptime")

        assert result.success == 3
        assert len(clients) == 3
        for c in clients:
            c.disconnect.assert_awaited_once()  # close_all 关闭所有连接

    @pytest.mark.asyncio
    async def test_single_host_no_retry_no_pool(self):
        """单主机无重试不创建内部池（与同步 BatchExecutor 对齐）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        clients = []

        def factory(cfg):
            c = _client_mock()
            clients.append(c)
            return c

        with (
            patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", side_effect=factory),
            patch("remote_cmd.service.async_batch_executor.AsyncConnectionPool") as mock_pool_cls,
        ):
            ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
            result = await ex.execute(["srv1"], "uptime")

        assert result.success == 1
        mock_pool_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_pool_factory_never_closed(self):
        """外部 pool_factory：池由调用方持有，executor 绝不关闭"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        client = _client_mock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=None)
        external_pool = MagicMock()
        external_pool.acquire_context = MagicMock(return_value=ctx)
        external_pool.close_all = AsyncMock()

        factory_calls = []

        def factory(cfg):
            factory_calls.append(cfg)
            return external_pool

        # 单主机无重试：提供 factory 时仍使用池（外部注入即明确意图）
        ex = AsyncBatchExecutor(host_service=make_mock_service([host]), pool_factory=factory)
        result = await ex.execute(["srv1"], "uptime")

        assert result.success == 1
        assert len(factory_calls) == 1
        client.execute.assert_awaited_once()
        external_pool.acquire_context.assert_called()
        # 所有权契约：外部池绝不关闭
        external_pool.close_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_host_multi_batch_returns_error_result(self):
        """多主机批次中含未知主机：返回错误条目而非抛异常（与同步一致）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")

        def factory(cfg):
            return _client_mock()

        with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", side_effect=factory):
            ex = AsyncBatchExecutor(host_service=make_mock_service([host]))
            result = await ex.execute(["srv1", "ghost"], "uptime")

        assert result.total == 2
        assert result.success == 1
        assert result.failed == 1
        assert "not found" in (result.results["ghost"].error or "")


# ============================================================================
# v2.1 发布加固：close_all 与阻塞中 acquire 的竞态（异步池）
# ============================================================================


class TestAsyncConnectionPoolCloseRace:
    """close_all() 与阻塞在信号量上的 acquire() 的竞态守卫（异步池）"""

    @pytest.mark.asyncio
    async def test_acquire_blocked_during_close_raises(self, config, patched_client):
        """信号量等待期间池被关闭：阻塞的 acquire 必须抛 RuntimeError，
        而非从已关闭的池发放新连接

        竞态窗口（无守卫时）：
        1. acquire 通过 _closed 预检（False）→ 挂起在信号量等待上
        2. close_all() 置位 _closed
        3. 信号量被 release 唤醒 → acquire 继续执行 → _create_connection
           从已关闭的池发放游离连接
        """
        pool = AsyncConnectionPool(config=config, max_connections=1)
        c1 = await pool.acquire()  # 占用唯一槽位

        task = asyncio.create_task(pool.acquire())
        # 协作式调度下，数次 sleep(0) 足以保证 task 已通过 _closed 预检
        # 并挂起在信号量上（许可为 0，不存在其他可前进的路径）
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        assert pool._semaphore._value == 0

        # task 挂起期间关闭池，随后归还连接释放许可唤醒 task
        await pool.close_all()
        await pool.release(c1)

        with pytest.raises(RuntimeError, match="connection pool is closed"):
            await task
        # 已关闭的池不得再新建连接
        assert pool.get_metrics()["total_created"] == 1


class TestBatchExecutorUseAsyncInRunningLoop:
    """use_async=True 在运行中的事件循环内调用的错误提示（v2.1 发布加固）"""

    @pytest.mark.asyncio
    async def test_execute_inside_running_loop_raises_clear_error(self):
        """事件循环内调用 use_async=True 的同步 execute：抛出可操作的
        项目级 RuntimeError（而非 asyncio.run 的通用错误）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        ex = BatchExecutor(host_service=make_mock_service([host]), use_async=True)
        with pytest.raises(RuntimeError) as excinfo:
            ex.execute(["srv1"], "uptime")

        msg = str(excinfo.value)
        assert "use_async=True" in msg
        assert "cannot be used inside a running event loop" in msg
        assert "AsyncBatchExecutor.execute()" in msg

    @pytest.mark.asyncio
    async def test_sync_kernel_unaffected_inside_running_loop(self):
        """守卫仅针对委托路径：use_async=False 的同步内核在事件循环内
        行为不变（不经 _delegate_to_async，不受影响）"""
        from unittest.mock import MagicMock

        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        with patch("remote_cmd.service.batch_executor.SSHClient") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            ok = MagicMock()
            ok.success = True
            ok.exit_code = 0
            ok.stdout = "OK"
            ok.stderr = ""
            mock_instance.execute.return_value = ok

            ex = BatchExecutor(host_service=make_mock_service([host]), use_async=False)
            result = ex.execute(["srv1"], "uptime")
        assert result.success == 1
