"""SSH 客户端全面单元测试

覆盖 SSHClient、ConnectionConfig、CommandResult 所有公共方法与异常路径。
通过 patch paramiko.SSHClient 模拟所有外部依赖。

目标覆盖率：≥90%
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import paramiko
import pytest

from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig, SSHClient
from remote_cmd.utils.exceptions import SSHCommandError, SSHConnectionError, SSHFileTransferError


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
        with pytest.raises(ValueError, match="端口"):
            ConnectionConfig(hostname="h", username="u", port=99999)

    def test_empty_hostname_raises(self):
        with pytest.raises(ValueError, match="主机名"):
            ConnectionConfig(hostname="", username="u")

    def test_empty_username_raises(self):
        with pytest.raises(ValueError, match="用户名"):
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


@pytest.fixture
def mock_paramiko():
    """Mock paramiko.SSHClient 及其返回值。"""
    with patch("remote_cmd.core.ssh_client.paramiko.SSHClient") as cls:
        inst = MagicMock()
        cls.return_value = inst

        # exec_command 模拟
        _stdin = Mock()
        _stdout = Mock()
        _stderr = Mock()
        _stdout.channel.recv_exit_status.return_value = 0
        _stdout.read.return_value = b"stdout data\n"
        _stderr.read.return_value = b""
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

    def test_connect_key_file_missing(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u", key_filename="/no/such/key")
        with pytest.raises(SSHConnectionError, match="密钥文件不存在"):
            SSHClient(config).connect()

    def test_connect_auth_failure(self, mock_paramiko):
        mock_paramiko.connect.side_effect = paramiko.AuthenticationException("bad auth")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHConnectionError, match="认证失败"):
            SSHClient(config).connect()

    def test_connect_timeout(self, mock_paramiko):
        mock_paramiko.connect.side_effect = socket.timeout("timeout")
        config = ConnectionConfig(hostname="h", username="u", password="p")
        with pytest.raises(SSHConnectionError, match="连接超时"):
            SSHClient(config).connect()

    def test_connect_unresolved(self, mock_paramiko):
        mock_paramiko.connect.side_effect = socket.gaierror("unknown host")
        config = ConnectionConfig(hostname="nowhere", username="u")
        with pytest.raises(SSHConnectionError, match="无法解析主机名"):
            SSHClient(config).connect()

    def test_connect_os_error(self, mock_paramiko):
        mock_paramiko.connect.side_effect = OSError("connection refused")
        config = ConnectionConfig(hostname="h", username="u")
        with pytest.raises(SSHConnectionError, match="连接错误"):
            SSHClient(config).connect()

    def test_connect_known_hosts_loading(self, mock_paramiko, tmp_path):
        known = tmp_path / "known_hosts"
        known.write_text("example.com ssh-rsa AAA...")
        config = ConnectionConfig(hostname="h", username="u", password="p", known_hosts_file=str(known))
        SSHClient(config).connect()
        # paramiko 的 load_host_keys 应被调用
        mock_paramiko.load_host_keys.assert_called_once_with(str(known))

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

    def test_disconnect_double_safe(self, mock_paramiko):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        client.disconnect()
        client.disconnect()

    def test_is_connected_true(self, mock_paramiko):
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
    def test_execute_success(self, mock_paramiko):
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
        with pytest.raises(SSHConnectionError, match="未连接"):
            client.execute("ls")

    def test_execute_ssh_exception(self, mock_paramiko):
        mock_paramiko.exec_command.side_effect = paramiko.SSHException("channel error")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHCommandError, match="执行命令"):
                client.execute("ls")

    def test_execute_os_error(self, mock_paramiko):
        mock_paramiko.exec_command.side_effect = OSError("pipe broken")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHCommandError, match="执行命令"):
                client.execute("ls")

    def test_execute_stdout_decoding(self, mock_paramiko):
        _stdin = Mock()
        _stdout = Mock()
        _stderr = Mock()
        _stdout.channel.recv_exit_status.return_value = 0
        _stdout.read.return_value = b"\xff\xfe\x00hello"  # 非 utf-8 字节
        _stderr.read.return_value = b""
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
        with SSHClient(config) as client:
            with pytest.raises(SSHCommandError, match="sudo"):
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

    def test_upload_missing_local(self, mock_paramiko):
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="本地文件不存在"):
                client.upload_file("/no/file", "/remote/x")

    def test_upload_sftp_exception(self, mock_paramiko, tmp_path):
        local = tmp_path / "b.txt"
        local.write_text("x")
        sftp = mock_paramiko.open_sftp.return_value
        sftp.put.side_effect = paramiko.SSHException("transfer fail")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="文件上传失败"):
                client.upload_file(str(local), "/remote/x")

    def test_download_file_success(self, mock_paramiko, tmp_path):
        local = tmp_path / "out" / "b.txt"
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            client.download_file("/remote/b.txt", str(local))
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get.assert_called_once()
        assert local.parent.exists()

    def test_download_sftp_exception(self, mock_paramiko, tmp_path):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.get.side_effect = OSError("disk full")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="文件下载失败"):
                client.download_file("/remote/x", str(tmp_path / "x.txt"))

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
        assert entries[0]["name"] == "file.txt"
        assert entries[0]["size"] == 100

    def test_list_remote_ssh_exception(self, mock_paramiko):
        sftp = mock_paramiko.open_sftp.return_value
        sftp.listdir_attr.side_effect = paramiko.SSHException("ls fail")
        config = ConnectionConfig(hostname="h", username="u")
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="列出远程目录失败"):
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
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="删除远程文件失败"):
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
        with SSHClient(config) as client:
            with pytest.raises(SSHFileTransferError, match="获取文件信息失败"):
                client.get_remote_file_info("/remote/x.txt")

    def test_get_sftp_without_connection_raises(self):
        client = SSHClient(ConnectionConfig(hostname="h", username="u"))
        with pytest.raises(SSHConnectionError, match="未连接"):
            client._get_sftp()

    def test_known_hosts_missing_warning(self, mock_paramiko, tmp_path):
        config = ConnectionConfig(hostname="h", username="u", known_hosts_file=str(tmp_path / "nonexistent"))
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
        with SSHClient(config) as c:
            with pytest.raises(SSHFileTransferError, match="创建远程目录失败"):
                c.create_remote_directory("/a/b")

    def test_remote_file_exists_unconnected(self, mock_paramiko):
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
