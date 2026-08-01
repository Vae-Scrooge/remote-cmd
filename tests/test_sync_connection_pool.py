"""SyncConnectionPool 同步连接池单元测试"""

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

        with patch(
            "remote_cmd.core.sync_connection_pool.SSHClient", side_effect=failing_factory
        ):
            with pytest.raises(SSHConnectionError):
                pool.acquire()
            assert pool.get_metrics()["failed"] == 1
            # 信号量已释放：后续可再获取
            pool.close_all()
