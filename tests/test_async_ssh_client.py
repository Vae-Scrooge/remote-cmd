"""Native asyncssh AsyncSSHClient 单元测试

通过 patch `remote_cmd.core.async_ssh_client.asyncssh` 模拟 asyncssh 的 connect/run/
SFTP 等行为，验证 AsyncSSHClient 在不真实连接 SSH 的情况下功能正常。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.utils.exceptions import (
    SSHAuthenticationError,
    SSHConnectionError,
    SSHFileTransferError,
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
        # 确认密码以 UTF-8 bytes 写入 stdin（asyncssh 二进制流契约：
        # 未指定 encoding 时写入 str 会在真实连接上抛 TypeError）
        proc = conn_mock.create_process.return_value
        proc.stdin.write.assert_called_with(b"secret\n")

    @pytest.mark.asyncio
    async def test_execute_sudo_rejects_str_stdin_write(self, config, patched_asyncssh, conn_mock):
        """回归：stdin 必须收到 bytes；若回退为 str，模拟真实 asyncssh 报错。"""

        def _require_bytes(data):
            if not isinstance(data, bytes):
                raise TypeError("string argument without an encoding")

        proc = conn_mock.create_process.return_value
        proc.stdin.write.side_effect = _require_bytes

        async with AsyncSSHClient(config) as client:
            r = await client.execute_sudo("ls /root", password="secret")
        assert r.success
        assert proc.stdin.write.call_args.args[0] == b"secret\n"

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


# ============================================================================
# v2.3：SFTP inactivity 超时（asyncssh watchdog + asyncio 取消清理）
# ============================================================================


async def _stall(*_args, **_kwargs):
    """永不完成的异步桩：模拟 SFTP 通道静默挂起。"""
    await asyncio.Event().wait()


class TestAsyncSSHClientFileTransferTimeout:
    @pytest.mark.asyncio
    async def test_upload_stalled_times_out_and_cleans_up(
        self, config, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = conn_mock.start_sftp_client.return_value
        sftp.put = AsyncMock(side_effect=_stall)

        client = AsyncSSHClient(config)
        await client.connect()
        start = time.monotonic()
        with pytest.raises(
            SSHFileTransferError,
            match="file upload timed out after 0.2 seconds of inactivity",
        ):
            await client.upload_file(str(local), "/remote/a.txt", timeout=0.2)
        elapsed = time.monotonic() - start

        # 超时被观测到（而非立即/永久挂起），且失步会话被丢弃、exit 被调用
        assert 0.15 <= elapsed < 5.0
        assert client._sftp is None
        sftp.exit.assert_called_once()
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_download_stalled_times_out_and_cleans_up(
        self, config, patched_asyncssh, conn_mock, tmp_path
    ):
        sftp = conn_mock.start_sftp_client.return_value
        sftp.get = AsyncMock(side_effect=_stall)

        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(
            SSHFileTransferError,
            match="file download timed out after 0.2 seconds of inactivity",
        ):
            await client.download_file("/remote/x", str(tmp_path / "x.txt"), timeout=0.2)

        assert client._sftp is None
        sftp.exit.assert_called_once()
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_configured_timeout_used_when_omitted(
        self, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = conn_mock.start_sftp_client.return_value
        sftp.put = AsyncMock(side_effect=_stall)

        config = ConnectionConfig(hostname="h", username="u", timeout=0.2)
        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(SSHFileTransferError, match="timed out after 0.2 seconds"):
            await client.upload_file(str(local), "/remote/a.txt")
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_list_directory_stalled_times_out(self, config, patched_asyncssh, conn_mock):
        sftp = conn_mock.start_sftp_client.return_value
        sftp.readdir = AsyncMock(side_effect=_stall)

        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(SSHFileTransferError, match="list remote directory timed out"):
            await client.list_remote_directory("/tmp", timeout=0.2)
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_open_sftp_channel_stalled_times_out(
        self, config, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        conn_mock.start_sftp_client = AsyncMock(side_effect=_stall)

        client = AsyncSSHClient(config)
        await client.connect()
        with pytest.raises(SSHFileTransferError, match="failed to open SFTP channel: timed out"):
            await client.upload_file(str(local), "/remote/a.txt", timeout=0.2)
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_success_path_passes_progress_handler(
        self, config, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        async with AsyncSSHClient(config) as client:
            await client.upload_file(str(local), "/remote/a.txt")
        sftp = conn_mock.start_sftp_client.return_value
        assert callable(sftp.put.await_args.kwargs["progress_handler"])

    @pytest.mark.asyncio
    async def test_non_positive_timeout_rejected(
        self, config, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        async with AsyncSSHClient(config) as client:
            with pytest.raises(ValidationError, match="timeout must be > 0"):
                await client.upload_file(str(local), "/remote/a.txt", timeout=0)


# ============================================================================
# v2.3 加固：异步 SFTP 取消语义（会话丢弃 / 重复取消 / 清理期间取消）
# ============================================================================


def _pending_tasks() -> set:
    """当前事件循环中除当前任务外的未完成任务。"""
    current = asyncio.current_task()
    return {t for t in asyncio.all_tasks() if not t.done() and t is not current}


def _make_gated_stall():
    """返回 (async_stall, started, cancel_entered, release)。

    async_stall 先停滞；被取消时进入取消处理器并等待 release 事件，
    用于确定性制造"超时清理正在等待子任务取消"的窗口。
    """
    started = asyncio.Event()
    cancel_entered = asyncio.Event()
    release = asyncio.Event()

    async def _stall(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancel_entered.set()
            await release.wait()
            raise

    return _stall, started, cancel_entered, release


class TestAsyncSSHClientFileTransferCancellation:
    @pytest.mark.asyncio
    async def test_outer_cancellation_discards_session_and_reopens(
        self, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = conn_mock.start_sftp_client.return_value
        started = asyncio.Event()

        async def _stall(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        sftp.put = AsyncMock(side_effect=_stall)
        client = AsyncSSHClient(ConnectionConfig(hostname="h", username="u", timeout=30))
        await client.connect()
        baseline = _pending_tasks()

        task = asyncio.create_task(client.upload_file(str(local), "/remote/a.txt", timeout=5))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 取消后立即丢弃会话，且没有遗留任务
        assert client._sftp is None
        sftp.exit.assert_called_once()
        assert _pending_tasks() - baseline == set()

        # 后续 SFTP 操作必须重新打开会话（不复用被取消的会话）
        sftp.readdir = AsyncMock(return_value=[])
        assert await client.list_remote_directory("/tmp") == []
        assert conn_mock.start_sftp_client.await_count == 2
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_repeated_cancellation_propagates_and_cleans_up(
        self, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = conn_mock.start_sftp_client.return_value
        stall, started, cancel_entered, release = _make_gated_stall()
        sftp.put = AsyncMock(side_effect=stall)
        client = AsyncSSHClient(ConnectionConfig(hostname="h", username="u", timeout=30))
        await client.connect()
        baseline = _pending_tasks()

        task = asyncio.create_task(client.upload_file(str(local), "/remote/a.txt", timeout=5))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        # 第一次取消已进入清理（等待子任务取消完成），此时再次取消
        await asyncio.wait_for(cancel_entered.wait(), timeout=5)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        # 重复取消不得中断清理：会话仍被丢弃
        assert client._sftp is None
        sftp.exit.assert_called_once()

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert _pending_tasks() - baseline == set()
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_cancellation_during_timeout_cleanup_propagates(
        self, patched_asyncssh, conn_mock, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = conn_mock.start_sftp_client.return_value
        stall, _started, cancel_entered, release = _make_gated_stall()
        sftp.put = AsyncMock(side_effect=stall)
        client = AsyncSSHClient(ConnectionConfig(hostname="h", username="u", timeout=30))
        await client.connect()
        baseline = _pending_tasks()

        # timeout 极短：watchdog 触发后进入"等待子任务取消"的清理窗口
        task = asyncio.create_task(client.upload_file(str(local), "/remote/a.txt", timeout=0.05))
        await asyncio.wait_for(cancel_entered.wait(), timeout=5)
        # 清理进行中到达的外层取消必须传播，而不是被转换为超时错误
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 清理仍然完成：会话被丢弃、无遗留任务
        assert client._sftp is None
        sftp.exit.assert_called_once()
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert _pending_tasks() - baseline == set()
        await client.disconnect()
