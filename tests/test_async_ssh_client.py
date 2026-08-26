"""Native asyncssh AsyncSSHClient 单元测试

通过 patch `remote_cmd.core.async_ssh_client.asyncssh` 模拟 asyncssh 的 connect/run/
SFTP 等行为，验证 AsyncSSHClient 在不真实连接 SSH 的情况下功能正常。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.utils.exceptions import (
    SSHAuthenticationError,
    SSHConnectionError,
    SSHTimeoutError,
    ValidationError,
)

# ============================================================================
# Fixtures
# ============================================================================


def _make_conn_mock(stdout="OK\n", stderr="", exit_status=0):
    """构造模拟 SSHClientConnection。"""
    conn = MagicMock()
    # asyncssh 用 is_closed() 表示连接是否已关闭；活动连接应返回 False
    conn.is_closed.return_value = False
    conn.run = AsyncMock(
        return_value=MagicMock(stdout=stdout, stderr=stderr, exit_status=exit_status)
    )
    # create_process 返回 SSHClientProcess 模拟
    proc = MagicMock()
    proc.stdin = MagicMock()
    wait = AsyncMock(return_value=MagicMock(stdout=stdout, stderr=stderr, exit_status=exit_status))
    proc.wait = wait
    conn.create_process = AsyncMock(return_value=proc)
    # SFTP
    sftp = MagicMock()
    sftp.put = AsyncMock()
    sftp.get = AsyncMock()
    sftp.readdir = AsyncMock(return_value=[])
    # asyncssh SFTPClient.exit() 是同步方法
    sftp.exit = MagicMock()
    conn.start_sftp_client = AsyncMock(return_value=sftp)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    return conn, sftp


@pytest.fixture
def config():
    return ConnectionConfig(hostname="test-host", username="admin", port=22)


@pytest.fixture
def conn_mock():
    conn, sftp = _make_conn_mock()
    return conn


@pytest.fixture
def patched_asyncssh(conn_mock):
    """patch 模块级的 asyncssh，使 connect 返回 conn_mock。"""
    with patch("remote_cmd.core.async_ssh_client.asyncssh") as mock_ssh:
        mock_ssh.connect = AsyncMock(return_value=conn_mock)
        mock_ssh.SSHClientConnection = MagicMock()
        # 真实异常类型层级：Error 为基类，其余为子类
        mock_ssh.Error = type("Error", (Exception,), {})
        mock_ssh.PermissionDenied = type("PermissionDenied", (mock_ssh.Error,), {})
        mock_ssh.TimeoutError = type("TimeoutError", (mock_ssh.Error,), {})
        mock_ssh.ChannelOpenError = type("ChannelOpenError", (mock_ssh.Error,), {})
        yield mock_ssh


# ============================================================================
# 连接管理
# ============================================================================


class TestAsyncSSHClientConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self, config, patched_asyncssh, conn_mock):
        client = AsyncSSHClient(config)
        result = await client.connect()
        assert result is client
        assert client.is_connected() is True
        patched_asyncssh.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_idempotent(self, config, patched_asyncssh, conn_mock):
        client = AsyncSSHClient(config)
        await client.connect()
        await client.connect()  # 不会再次 connect
        assert patched_asyncssh.connect.await_count == 1

    @pytest.mark.asyncio
    async def test_connect_auth_failed(self, config, patched_asyncssh):
        patched_asyncssh.PermissionDenied = type("PD", (Exception,), {})
        patched_asyncssh.connect = AsyncMock(
            side_effect=patched_asyncssh.PermissionDenied("bad creds")
        )
        client = AsyncSSHClient(config)
        with pytest.raises(SSHConnectionError, match="authentication failed"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_auth_failed_raises_authentication_error(self, config, patched_asyncssh):
        """v2.1：认证失败细分为 SSHAuthenticationError（永久性，不重试），
        同时保持 SSHConnectionError 可捕获（既有契约）"""
        patched_asyncssh.PermissionDenied = type("PD", (Exception,), {})
        patched_asyncssh.connect = AsyncMock(
            side_effect=patched_asyncssh.PermissionDenied("bad creds")
        )
        client = AsyncSSHClient(config)
        with pytest.raises(SSHAuthenticationError, match="authentication failed"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_timeout(self, config, patched_asyncssh):
        patched_asyncssh.connect = AsyncMock(side_effect=OSError("Connection timed out"))
        client = AsyncSSHClient(config)
        with pytest.raises(SSHConnectionError, match="connection timeout"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_timeout_raises_timeout_error(self, config, patched_asyncssh):
        """v2.1：连接超时细分为 SSHTimeoutError（瞬态，可重试）"""
        patched_asyncssh.connect = AsyncMock(side_effect=OSError("Connection timed out"))
        client = AsyncSSHClient(config)
        with pytest.raises(SSHTimeoutError, match="connection timeout"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_disconnect(self, config, patched_asyncssh, conn_mock):
        client = AsyncSSHClient(config)
        await client.connect()
        await client.disconnect()
        assert client.is_connected() is False
        conn_mock.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager(self, config, patched_asyncssh, conn_mock):
        async with AsyncSSHClient(config) as client:
            assert client.is_connected() is True
        assert client.is_connected() is False


# ============================================================================
# 命令执行
# ============================================================================


class TestAsyncSSHClientExecute:
    @pytest.mark.asyncio
    async def test_execute_success(self, config, patched_asyncssh, conn_mock):
        conn_mock.run = AsyncMock(
            return_value=MagicMock(stdout="hello\n", stderr="", exit_status=0)
        )
        async with AsyncSSHClient(config) as client:
            r = await client.execute("echo hello")
        assert isinstance(r, CommandResult)
        assert r.success
        assert r.stdout == "hello\n"
        assert r.exit_code == 0
        assert r.command == "echo hello"

    @pytest.mark.asyncio
    async def test_execute_with_environment(self, config, patched_asyncssh, conn_mock):
        async with AsyncSSHClient(config) as client:
            await client.execute("ls", environment={"FOO": "bar"})
        # 断言 run 被调用，命令中包含 export 前缀注入的环境变量；
        # env 不再通过 conn.run(env=...) 传递（依赖服务端 AcceptEnv 且
        # 与同步实现语义分叉，v2.1 起仅保留 shell 前缀注入）
        args, kwargs = conn_mock.run.call_args
        assert "export FOO=bar" in args[0]
        assert "env" not in kwargs

    @pytest.mark.asyncio
    async def test_execute_failure_exit_code(self, config, patched_asyncssh, conn_mock):
        conn_mock.run = AsyncMock(return_value=MagicMock(stdout="", stderr="boom", exit_status=127))
        async with AsyncSSHClient(config) as client:
            r = await client.execute("badcmd")
        assert r.success is False
        assert r.exit_code == 127
        assert r.stderr == "boom"

    @pytest.mark.asyncio
    async def test_execute_sudo_without_password(self, config, patched_asyncssh, conn_mock):
        conn_mock.run = AsyncMock(return_value=MagicMock(stdout="ok", stderr="", exit_status=0))
        async with AsyncSSHClient(config) as client:
            r = await client.execute_sudo("whoami")
        assert r.success
        # 应委托给 execute，命令前缀 sudo
        args, _ = conn_mock.run.call_args
        assert args[0].endswith("sudo whoami")

    @pytest.mark.asyncio
    async def test_execute_sudo_with_password(self, config, patched_asyncssh, conn_mock):
        async with AsyncSSHClient(config) as client:
            r = await client.execute_sudo("ls /root", password="secret")
        assert r.success
        conn_mock.create_process.assert_awaited_once()
        # 确认密码被写入 stdin
        proc = conn_mock.create_process.return_value
        proc.stdin.write.assert_called_with("secret\n")

    @pytest.mark.asyncio
    async def test_execute_not_connected_raises(self, config):
        client = AsyncSSHClient(config)
        with pytest.raises(SSHConnectionError, match="not connected"):
            await client.execute("ls")


# ============================================================================
# 文件传输
# ============================================================================


class TestAsyncSSHClientFileTransfer:
    @pytest.mark.asyncio
    async def test_upload_file(self, config, patched_asyncssh, conn_mock, tmp_path):
        local = tmp_path / "a.txt"
        local.write_text("data")
        async with AsyncSSHClient(config) as client:
            await client.upload_file(str(local), "/remote/a.txt")
        conn_mock.start_sftp_client.assert_awaited_once()
        # sftp 为 mock，put 被 await 调用
        sftp = conn_mock.start_sftp_client.return_value
        sftp.put.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upload_missing_local(self, config, patched_asyncssh, conn_mock):
        from remote_cmd.utils.exceptions import SSHFileTransferError

        async with AsyncSSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="Local file not found"):
                await client.upload_file("/no/such/file", "/remote/x")

    @pytest.mark.asyncio
    async def test_download_file(self, config, patched_asyncssh, conn_mock, tmp_path):
        local = tmp_path / "out" / "b.txt"
        async with AsyncSSHClient(config) as client:
            await client.download_file("/remote/b.txt", str(local))
        sftp = conn_mock.start_sftp_client.return_value
        sftp.get.assert_awaited_once()
        assert local.parent.exists()

    @pytest.mark.asyncio
    async def test_list_remote_directory(self, config, patched_asyncssh, conn_mock):
        # 构造两个 readdir 条目
        file_entry = MagicMock(filename="a.txt")
        file_entry.attrs = MagicMock(size=10)
        file_entry.attrs.permissions = 0o100644
        dir_entry = MagicMock(filename="subdir")
        dir_entry.attrs = MagicMock(size=0)
        dir_entry.attrs.permissions = 0o040755
        sftp = conn_mock.start_sftp_client.return_value
        sftp.readdir = AsyncMock(return_value=[file_entry, dir_entry])

        async with AsyncSSHClient(config) as client:
            entries = await client.list_remote_directory("/home")
        assert len(entries) == 2
        assert entries[0].name == "a.txt"
        assert entries[1].is_dir is True


# ============================================================================
# v2.1：环境变量键校验（安全加固）
# ============================================================================


class TestAsyncSSHClientEnvironmentValidation:
    @pytest.mark.asyncio
    async def test_invalid_key_rejected_before_execution(self, config, patched_asyncssh, conn_mock):
        """含 shell 元字符的键在拼接命令前被拒绝（防命令注入，与同步实现一致）"""
        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(ValidationError, match="invalid environment variable name"):
            await client.execute("ls", environment={"A; rm -rf /": "x"})
        conn_mock.run.assert_not_awaited()

    @pytest.mark.parametrize("bad_key", ["B;echo pwned", "$(cmd)", "`cmd`", "A B", "1BAD", ""])
    @pytest.mark.asyncio
    async def test_various_malformed_keys_rejected(self, config, patched_asyncssh, bad_key):
        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(ValidationError):
            await client.execute("ls", environment={bad_key: "v"})
