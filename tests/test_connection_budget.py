"""ConnectionBudget（v2.6 P2.5）与池/执行器集成测试。

覆盖：
- 构造校验、同步/异步 acquire/release、指标、超时、跨内核共享同一上限
- 连接池集成：建连占用 / 关闭释放 / 复用不重复占用 / close_all 幂等
- 执行器集成：直连路径占用并释放、内部池路径、worker 异常兜底不死锁
- 重试分类：BudgetTimeoutError 为瞬态（可重试）
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_cmd.core.async_connection_pool import AsyncConnectionPool
from remote_cmd.core.budget import ConnectionBudget
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.core.sync_connection_pool import SyncConnectionPool
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.batch_executor import BatchExecutor
from remote_cmd.service.retry_policy import is_retryable
from remote_cmd.utils.exceptions import BudgetTimeoutError, ValidationError


def make_mock_service(hosts):
    service = MagicMock()
    host_dict = {h.name: h for h in hosts}

    def _resolve(name):
        if name in host_dict:
            return host_dict[name]
        raise KeyError(name)

    service.resolve_host = _resolve
    return service


class _FakeSyncClient:
    def __init__(self, config):
        self.config = config
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def execute(self, command, timeout=None, environment=None):  # noqa: ARG002
        return CommandResult(command=command, stdout="", stderr="", exit_code=0)


class _FakeAsyncClient:
    def __init__(self, config):
        self.config = config
        self.connected = False

    async def connect(self):
        self.connected = True
        return self

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def execute(self, command, timeout=None, environment=None):  # noqa: ARG002
        return CommandResult(command=command, stdout="", stderr="", exit_code=0)


def _config() -> ConnectionConfig:
    return ConnectionConfig(hostname="h", username="u")


# ============================================================================
# 预算本体
# ============================================================================


class TestConnectionBudgetCore:
    @pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "10", None])
    def test_invalid_max_connections_raises(self, bad):
        with pytest.raises(ValidationError, match="max_connections"):
            ConnectionBudget(bad)

    @pytest.mark.parametrize("bad", [0, -1, True, 0.0])
    def test_invalid_timeout_raises(self, bad):
        with pytest.raises(ValidationError, match="acquire_timeout"):
            ConnectionBudget(1, acquire_timeout=bad)

    def test_sync_acquire_release_metrics(self):
        budget = ConnectionBudget(2)
        budget.acquire()
        metrics = budget.get_metrics()
        assert metrics["in_use"] == 1
        assert metrics["available"] == 1
        budget.release()
        metrics = budget.get_metrics()
        assert metrics["in_use"] == 0
        assert metrics["total_acquired"] == 1
        assert metrics["total_released"] == 1

    def test_context_manager(self):
        budget = ConnectionBudget(1)
        with budget.acquire_context():
            assert budget.get_metrics()["in_use"] == 1
        assert budget.get_metrics()["in_use"] == 0

    def test_sync_timeout_raises_and_counts(self):
        budget = ConnectionBudget(1, acquire_timeout=0.05)
        budget.acquire()
        with pytest.raises(BudgetTimeoutError, match="exhausted"):
            budget.acquire()
        assert budget.get_metrics()["total_timeouts"] == 1
        budget.release()
        assert budget.get_metrics()["in_use"] == 0

    def test_over_release_raises(self):
        budget = ConnectionBudget(1)
        with pytest.raises(ValueError):
            budget.release()

    @pytest.mark.asyncio
    async def test_async_acquire_release_metrics(self):
        budget = ConnectionBudget(2)
        await budget.acquire_async()
        assert budget.get_metrics()["in_use"] == 1
        async with budget.acquire_context_async():
            assert budget.get_metrics()["in_use"] == 2
        assert budget.get_metrics()["in_use"] == 1
        await budget.release_async()
        assert budget.get_metrics()["in_use"] == 0

    @pytest.mark.asyncio
    async def test_async_timeout_raises_and_counts(self):
        budget = ConnectionBudget(1, acquire_timeout=0.05)
        await budget.acquire_async()
        with pytest.raises(BudgetTimeoutError):
            await budget.acquire_async()
        assert budget.get_metrics()["total_timeouts"] == 1
        await budget.release_async()

    @pytest.mark.asyncio
    async def test_sync_and_async_share_single_cap(self):
        """同一实例：同步占满后，异步侧也拿不到槽位（全局上限共享）。"""
        budget = ConnectionBudget(1, acquire_timeout=0.05)
        budget.acquire()
        with pytest.raises(BudgetTimeoutError):
            await budget.acquire_async()
        budget.release()
        await budget.acquire_async()  # 释放后异步侧可获取
        await budget.release_async()
        assert budget.get_metrics()["in_use"] == 0

    def test_budget_timeout_is_retryable(self):
        assert is_retryable(BudgetTimeoutError("busy")) is True


class TestConnectionBudgetAsyncQueue:
    """v2.7：真异步等待队列（非轮询）的唤醒与取消语义。"""

    @pytest.mark.asyncio
    async def test_async_waiter_woken_by_release_from_other_thread(self):
        """释放发生在工作线程时，事件循环中的等待者被精准唤醒。"""
        import threading

        budget = ConnectionBudget(1, acquire_timeout=2.0)
        budget.acquire()  # 占满
        acquired = asyncio.Event()

        async def waiter():
            await budget.acquire_async()
            acquired.set()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert not task.done()

        t = threading.Thread(target=budget.release)
        t.start()
        try:
            await asyncio.wait_for(acquired.wait(), timeout=1.5)
        finally:
            t.join(timeout=1.0)

        assert task.done() and not task.cancelled()
        assert budget.get_metrics()["in_use"] == 1  # waiter 持有槽位
        budget.release()
        assert budget.get_metrics()["in_use"] == 0

    @pytest.mark.asyncio
    async def test_sync_waiter_woken_by_release(self):
        """同步等待者（独立线程）被释放唤醒，不依赖轮询。"""
        import threading

        budget = ConnectionBudget(1, acquire_timeout=2.0)
        budget.acquire()
        result: dict[str, bool] = {}

        def sync_waiter():
            try:
                budget.acquire()
                result["ok"] = True
                budget.release()
            except BudgetTimeoutError:
                result["ok"] = False

        t = threading.Thread(target=sync_waiter)
        t.start()
        await asyncio.sleep(0.05)
        assert t.is_alive()
        budget.release()  # 唤醒同步等待者
        t.join(timeout=1.5)
        assert result.get("ok") is True
        assert budget.get_metrics()["in_use"] == 0

    @pytest.mark.asyncio
    async def test_cancelled_waiter_is_removed_without_side_effects(self):
        """取消的异步等待者从队列移除，且不消费容量。"""
        budget = ConnectionBudget(1, acquire_timeout=5.0)
        budget.acquire()
        task = asyncio.create_task(budget.acquire_async())
        await asyncio.sleep(0.05)  # 确保已注册等待
        assert len(budget._async_waiters) == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(budget._async_waiters) == 0  # 无残留等待者

        budget.release()
        await asyncio.wait_for(budget.acquire_async(), timeout=1.0)
        assert budget.get_metrics()["in_use"] == 1
        await budget.release_async()
        assert budget.get_metrics()["in_use"] == 0


# ============================================================================
# 连接池集成
# ============================================================================


class TestSyncPoolBudget:
    def test_connection_creation_acquires_and_close_releases(self):
        budget = ConnectionBudget(2)
        pool = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeSyncClient,
            connection_budget=budget,
        )

        conn = pool.acquire()
        assert budget.get_metrics()["in_use"] == 1
        pool.release(conn)
        # 归还空闲不释放预算（连接仍存活）
        assert budget.get_metrics()["in_use"] == 1
        pool.close_all()
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_acquired"] == 1
        assert budget.get_metrics()["total_released"] == 1

    def test_reuse_does_not_double_acquire(self):
        budget = ConnectionBudget(1)
        pool = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeSyncClient,
            connection_budget=budget,
        )

        for _ in range(3):
            conn = pool.acquire()
            pool.release(conn)
        assert budget.get_metrics()["total_acquired"] == 1
        pool.close_all()
        assert budget.get_metrics()["in_use"] == 0

    def test_two_pools_share_global_cap(self):
        budget = ConnectionBudget(1, acquire_timeout=0.05)
        pool_a = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeSyncClient,
            connection_budget=budget,
        )
        pool_b = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeSyncClient,
            connection_budget=budget,
        )

        pool_a.acquire()  # 占满全局预算（连接存活）
        with pytest.raises(BudgetTimeoutError):
            pool_b.acquire()
        pool_a.close_all()
        conn_b = pool_b.acquire()  # 释放后可获取
        assert conn_b.is_connected()
        pool_b.close_all()
        assert budget.get_metrics()["in_use"] == 0

    def test_close_all_twice_no_over_release(self):
        budget = ConnectionBudget(1)
        pool = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeSyncClient,
            connection_budget=budget,
        )
        pool.acquire()
        pool.close_all()
        pool.close_all()  # 幂等：不得触发 BoundedSemaphore 超额释放
        assert budget.get_metrics()["in_use"] == 0

    def test_connect_failure_releases_budget(self):
        budget = ConnectionBudget(1, acquire_timeout=0.05)

        class FailingClient(_FakeSyncClient):
            def connect(self):
                raise OSError("connect refused")

        pool = SyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=FailingClient,
            connection_budget=budget,
        )
        with pytest.raises(OSError):
            pool.acquire()
        # 建连失败预算已归还，还能再次 acquire（此处仍失败但拿到槽位）
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_released"] == 1


class TestAsyncPoolBudget:
    @pytest.mark.asyncio
    async def test_connection_creation_acquires_and_close_releases(self):
        budget = ConnectionBudget(2)
        pool = AsyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeAsyncClient,
            connection_budget=budget,
        )

        conn = await pool.acquire()
        assert budget.get_metrics()["in_use"] == 1
        await pool.release(conn)
        assert budget.get_metrics()["in_use"] == 1  # 空闲连接仍占预算
        await pool.close_all()
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_acquired"] == 1

    @pytest.mark.asyncio
    async def test_two_pools_share_global_cap_async(self):
        budget = ConnectionBudget(1, acquire_timeout=0.05)
        pool_a = AsyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeAsyncClient,
            connection_budget=budget,
        )
        pool_b = AsyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeAsyncClient,
            connection_budget=budget,
        )

        await pool_a.acquire()
        with pytest.raises(BudgetTimeoutError):
            await pool_b.acquire()
        await pool_a.close_all()
        conn_b = await pool_b.acquire()
        assert conn_b.is_connected()
        await pool_b.close_all()
        assert budget.get_metrics()["in_use"] == 0

    @pytest.mark.asyncio
    async def test_close_all_twice_no_over_release_async(self):
        budget = ConnectionBudget(1)
        pool = AsyncConnectionPool(
            _config(),
            max_connections=1,
            client_factory=_FakeAsyncClient,
            connection_budget=budget,
        )
        await pool.acquire()
        await pool.close_all()
        await pool.close_all()
        assert budget.get_metrics()["in_use"] == 0


# ============================================================================
# 执行器集成
# ============================================================================


class TestExecutorBudget:
    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_sync_direct_path_uses_and_releases_budget(self, mock_cls):
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        instance = MagicMock()
        instance.execute.return_value = CommandResult("cmd", "ok", "", 0)
        mock_cls.return_value = instance

        budget = ConnectionBudget(1, acquire_timeout=1.0)
        ex = BatchExecutor(
            host_service=make_mock_service([host]),
            max_concurrency=1,
            connection_budget=budget,
        )
        result = ex.execute(["srv1"], "cmd")
        assert result.success == 1
        instance.connect.assert_called_once()
        instance.disconnect.assert_called_once()
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_acquired"] == 1

    @pytest.mark.asyncio
    async def test_async_direct_path_uses_and_releases_budget(self):
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        instance = MagicMock()
        instance.connect = AsyncMock(return_value=instance)
        instance.disconnect = AsyncMock()
        instance.execute = AsyncMock(return_value=CommandResult("cmd", "ok", "", 0))
        instance.is_connected.return_value = True
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)

        budget = ConnectionBudget(1, acquire_timeout=1.0)
        with patch(
            "remote_cmd.service.async_batch_executor.AsyncSSHClient",
            MagicMock(return_value=instance),
        ):
            ex = AsyncBatchExecutor(
                host_service=make_mock_service([host]),
                max_concurrency=1,
                connection_budget=budget,
            )
            result = await ex.execute(["srv1"], "cmd")
        assert result.success == 1
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_acquired"] == 1

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_sync_internal_pools_share_budget(self, mock_cls):
        """多主机（走内部池）共享同一预算，批结束后全部释放。"""
        hosts = [Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(3)]
        mock_cls.return_value = MagicMock(
            **{
                "execute.return_value": CommandResult("cmd", "ok", "", 0),
                "is_connected.return_value": True,
            }
        )
        budget = ConnectionBudget(1, acquire_timeout=5.0)
        ex = BatchExecutor(
            host_service=make_mock_service(hosts),
            max_concurrency=3,
            connection_budget=budget,
        )
        result = ex.execute([h.name for h in hosts], "cmd")
        assert result.success == 3
        # 3 台主机各建 1 条连接（串行受预算约束），批结束全部释放
        assert budget.get_metrics()["in_use"] == 0
        assert budget.get_metrics()["total_acquired"] == 3

    @pytest.mark.asyncio
    async def test_async_worker_survives_internal_pool_error(self):
        """回归：内部池构造异常不得使 worker 退出导致 queue.join 死锁。"""
        hosts = [Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(3)]
        ex = AsyncBatchExecutor(
            host_service=make_mock_service(hosts),
            max_concurrency=2,
            command_timeout=1,
        )
        with patch(
            "remote_cmd.service.async_batch_executor.AsyncConnectionPool",
            side_effect=RuntimeError("pool construction boom"),
        ):
            result = await ex.execute([h.name for h in hosts], "uptime", retry_count=0)
        assert result.total == 3
        assert result.failed == 3
        assert all("internal error" in (r.error or "") for r in result.results.values())
