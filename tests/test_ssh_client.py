"""SSH 客户端全面单元测试

覆盖 SSHClient、ConnectionConfig、CommandResult 所有公共方法与异常路径。
通过 patch paramiko.SSHClient 模拟所有外部依赖。

目标覆盖率：≥90%
"""

from __future__ import annotations

import errno
import queue
import socket
import stat
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import paramiko
import pytest

from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig, SSHClient
from remote_cmd.utils.exceptions import (
    SSHAuthenticationError,
    SSHCommandError,
    SSHCommandTimeoutError,
    SSHConnectionError,
    SSHFileTransferError,
    SSHTimeoutError,
    ValidationError,
)

# ============================================================================
# ConnectionConfig
# ============================================================================


class TestConnectionConfig:
    def test_valid_with_password(self):
        c = ConnectionConfig(hostname="h", username="u", password="p")
        assert c.port == 22

    def test_valid_with_key(self):
        c = ConnectionConfig(hostname="h", username="u", key_filename="~/.ssh/id_rsa")
        assert c.key_filename == "~/.ssh/id_rsa"

    def test_agent_default(self):
        c = ConnectionConfig(hostname="h", username="u")
        assert c.password is None and c.key_filename is None

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
            ConnectionConfig(hostname="h", username="u", port=99999)

    def test_empty_hostname_raises(self):
        with pytest.raises(ValueError, match="hostname must not be empty"):
            ConnectionConfig(hostname="", username="u")

    def test_empty_username_raises(self):
        with pytest.raises(ValueError, match="username must not be empty"):
            ConnectionConfig(hostname="h", username="")


# ============================================================================
# CommandResult
# ============================================================================


class TestCommandResult:
    def test_success(self):
        r = CommandResult("ls", "out", "", 0)
        assert r.success is True
        assert "✓" in str(r)

    def test_failure(self):
        r = CommandResult("cmd", "", "err", 1)
        assert r.success is False
        assert "✗" in str(r)


# ============================================================================
# Fixtures
# ============================================================================


class _PollingChannel:
    """Small Paramiko-channel fake exposing the public readiness API."""

    def __init__(self, stdout: bytes = b"stdout data\n", stderr: bytes = b"") -> None:
        self.stdout_buffer = bytearray(stdout)
        self.stderr_buffer = bytearray(stderr)
        self.status_ready = True
        self.closed = False
        self.exit_code = 0
        self.recv = Mock(side_effect=self._recv_stdout)
        self.recv_stderr = Mock(side_effect=self._recv_stderr)
        self.recv_exit_status = Mock(side_effect=lambda: self.exit_code)
        self.close = Mock(side_effect=self._close)

    def recv_ready(self) -> bool:
        return bool(self.stdout_buffer)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr_buffer)

    def exit_status_ready(self) -> bool:
        return self.status_ready or self.closed

    def _recv_stdout(self, size: int) -> bytes:
        chunk = bytes(self.stdout_buffer[:size])
        del self.stdout_buffer[:size]
        return chunk

    def _recv_stderr(self, size: int) -> bytes:
        chunk = bytes(self.stderr_buffer[:size])
        del self.stderr_buffer[:size]
        return chunk

    def _close(self) -> None:
        self.closed = True


@pytest.fixture
def mock_paramiko():
    """Mock paramiko.SSHClient 及其返回值。"""
    with patch("remote_cmd.core.ssh_client.paramiko.SSHClient") as cls:
        inst = MagicMock()
        cls.return_value = inst

        # exec_command 模拟
        _stdin = Mock()
        channel = _PollingChannel()
        _stdout = Mock(channel=channel)
        _stderr = Mock(channel=channel)
        inst.exec_command.return_value = (_stdin, _stdout, _stderr)

        # transport 模拟
        _transport = MagicMock()
        _transport.is_active.return_value = True
        inst.get_transport.return_value = _transport
        inst.get_transport.return_value.is_active.return_value = True

        # SFTP
        _sftp = MagicMock()
        _sftp.put = MagicMock()
        _sftp.get = MagicMock()
        _sftp.listdir_attr = MagicMock(return_value=[])
        _sftp.stat = MagicMock()
        _sftp.mkdir = MagicMock()
        _sftp.remove = MagicMock()
        _sftp.rmdir = MagicMock()
        inst.open_sftp.return_value = _sftp

        # 管理
        inst.close = MagicMock()
        yield inst


@pytest.fixture
def key_file(tmp_path):
    p = tmp_path / "id_test"
    p.write_text("fake key")
    return str(p)


# ============================================================================
# 连接管理
# ============================================================================


