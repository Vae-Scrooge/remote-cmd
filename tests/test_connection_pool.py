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

    async def connect(self):
        self._connected = True
        return self

    async def disconnect(self):
        self._connected = False

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
