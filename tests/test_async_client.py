"""AsyncSSHClient（sync 包装版）单元测试

AsyncSSHClient 内部组合同步 SSHClient，通过 run_in_executor 在
线程池中执行同步操作，避免阻塞事件循环。测试通过 mock 内部
_sync 实例验证异步 API 的行为。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from remote_cmd.core.async_client import AsyncSSHClient
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig

pytestmark = pytest.mark.asyncio(loop_scope="module")


def make_client(sync: MagicMock = None, loop: asyncio.AbstractEventLoop = None):
    config = ConnectionConfig(hostname="h", username="u")
    client = AsyncSSHClient(config, loop=loop)
    if sync is not None:
        client._sync = sync
    return client


class TestAsyncSSHClientInit:
    """构造与元数据测试"""

    async def test_init_metadata(self):
        """测试：初始化元数据与默认状态"""
        config = ConnectionConfig(hostname="h", username="u")
        client = AsyncSSHClient(config)
        assert client.config is config
        assert client.is_connected() is False
        assert client._connection_id
        assert client._created_at > 0
        assert client._last_used > 0

    async def test_init_accepts_loop(self):
        """测试：接受外部传入的 loop"""
        loop = asyncio.get_event_loop()
        client = AsyncSSHClient(ConnectionConfig(hostname="h", username="u"), loop=loop)
        assert client._loop is loop


class TestConnectDisconnect:
    """连接与断开测试"""

    async def test_connect_calls_sync(self):
        """测试：connect 在线程池中调用同步连接并置位状态"""
        sync = MagicMock()
        client = make_client(sync=sync)
        result = await client.connect()
        assert result is client
        assert sync.connect.called
        assert client.is_connected() is True

    async def test_connect_when_already_connected(self):
        """测试：已连接时不重复连接"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._connected = True
        await client.connect()
        assert not sync.connect.called

    async def test_disconnect(self):
        """测试：断开连接并复位状态"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._connected = True
        await client.disconnect()
        assert sync.disconnect.called
        assert client.is_connected() is False

    async def test_sync_helpers_update_state(self):
        """测试：_connect_sync / _disconnect_sync 更新状态"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._connect_sync()
        assert client.is_connected() is True
        client._disconnect_sync()
        assert client.is_connected() is False


class TestExecute:
    """命令执行测试"""

    async def test_execute(self):
        """测试：execute 委托同步实现并返回结果"""
        sync = MagicMock()
        expected = CommandResult("cmd", "out", "", 0)
        sync.execute.return_value = expected
        client = make_client(sync=sync)
        result = await client.execute("ls -la", timeout=10, environment={"K": "V"})
        assert result is expected
        sync.execute.assert_called_once_with("ls -la", timeout=10, environment={"K": "V"})

    async def test_execute_sync_passthrough(self):
        """测试：_execute_sync 传递参数"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._execute_sync("cmd", timeout=5, environment=None)
        sync.execute.assert_called_once_with("cmd", timeout=5, environment=None)

    async def test_execute_sudo(self):
        """测试：execute_sudo 委托同步实现"""
        sync = MagicMock()
        expected = CommandResult("cmd", "", "denied", 1)
        sync.execute_sudo.return_value = expected
        client = make_client(sync=sync)
        result = await client.execute_sudo("whoami", password="pwd", timeout=5)
        assert result is expected
        sync.execute_sudo.assert_called_once_with("whoami", password="pwd", timeout=5)

    async def test_execute_sudo_sync_passthrough(self):
        """测试：_execute_sudo_sync 传递参数"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._execute_sudo_sync("whoami", password="p", timeout=3)
        sync.execute_sudo.assert_called_once_with("whoami", password="p", timeout=3)


class TestFileTransfer:
    """文件传输测试"""

    async def test_upload_file(self):
        """测试：upload_file 委托同步实现"""
        sync = MagicMock()
        client = make_client(sync=sync)
        await client.upload_file("/tmp/local", "/remote")
        sync.upload_file.assert_called_once_with("/tmp/local", "/remote")

    async def test_download_file(self):
        """测试：download_file 委托同步实现"""
        sync = MagicMock()
        client = make_client(sync=sync)
        await client.download_file("/remote", "/tmp/local")
        sync.download_file.assert_called_once_with("/remote", "/tmp/local")

    async def test_list_remote_directory(self):
        """测试：list_remote_directory 委托同步实现"""
        sync = MagicMock()
        sync.list_remote_directory.return_value = [{"name": "f"}]
        client = make_client(sync=sync)
        result = await client.list_remote_directory("/dir")
        assert result == [{"name": "f"}]
        sync.list_remote_directory.assert_called_once_with("/dir")

    async def test_upload_sync(self):
        """测试：_upload_sync 传递参数"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._upload_sync("/a", "/b")
        sync.upload_file.assert_called_once_with("/a", "/b")

    async def test_download_sync(self):
        """测试：_download_sync 传递参数"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._download_sync("/a", "/b")
        sync.download_file.assert_called_once_with("/a", "/b")

    async def test_list_dir_sync(self):
        """测试：_list_dir_sync 传递参数"""
        sync = MagicMock()
        client = make_client(sync=sync)
        client._list_dir_sync("/d")
        sync.list_remote_directory.assert_called_once_with("/d")


class TestContextManager:
    """异步上下文管理器测试"""

    async def test_async_with(self):
        """测试：__aenter__ 连接、__aexit__ 断开"""
        sync = MagicMock()
        client = make_client(sync=sync)
        async with client as c:
            assert c is client
            assert client.is_connected() is True
        assert client.is_connected() is False
        assert sync.connect.called
        assert sync.disconnect.called