class TestSSHClientConnect:
    def test_connect_password(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u", password="p")
        client = SSHClient(config)
        r = client.connect()
        assert r is client
        mock_paramiko.connect.assert_called_once_with(
            hostname="h", port=22, username="u", timeout=30, compress=True, password="p"
        )

    def test_connect_key(self, mock_paramiko, key_file):
        config = ConnectionConfig(hostname="h", username="u", key_filename=key_file)
        client = SSHClient(config)
        client.connect()
        kwargs = mock_paramiko.connect.call_args.kwargs
        assert "key_filename" in kwargs
        assert kwargs["key_filename"] == key_file

    def test_connect_key_file_missing(self, mock_paramiko):  # noqa: ARG002
        config = ConnectionConfig(hostname="h", username="u", key_filename="/no/such/key")
        with pytest.raises(SSHConnectionError, match="SSH key file not found"):
            SSHClient(config).connect()

    def test_connect_auth_failure(self, mock_paramiko):
        mock_paramiko.connect.side_effect = paramiko.AuthenticationException("bad auth")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHConnectionError, match="authentication failed"):
            SSHClient(config).connect()

    def test_connect_auth_failure_raises_authentication_error(self, mock_paramiko):
        """v2.1：认证失败细分为 SSHAuthenticationError（永久性，不重试），
        同时保持 SSHConnectionError 可捕获（既有契约）"""
        mock_paramiko.connect.side_effect = paramiko.AuthenticationException("bad auth")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHAuthenticationError, match="authentication failed"):
            SSHClient(config).connect()
        mock_paramiko.close.assert_called_once()

    def test_connect_timeout(self, mock_paramiko):
        mock_paramiko.connect.side_effect = socket.timeout("timeout")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHConnectionError, match="connection timeout"):
            SSHClient(config).connect()

    def test_connect_timeout_raises_timeout_error(self, mock_paramiko):
        """v2.1：连接超时细分为 SSHTimeoutError（瞬态，可重试）"""
        mock_paramiko.connect.side_effect = socket.timeout("timeout")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHTimeoutError, match="connection timeout"):
            SSHClient(config).connect()

    def test_connect_unresolved(self, mock_paramiko):
        mock_paramiko.connect.side_effect = socket.gaierror("unknown host")
        config = ConnectionConfig(hostname="nowhere", username="u")
        with pytest.raises(SSHConnectionError, match="could not resolve hostname"):
            SSHClient(config).connect()

    def test_connect_os_error(self, mock_paramiko):
        mock_paramiko.connect.side_effect = OSError("connection refused")
        config = ConnectionConfig(hostname="h", username="u")
        with pytest.raises(SSHConnectionError, match="connection error"):
            SSHClient(config).connect()

    def test_connect_known_hosts_loading(self, mock_paramiko, tmp_path):
        known = tmp_path / "known_hosts"
        known.write_text("example.com ssh-rsa AAA...")
        config = ConnectionConfig(
            hostname="h", username="u", password="p", known_hosts_file=str(known)
        )
        SSHClient(config).connect()
        # paramiko 的 load_host_keys 应被调用
        mock_paramiko.load_host_keys.assert_called_once_with(str(known))

    def test_default_loads_system_known_hosts_with_reject_policy(self, mock_paramiko):
        SSHClient(ConnectionConfig(hostname="h", username="u")).connect()

        mock_paramiko.load_system_host_keys.assert_called_once_with()
        policy = mock_paramiko.set_missing_host_key_policy.call_args.args[0]
        assert isinstance(policy, paramiko.RejectPolicy)

    def test_disconnect_cleanup(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        client = SSHClient(config)
        client.connect()
        client.disconnect()
        mock_paramiko.close.assert_called_once()

    def test_disconnect_with_sftp(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        client = SSHClient(config)
        client.connect()
        # 触发 SFTP 初始化
        client._get_sftp()
        client.disconnect()
        mock_paramiko.open_sftp.return_value.close.assert_called_once()

    def test_disconnect_double_safe(self, mock_paramiko):  # noqa: ARG002
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.disconnect()
        client.disconnect()

    def test_is_connected_true(self, mock_paramiko):  # noqa: ARG002
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        assert client.is_connected() is True

    def test_is_connected_before_connect(self):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        assert client.is_connected() is False

    def test_is_connected_transport_none(self, mock_paramiko):
        mock_paramiko.get_transport.return_value = None
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        assert client.is_connected() is False

    def test_context_manager(self, mock_paramiko):
        with SSHClient(ConnectionConfig(hostname="h", username="u", password="p")) as client:
            assert client.is_connected() is True
        mock_paramiko.close.assert_called_once()

    def test_context_manager_exception_safety(self, mock_paramiko):
        try:
            with SSHClient(ConnectionConfig(hostname="h", username="u")):
                raise ValueError("boom")
        except ValueError:
            pass
        mock_paramiko.close.assert_called_once()


# ============================================================================
# 命令执行
# ============================================================================


class TestSSHClientExecute:
    def test_execute_success(self, mock_paramiko):  # noqa: ARG002
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            r = client.execute("ls")
        assert r.exit_code == 0
        assert "stdout data" in r.stdout
        assert r.success

    def test_execute_with_environment(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.execute("ls", environment={"HOME": "/tmp"})
        cmd = mock_paramiko.exec_command.call_args[0][0]
        assert "export HOME=/tmp" in cmd

    def test_execute_without_connection_raises(self):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        with pytest.raises(SSHConnectionError, match="not connected"):
            client.execute("ls")

    def test_execute_ssh_exception(self, mock_paramiko):
        mock_paramiko.exec_command.side_effect = paramiko.SSHException("channel error")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHCommandError, match="command execution failed"),
        ):
            client.execute("ls")

    def test_execute_os_error(self, mock_paramiko):
        mock_paramiko.exec_command.side_effect = OSError("pipe broken")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHCommandError, match="command execution failed"),
        ):
            client.execute("ls")

    def test_execute_stdout_decoding(self, mock_paramiko):
        _stdin = Mock()
        channel = _PollingChannel(b"\xff\xfe\x00hello")
        _stdout = Mock(channel=channel)
        _stderr = Mock(channel=channel)
        mock_paramiko.exec_command.return_value = (_stdin, _stdout, _stderr)
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            r = client.execute("ls")
        assert isinstance(r.stdout, str)
        assert len(r.stdout) > 0

    def test_execute_sudo_without_password(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            r = client.execute_sudo("whoami")
        cmd = mock_paramiko.exec_command.call_args[0][0]
        assert "sudo" in cmd
        assert r.success

    def test_execute_sudo_with_password(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            r = client.execute_sudo("ls /root", password="mypass")
        assert r.success
        # 确认 sudo -S 模式被使用
        cmd = mock_paramiko.exec_command.call_args[0][0]
        assert "sudo -S" in cmd
        # 密码通过 stdin 传入
        stdin_mock = mock_paramiko.exec_command.return_value[0]
        stdin_mock.write.assert_called_with("mypass\n")

    def test_execute_sudo_exception(self, mock_paramiko):
        mock_paramiko.exec_command.side_effect = paramiko.SSHException("sudo fail")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client, pytest.raises(SSHCommandError, match="sudo"):
            client.execute_sudo("ls", password="x")


# ============================================================================
# 文件传输
# ============================================================================


class TestSSHClientFileTransfer:
    def test_upload_file_success(self, mock_paramiko, tmp_path):
        local = tmp_path / "a.txt"
        local.write_text("data")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.upload_file(str(local), "/remote/a.txt")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.put.assert_called_once()
        staged_path = sftp.put.call_args.args[1]
        assert staged_path.startswith("/remote/.remote-cmd-")
        staging_dir = sftp.mkdir.call_args.args[0]
        assert staged_path == f"{staging_dir}/upload"
        assert sftp.mkdir.call_args.kwargs["mode"] == 0o700
        sftp.posix_rename.assert_called_once_with(staged_path, "/remote/a.txt")
        sftp.rmdir.assert_called_once_with(staging_dir)

    def test_upload_missing_local(self, mock_paramiko):  # noqa: ARG002
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHFileTransferError, match="Local file not found"),
        ):
            client.upload_file("/no/file", "/remote/x")

    def test_upload_sftp_exception(self, mock_paramiko, tmp_path):
        local = tmp_path / "b.txt"
        local.write_text("x")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.put.side_effect = paramiko.SSHException("transfer fail")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="file upload failed"):
                client.upload_file(str(local), "/remote/x")
            assert client._sftp is None
        sftp.close.assert_called_once()

    def test_failed_staged_upload_does_not_replace_destination(self, mock_paramiko, tmp_path):
        local = tmp_path / "failed.txt"
        local.write_text("new data")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.put.side_effect = OSError("connection reset")

        with (
            SSHClient(ConnectionConfig(hostname="h", username="u")) as client,
            pytest.raises(SSHFileTransferError, match="file upload failed"),
        ):
            client.upload_file(str(local), "/remote/existing.txt")

        staged_path = sftp.put.call_args.args[1]
        assert staged_path != "/remote/existing.txt"
        sftp.posix_rename.assert_not_called()
        sftp.remove.assert_called_once_with(staged_path)
        sftp.rmdir.assert_called_once_with(staged_path.rsplit("/", 1)[0])

    def test_new_upload_uses_standard_rename_when_posix_extension_is_missing(
        self, mock_paramiko, tmp_path
    ):
        local = tmp_path / "new.txt"
        local.write_text("new")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.lstat.side_effect = FileNotFoundError(errno.ENOENT, "missing")
        sftp.posix_rename.side_effect = paramiko.SSHException("extension unsupported")

        with SSHClient(ConnectionConfig(hostname="h", username="u")) as client:
            client.upload_file(str(local), "/remote/new.txt")

        staged_path = sftp.put.call_args.args[1]
        sftp.rename.assert_called_once_with(staged_path, "/remote/new.txt")

    def test_existing_upload_fallback_commits_and_removes_backup(self, mock_paramiko, tmp_path):
        local = tmp_path / "replacement.txt"
        local.write_text("replacement")
        sftp = mock_paramiko.open_sftp.return_value
        attrs = paramiko.SFTPAttributes()
        attrs.st_mode = stat.S_IFREG | 0o640
        sftp.lstat.return_value = attrs
        sftp.posix_rename.side_effect = paramiko.SSHException("extension unsupported")
        sftp.rename.side_effect = [paramiko.SSHException("destination exists"), None, None]

        with SSHClient(ConnectionConfig(hostname="h", username="u")) as client:
            client.upload_file(str(local), "/remote/existing.txt")

        staged_path = sftp.put.call_args.args[1]
        backup_path = sftp.rename.call_args_list[1].args[1]
        assert sftp.rename.call_args_list[2].args == (staged_path, "/remote/existing.txt")
        sftp.remove.assert_called_once_with(backup_path)

    def test_existing_upload_fallback_rolls_back_and_preserves_mode(self, mock_paramiko, tmp_path):
        local = tmp_path / "replacement.txt"
        local.write_text("replacement")
        sftp = mock_paramiko.open_sftp.return_value
        attrs = paramiko.SFTPAttributes()
        attrs.st_mode = stat.S_IFREG | 0o640
        sftp.lstat.return_value = attrs
        sftp.posix_rename.side_effect = paramiko.SSHException("extension unsupported")
        sftp.rename.side_effect = [
            paramiko.SSHException("destination exists"),
            None,
            OSError("commit failed"),
            None,
        ]

        with (
            SSHClient(ConnectionConfig(hostname="h", username="u")) as client,
            pytest.raises(SSHFileTransferError, match="file upload failed"),
        ):
            client.upload_file(str(local), "/remote/existing.txt")

        staged_path = sftp.put.call_args.args[1]
        backup_path = sftp.rename.call_args_list[1].args[1]
        assert staged_path.startswith("/remote/.remote-cmd-")
        assert backup_path.startswith("/remote/.remote-cmd-")
        assert backup_path.endswith(".backup")
        sftp.chmod.assert_called_once_with(staged_path, 0o640)
        assert sftp.rename.call_args_list[2].args == (staged_path, "/remote/existing.txt")
        assert sftp.rename.call_args_list[3].args == (backup_path, "/remote/existing.txt")

    def test_upload_follows_destination_symlink(self, mock_paramiko, tmp_path):
        local = tmp_path / "link-target.txt"
        local.write_text("replacement")
        sftp = mock_paramiko.open_sftp.return_value
        link = paramiko.SFTPAttributes()
        link.st_mode = stat.S_IFLNK | 0o777
        target = paramiko.SFTPAttributes()
        target.st_mode = stat.S_IFREG | 0o600
        sftp.lstat.return_value = link
        sftp.normalize.return_value = "/remote/actual.txt"
        sftp.stat.return_value = target

        with SSHClient(ConnectionConfig(hostname="h", username="u")) as client:
            client.upload_file(str(local), "/remote/link.txt")

        staged_path = sftp.put.call_args.args[1]
        assert staged_path.startswith("/remote/.remote-cmd-")
        sftp.posix_rename.assert_called_once_with(staged_path, "/remote/actual.txt")
        sftp.chmod.assert_called_once_with(staged_path, 0o600)

    def test_upload_rejects_directory_destination(self, mock_paramiko, tmp_path):
        local = tmp_path / "source.txt"
        local.write_text("data")
        attrs = paramiko.SFTPAttributes()
        attrs.st_mode = stat.S_IFDIR | 0o755
        sftp = mock_paramiko.open_sftp.return_value
        sftp.lstat.return_value = attrs

        with (
            SSHClient(ConnectionConfig(hostname="h", username="u")) as client,
            pytest.raises(SSHFileTransferError, match="remote destination is a directory"),
        ):
            client.upload_file(str(local), "/remote/directory")

        sftp.put.assert_not_called()

    def test_download_file_success(self, mock_paramiko, tmp_path):
        local = tmp_path / "out" / "b.txt"
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.download_file("/remote/b.txt", str(local))
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get.assert_called_once()
        remote, staged_path = sftp.get.call_args.args
        assert remote == "/remote/b.txt"
        assert staged_path != str(local)
        assert local.exists()
        assert list(local.parent.glob(".*.part")) == []
        assert local.parent.exists()

    def test_download_sftp_exception(self, mock_paramiko, tmp_path):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get.side_effect = OSError("disk full")
        config = ConnectionConfig(hostname="h", username="u")
        local = tmp_path / "x.txt"
        local.write_text("existing-good-content")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="file download failed"):
                client.download_file("/remote/x", str(local))
            assert client._sftp is None
        assert local.read_text() == "existing-good-content"
        assert list(tmp_path.glob(".*.part")) == []

    def test_list_remote_directory(self, mock_paramiko):
        """在 mock 中需要构造 SFTPAttributes 列表。"""
        attr = MagicMock()
        attr.filename = "file.txt"
        attr.st_size = 100
        attr.st_mode = 0o100644
        attr.st_mtime = 1234567890
        sftp = mock_paramiko.open_sftp.return_value
        sftp.listdir_attr.return_value = [attr]
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            entries = client.list_remote_directory("/home")
        assert len(entries) == 1
        assert entries[0].name == "file.txt"
        assert entries[0].size == 100

    def test_list_remote_ssh_exception(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.listdir_attr.side_effect = paramiko.SSHException("ls fail")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHFileTransferError, match="failed to list remote directory"),
        ):
            client.list_remote_directory("/")

    def test_create_remote_directory(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = OSError("not found")  # 触发递归创建
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.create_remote_directory("/a/b/c")
        assert sftp.mkdir.call_count >= 1

    def test_remove_remote_file(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.remove_remote_file("/remote/x.txt")
        mock_paramiko.open_sftp.return_value.remove.assert_called_once()

    def test_remove_remote_file_exception(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.remove.side_effect = paramiko.SSHException("rm fail")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHFileTransferError, match="failed to delete remote file"),
        ):
            client.remove_remote_file("/remote/x.txt")

    def test_remove_remote_directory_recursive(self, mock_paramiko):
        # 设定一个包含子文件和子目录的目录结构
        file_attr = MagicMock()
        file_attr.filename = "a.txt"
        file_attr.st_mode = 0o100644
        file_attr.st_size = 0
        subdir_attr = MagicMock()
        subdir_attr.filename = "sub"
        subdir_attr.st_mode = 0o040755
        subdir_attr.st_size = 0
        sftp = mock_paramiko.open_sftp.return_value
        sftp.listdir_attr.return_value = [file_attr, subdir_attr]
        # 再次调用返回空
        sftp.listdir_attr.side_effect = [[file_attr, subdir_attr], []]
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.remove_remote_directory("/target", recursive=True)
        sftp.rmdir.assert_called()
        sftp.remove.assert_called()

    def test_remote_file_exists_true(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = None  # 默认 MagicMock 不为 OSError
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            assert client.remote_file_exists("/some/file") is True

    def test_remote_file_exists_false(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = OSError("not found")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            assert client.remote_file_exists("/no/file") is False

    def test_get_remote_file_info(self, mock_paramiko):
        stat_result = MagicMock()
        stat_result.st_mode = 0o100644
        stat_result.st_size = 42
        stat_result.st_mtime = 100
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.return_value = stat_result
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            info = client.get_remote_file_info("/remote/x.txt")
        assert info["size"] == 42
        assert info["is_file"] is True
        assert info["is_dir"] is False

    def test_get_remote_file_info_exception(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = paramiko.SSHException("stat fail")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as client,
            pytest.raises(SSHFileTransferError, match="failed to get file info"),
        ):
            client.get_remote_file_info("/remote/x.txt")

    def test_get_sftp_without_connection_raises(self):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        with pytest.raises(SSHConnectionError, match="not connected"):
            client._get_sftp()

    def test_known_hosts_missing_warning(self, mock_paramiko, tmp_path):  # noqa: ARG002
        config = ConnectionConfig(
            hostname="h", username="u", known_hosts_file=str(tmp_path / "nonexistent")
        )
        c = SSHClient(config)
        c.connect()

    def test_disconnect_sftp_error(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.close.side_effect = OSError("sftp error")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as c:
            c._get_sftp()
        c.disconnect()

    def test_disconnect_ssh_error(self, mock_paramiko):
        mock_paramiko.close.side_effect = paramiko.SSHException("ssh error")
        config = ConnectionConfig(hostname="h", username="u")
        c = SSHClient(config)
        c.connect()
        c.disconnect()

    def test_is_connected_transport_error(self, mock_paramiko):
        mock_paramiko.get_transport.side_effect = AttributeError("no transport")
        config = ConnectionConfig(hostname="h", username="u")
        c = SSHClient(config)
        c.connect()
        assert c.is_connected() is False

    def test_create_remote_directory_exception(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = OSError("not found")
        sftp.mkdir.side_effect = paramiko.SSHException("mkdir fail")
        config = ConnectionConfig(hostname="h", username="u")
        with (
            SSHClient(config) as c,
            pytest.raises(SSHFileTransferError, match="failed to create remote directory"),
        ):
            c.create_remote_directory("/a/b")

    def test_remote_file_exists_unconnected(self, mock_paramiko):  # noqa: ARG002
        c = SSHClient(ConnectionConfig(hostname="h", username="u"))
        assert c.remote_file_exists("/x") is False

    def test_get_remote_file_info_dir(self, mock_paramiko):
        stat_result = MagicMock()
        stat_result.st_mode = 0o040755
        stat_result.st_size = 0
        stat_result.st_mtime = 200
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.return_value = stat_result
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as c:
            info = c.get_remote_file_info("/dir")
        assert info["is_dir"] is True
        assert info["is_file"] is False


# ============================================================================
# v2.1：大输出死锁防护 / wall-clock 超时 / 环境变量键校验
# ============================================================================


class TestSSHClientDeadlockAndTimeout:
    """execute 的并发排空与超时语义（v2.1）"""

    def _connected_client(self, mock_paramiko):  # noqa: ARG002
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        return client

    def test_reads_streams_before_exit_status(self, mock_paramiko):
        """两个 readiness buffer 必须都排空后才读取退出状态。

        顺序颠倒（先 recv_exit_status）会在输出超过 SSH 通道窗口时
        死锁——paramiko 官方文档明确警告的场景。
        """
        client = self._connected_client(mock_paramiko)
        _stdin, _stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = _stdout.channel
        channel.stdout_buffer = bytearray(b"out")
        channel.stderr_buffer = bytearray(b"err")
        events = []
        recv_stdout = channel.recv.side_effect
        recv_stderr = channel.recv_stderr.side_effect

        def read_stdout(size):
            events.append("stdout")
            return recv_stdout(size)

        def read_stderr(size):
            events.append("stderr")
            return recv_stderr(size)

        channel.recv.side_effect = read_stdout
        channel.recv_stderr.side_effect = read_stderr
        channel.recv_exit_status.side_effect = lambda: (events.append("exit"), 0)[1]

        result = client.execute("ls")

        assert result.exit_code == 0
        assert result.stdout == "out"
        assert result.stderr == "err"
        # 每个流均有数据时，两者都先于退出状态包消费。
        assert events[-1] == "exit"
        assert set(events[:2]) == {"stdout", "stderr"}

    def test_large_output_both_streams(self, mock_paramiko):
        """大输出（超过默认通道窗口 2MB）在两个流上均可完整返回"""
        client = self._connected_client(mock_paramiko)
        _stdin, _stdout, _stderr = mock_paramiko.exec_command.return_value
        big_out = b"x" * (3 * 1024 * 1024)
        big_err = b"y" * (3 * 1024 * 1024)
        _stdout.channel.stdout_buffer = bytearray(big_out)
        _stdout.channel.stderr_buffer = bytearray(big_err)

        result = client.execute("cat /var/log/big.log")

        assert len(result.stdout) == 3 * 1024 * 1024
        assert len(result.stderr) == 3 * 1024 * 1024
        assert result.exit_code == 0

    def test_simultaneous_stream_flood_drains_a_bounded_remote_window(self, mock_paramiko):
        """Both producers exceed a tiny simulated SSH window without deadlock."""
        window_chunks = 2
        chunk_size = 32 * 1024
        total_bytes = 3 * 1024 * 1024

        class WindowedChannel:
            def __init__(self):
                self.stdout_queue: queue.Queue[bytes | None] = queue.Queue(window_chunks)
                self.stderr_queue: queue.Queue[bytes | None] = queue.Queue(window_chunks)
                self.producers_done = 0
                self.stdout_eof = False
                self.stderr_eof = False
                self.lock = threading.Lock()
                self.recv_exit_status = Mock(return_value=0)
                self.close = Mock()
                self.producer_threads = [
                    threading.Thread(target=self._produce, args=(self.stdout_queue, b"x"), daemon=True),
                    threading.Thread(target=self._produce, args=(self.stderr_queue, b"y"), daemon=True),
                ]
                for thread in self.producer_threads:
                    thread.start()

            def _produce(self, target: queue.Queue[bytes | None], value: bytes) -> None:
                try:
                    for _ in range(total_bytes // chunk_size):
                        target.put(value * chunk_size, timeout=5)
                    target.put(None, timeout=5)
                finally:
                    with self.lock:
                        self.producers_done += 1

            def recv_ready(self) -> bool:
                return not self.stdout_queue.empty()

            def recv_stderr_ready(self) -> bool:
                return not self.stderr_queue.empty()

            @staticmethod
            def _read(target: queue.Queue[bytes | None], size: int) -> bytes:
                item = target.get_nowait()
                if item is None:
                    return b""
                return item[:size]

            def recv(self, size: int) -> bytes:
                data = self._read(self.stdout_queue, size)
                if not data:
                    self.stdout_eof = True
                return data

            def recv_stderr(self, size: int) -> bytes:
                data = self._read(self.stderr_queue, size)
                if not data:
                    self.stderr_eof = True
                return data

            def exit_status_ready(self) -> bool:
                with self.lock:
                    return self.producers_done == 2 and self.stdout_eof and self.stderr_eof

        channel = WindowedChannel()
        stdout = Mock(channel=channel)
        stderr = Mock(channel=channel)
        mock_paramiko.exec_command.return_value = (Mock(), stdout, stderr)
        client = self._connected_client(mock_paramiko)

        result = client.execute("generate both streams")

        for thread in channel.producer_threads:
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert len(result.stdout.encode()) == total_bytes
        assert len(result.stderr.encode()) == total_bytes
        assert result.exit_code == 0

    def test_timeout_raises_command_timeout_error(self, mock_paramiko):
        """wall-clock 超时：挂起的命令在超时后抛 SSHCommandTimeoutError"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        closed = threading.Event()
        channel.stdout_buffer.clear()
        channel.stderr_buffer.clear()
        channel.status_ready = False
        channel.close.side_effect = lambda: (closed.set(), setattr(channel, "closed", True))

        with pytest.raises(SSHCommandTimeoutError, match="timed out after 0.1"):
            client.execute("sleep 100", timeout=0.1)

    def test_timeout_closes_channel(self, mock_paramiko):
        """超时后必须关闭通道以终止远端命令（避免僵尸进程）"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        closed = threading.Event()
        channel.stdout_buffer.clear()
        channel.stderr_buffer.clear()
        channel.status_ready = False
        channel.close.side_effect = lambda: (closed.set(), setattr(channel, "closed", True))

        with pytest.raises(SSHCommandTimeoutError):
            client.execute("sleep 100", timeout=0.1)
        channel.close.assert_called_once()

    def test_client_reusable_after_timeout(self, mock_paramiko):
        """超时命令后同一客户端必须可执行下一命令（无半关闭状态）。"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        channel.stdout_buffer.clear()
        channel.stderr_buffer.clear()
        channel.status_ready = False
        channel.close.side_effect = lambda: setattr(channel, "closed", True)

        with pytest.raises(SSHCommandTimeoutError):
            client.execute("sleep 100", timeout=0.1)

        fresh = _PollingChannel(stdout=b"recovered\n")
        mock_paramiko.exec_command.return_value = (Mock(), Mock(channel=fresh), Mock(channel=fresh))
        result = client.execute("echo ok")
        assert result.success is True
        assert result.stdout == "recovered\n"
        assert client.is_connected()

    def test_timeout_returns_if_channel_close_fails(self, mock_paramiko):
        """A failed close is bounded by the Paramiko channel I/O timeout fallback."""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        channel.stdout_buffer.clear()
        channel.stderr_buffer.clear()
        channel.status_ready = False
        channel.close.side_effect = OSError("close failed")
        mock_paramiko.close.side_effect = lambda: setattr(channel, "closed", True)
        started = time.monotonic()
        with pytest.raises(SSHCommandTimeoutError, match="timed out after 0.1"):
            client.execute("sleep 100", timeout=0.1)
        assert time.monotonic() - started < 1.0
        mock_paramiko.close.assert_called_once()

    def test_timeout_covers_exec_request_setup(self, mock_paramiko):
        """Paramiko exec request 等待阶段也必须受 wall-clock watchdog 约束。"""
        client = self._connected_client(mock_paramiko)
        closed = threading.Event()
        entered = threading.Event()
        mock_paramiko.close.side_effect = closed.set

        def blocking_exec(_command, timeout=None):  # noqa: ARG001
            entered.set()
            assert closed.wait(timeout=2)
            raise paramiko.SSHException("transport closed by command timeout")

        mock_paramiko.exec_command.side_effect = blocking_exec
        with pytest.raises(SSHCommandTimeoutError, match="timed out after 0.1"):
            client.execute("uptime", timeout=0.1)

        assert entered.is_set()
        assert mock_paramiko.close.called
        assert mock_paramiko.exec_command.call_args.kwargs["timeout"] == 0.1

    def test_timeout_covers_exit_status_wait(self, mock_paramiko):
        """输出 EOF 后迟迟不发 exit-status 也不得绕过调用超时。"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        status_ready = threading.Event()
        channel.stdout_buffer = bytearray(b"out")
        channel.stderr_buffer.clear()
        channel.status_ready = False

        def close_status_wait():
            status_ready.set()
            channel.closed = True

        channel.close.side_effect = close_status_wait

        with pytest.raises(SSHCommandTimeoutError, match="timed out after 0.1"):
            client.execute("uptime", timeout=0.1)

        assert status_ready.is_set()
        channel.close.assert_called_once()

    def test_stderr_reader_error_propagates(self, mock_paramiko):
        """stderr receive 异常在命令 worker 中回传并包装为 SSHCommandError"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        channel.stdout_buffer = bytearray(b"out")
        channel.stderr_buffer = bytearray(b"err")
        channel.recv_stderr.side_effect = OSError("connection reset during drain")

        with pytest.raises(SSHCommandError, match="command execution failed"):
            client.execute("ls")

    def test_no_timeout_by_default(self, mock_paramiko):
        """未传 timeout 时不创建超时定时器（历史行为：不限时）"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        channel.stdout_buffer = bytearray(b"ok")
        channel.stderr_buffer.clear()

        result = client.execute("ls")
        assert result.success is True
        channel.close.assert_not_called()

    def test_execute_sudo_timeout(self, mock_paramiko):
        """execute_sudo 复用同一 wall-clock 超时语义"""
        client = self._connected_client(mock_paramiko)
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        channel = stdout.channel
        closed = threading.Event()
        channel.stdout_buffer.clear()
        channel.stderr_buffer.clear()
        channel.status_ready = False
        channel.close.side_effect = lambda: (closed.set(), setattr(channel, "closed", True))

        with pytest.raises(SSHCommandTimeoutError, match="timed out after 0.1"):
            client.execute_sudo("sleep 100", password="pw", timeout=0.1)


class TestSSHClientEnvironmentValidation:
    """环境变量键注入校验（v2.1 安全加固）"""

    def test_invalid_key_rejected_before_execution(self, mock_paramiko):
        """含 shell 元字符的键在拼接命令前被拒绝（防命令注入）"""
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        with pytest.raises(ValidationError, match="invalid environment variable name"):
            client.execute("ls", environment={"A; rm -rf /": "x"})
        mock_paramiko.exec_command.assert_not_called()

    @pytest.mark.parametrize("bad_key", ["B;echo pwned", "$(cmd)", "`cmd`", "A B", "1BAD", ""])
    def test_various_malformed_keys_rejected(self, mock_paramiko, bad_key):  # noqa: ARG002
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        with pytest.raises(ValidationError):
            client.execute("ls", environment={bad_key: "v"})

    @pytest.mark.parametrize("good_key", ["FOO", "_FOO", "f9o_Bar"])
    def test_valid_keys_accepted(self, mock_paramiko, good_key):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()

        result = client.execute("ls", environment={good_key: "v z"})

        assert result.success is True
        cmd = mock_paramiko.exec_command.call_args[0][0]
        assert f"export {good_key}=" in cmd
        # 值必须被 shlex.quote 转义
        assert "'v z'" in cmd


class TestSSHClientReaderJoinHardening:
    """Polling read errors must close the channel without leaking a helper thread."""

    def test_stream_read_error_closes_channel_and_returns(self, mock_paramiko):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        _stdin, stdout, _stderr = mock_paramiko.exec_command.return_value
        stdout.channel.stdout_buffer = bytearray(b"data")
        stdout.channel.recv.side_effect = OSError("main read failed")

        with pytest.raises(SSHCommandError, match="command execution failed"):
            client.execute("ls")
        stdout.channel.close.assert_called_once()


# ============================================================================
# v2.3：SFTP inactivity 超时（Paramiko channel.settimeout）
# ============================================================================


class TestSSHClientFileTransferTimeout:
    """SFTP 操作使用 channel 级 inactivity 超时，静默即中止且清理会话。"""

    def test_open_sftp_channel_timeout_interrupts_stalled_subsystem_request(self, mock_paramiko):
        client_closed = threading.Event()
        mock_paramiko.close.side_effect = client_closed.set

        def stalled_open():
            assert client_closed.wait(timeout=2)
            raise paramiko.SSHException("transport closed during SFTP startup")

        mock_paramiko.open_sftp.side_effect = stalled_open
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()

        with pytest.raises(
            SSHFileTransferError,
            match="open SFTP channel timed out after 0.1 seconds of inactivity",
        ):
            client._get_sftp(timeout=0.1)

        assert client_closed.is_set()
        assert client._sftp is None

    def test_default_timeout_applied_to_sftp_channel(self, mock_paramiko, tmp_path):
        local = tmp_path / "a.txt"
        local.write_text("data")
        config = ConnectionConfig(hostname="h", username="u")  # timeout=30
        with SSHClient(config) as client:
            client.upload_file(str(local), "/remote/a.txt")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get_channel.return_value.settimeout.assert_called_with(30)
        # 成功路径行为不变
        sftp.put.assert_called_once()

    def test_explicit_timeout_overrides_config(self, mock_paramiko, tmp_path):
        local = tmp_path / "a.txt"
        local.write_text("data")
        with SSHClient(ConnectionConfig(hostname="h", username="u", timeout=99)) as client:
            client.upload_file(str(local), "/remote/a.txt", timeout=5)
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get_channel.return_value.settimeout.assert_called_with(5)

    def test_timeout_applied_before_blocking_transfer(self, mock_paramiko, tmp_path):
        """channel 超时必须先于 put/get 生效，否则真实静默仍会无限阻塞。"""

        class _Channel:
            timeout = None

            def settimeout(self, value):
                self.timeout = value

        class _SFTP:
            def __init__(self):
                self.channel = _Channel()
                self.observed_timeout = None

            def get_channel(self):
                return self.channel

            def lstat(self, _path):
                attrs = paramiko.SFTPAttributes()
                attrs.st_mode = stat.S_IFREG | 0o644
                return attrs

            def mkdir(self, _path, mode=0o777):
                self.staging_mode = mode

            def put(self, *_args, **_kwargs):
                # 记录 put 被调用时 channel 上已生效的超时
                self.observed_timeout = self.channel.timeout
                raise socket.timeout()

            def close(self):
                pass

        fake = _SFTP()
        mock_paramiko.open_sftp.return_value = fake
        local = tmp_path / "a.txt"
        local.write_text("data")

        client = SSHClient(ConnectionConfig(hostname="h", username="u", timeout=17))
        with client, pytest.raises(SSHFileTransferError, match="timed out"):
            client.upload_file(str(local), "/remote/a.txt")

        assert fake.observed_timeout == 17
        assert client._sftp is None

    def test_upload_timeout_maps_to_file_transfer_error_and_cleans_up(
        self, mock_paramiko, tmp_path
    ):
        local = tmp_path / "a.txt"
        local.write_text("data")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.put.side_effect = socket.timeout()

        client = SSHClient(ConnectionConfig(hostname="h", username="u", timeout=12))
        with (
            client,
            pytest.raises(
                SSHFileTransferError,
                match="file upload timed out after 12 seconds of inactivity",
            ),
        ):
            client.upload_file(str(local), "/remote/a.txt")

        # 超时后丢弃失步会话，避免复用；通道被关闭（无泄漏）
        assert client._sftp is None
        sftp.close.assert_called_once()

    def test_download_timeout_maps_to_file_transfer_error_and_cleans_up(
        self, mock_paramiko, tmp_path
    ):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get.side_effect = socket.timeout()

        client = SSHClient(ConnectionConfig(hostname="h", username="u", timeout=3))
        with (
            client,
            pytest.raises(
                SSHFileTransferError,
                match="file download timed out after 3 seconds of inactivity",
            ),
        ):
            client.download_file("/remote/x", str(tmp_path / "x.txt"))

        assert client._sftp is None
        sftp.close.assert_called_once()

    def test_list_directory_timeout_maps(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.listdir_attr.side_effect = socket.timeout()
        with (
            SSHClient(ConnectionConfig(hostname="h", username="u")) as client,
            pytest.raises(SSHFileTransferError, match="list remote directory timed out"),
        ):
            client.list_remote_directory("/tmp")

    def test_non_positive_timeout_rejected(self, mock_paramiko, tmp_path):  # noqa: ARG002
        local = tmp_path / "a.txt"
        local.write_text("data")
        with (
            SSHClient(ConnectionConfig(hostname="h", username="u")) as client,
            pytest.raises(ValidationError, match="timeout must be > 0"),
        ):
            client.upload_file(str(local), "/remote/a.txt", timeout=0)

    def test_remote_file_exists_timeout_returns_false_and_discards(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.stat.side_effect = socket.timeout()
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        with client:
            assert client.remote_file_exists("/remote/x") is False
            assert client._sftp is None


# ============================================================================
# v2.7（P2.4）：每命令线程模型回归锁定
# ============================================================================


class TestReadOutputThreadModel:
    """The command worker polls both streams; only timed commands need a Timer."""

    @staticmethod
    def _spy(monkeypatch):
        """以代理对象替换 remote_cmd.core.ssh_client 的 threading 引用。

        不污染全局 threading 模块：threading.Timer 内部对 Thread 的引用
        仍指向真实类（否则示例化 Timer 会因元类不匹配而失败）。
        """
        created_threads: list[str] = []
        created_timers: list[threading.Timer] = []

        class CountingThread(threading.Thread):
            def __init__(self, *args, **kwargs):
                created_threads.append(kwargs.get("name", "unnamed"))
                super().__init__(*args, **kwargs)

        class CountingTimer(threading.Timer):
            def __init__(self, *args, **kwargs):
                created_timers.append(self)
                super().__init__(*args, **kwargs)

        class ThreadingSpy:
            Thread = CountingThread
            Timer = CountingTimer

            def __getattr__(self, name):
                return getattr(threading, name)

        monkeypatch.setattr("remote_cmd.core.ssh_client.threading", ThreadingSpy())
        return created_threads, created_timers

    def test_no_timeout_creates_no_auxiliary_thread(self, mock_paramiko, monkeypatch):
        threads, timers = self._spy(monkeypatch)

        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        client.execute("ls")  # timeout=None

        assert threads == []
        assert timers == []

    def test_timeout_adds_one_timer_thread(self, mock_paramiko, monkeypatch):
        threads, timers = self._spy(monkeypatch)

        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.connect()
        client.execute("ls", timeout=30)

        assert threads == []
        assert len(timers) == 1
