"""SSH 连接服务测试"""

import logging
from unittest.mock import MagicMock, patch

from remote_cmd.core.ssh_client import ConnectionConfig, SSHConnectionError
from remote_cmd.service.ssh_service import SSHService


class TestCreateClient:
    """创建客户端测试"""

    @patch("remote_cmd.service.ssh_service.SSHClient")
    def test_creates_connected_client(self, mock_ssh_client_cls):
        """测试：创建并连接客户端，返回已连接实例"""
        mock_client = MagicMock()
        mock_ssh_client_cls.return_value = mock_client
        mock_client.connect.return_value = mock_client

        service = SSHService(timeout=15)
        client = service.create_client(
            hostname="10.0.0.1",
            username="admin",
            port=2222,
            password="pwd",
            key_filename="/tmp/key",
            known_hosts_file="/tmp/known_hosts",
        )

        mock_ssh_client_cls.assert_called_once()
        config: ConnectionConfig = mock_ssh_client_cls.call_args.args[0]
        assert config.hostname == "10.0.0.1"
        assert config.username == "admin"
        assert config.port == 2222
        assert config.password == "pwd"
        assert config.key_filename == "/tmp/key"
        assert config.timeout == 15
        assert config.known_hosts_file == "/tmp/known_hosts"
        mock_client.connect.assert_called_once()
        assert client is mock_client

    @patch("remote_cmd.service.ssh_service.SSHClient")
    def test_uses_default_port_and_timeout(self, mock_ssh_client_cls):
        """测试：默认端口与超时"""
        mock_ssh_client_cls.return_value.connect.return_value = MagicMock()

        SSHService().create_client(hostname="h", username="u")

        config: ConnectionConfig = mock_ssh_client_cls.call_args.args[0]
        assert config.port == 22
        assert config.timeout == 30
        assert config.password is None
        assert config.key_filename is None

    @patch("remote_cmd.service.ssh_service.SSHClient")
    def test_connect_error_propagates(self, mock_ssh_client_cls):
        """测试：连接失败时抛出 SSHConnectionError"""
        mock_client = MagicMock()
        mock_ssh_client_cls.return_value = mock_client
        mock_client.connect.side_effect = SSHConnectionError("authentication failed")

        service = SSHService()
        try:
            service.create_client(hostname="h", username="u")
            raise AssertionError("应抛出 SSHConnectionError")
        except SSHConnectionError as e:
            assert "authentication failed" in str(e)


class TestTestConnection:
    """连接测试功能测试"""

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_success_returns_true(self, mock_create_client):
        """测试：连接成功且已连接时返回 True"""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = True
        mock_create_client.return_value.__enter__.return_value = mock_client

        service = SSHService()
        assert service.test_connection("10.0.0.1", "admin", password="pwd") is True
        mock_client.is_connected.assert_called_once()

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_not_connected_returns_false(self, mock_create_client):
        """测试：连接建立但未就绪时返回 False"""
        mock_client = MagicMock()
        mock_client.is_connected.return_value = False
        mock_create_client.return_value.__enter__.return_value = mock_client

        service = SSHService()
        assert service.test_connection("h", "u") is False

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_error_returns_false_and_logs(self, mock_create_client, caplog):
        """测试：连接失败时返回 False 并记录调试日志"""
        mock_create_client.side_effect = SSHConnectionError("拒绝连接")
        with caplog.at_level(logging.DEBUG, logger="remote_cmd.service.ssh_service"):
            service = SSHService()
            assert service.test_connection("h", "u", port=2222) is False
        assert "connection test failed" in caplog.text

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_generic_exception_returns_false(self, mock_create_client):
        """测试：任意异常都返回 False"""
        mock_create_client.side_effect = RuntimeError("意外错误")
        service = SSHService()
        assert service.test_connection("h", "u") is False


class TestExecuteCommand:
    """命令执行测试"""

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_executes_and_returns_result(self, mock_create_client):
        """测试：在连接客户端上执行命令并返回结果"""
        mock_client = MagicMock()
        result = MagicMock()
        result.stdout = "ok"
        result.exit_code = 0
        mock_client.execute.return_value = result
        mock_create_client.return_value.__enter__.return_value = mock_client

        service = SSHService()
        returned = service.execute_command(
            "10.0.0.1", "admin", "echo hi", password="pwd", timeout=5
        )

        mock_client.execute.assert_called_once_with("echo hi", timeout=5)
        assert returned.stdout == "ok"
        assert returned.exit_code == 0

    @patch("remote_cmd.service.ssh_service.SSHService.create_client")
    def test_execute_default_timeout_none(self, mock_create_client):
        """测试：未指定超时时传 None"""
        mock_client = MagicMock()
        mock_create_client.return_value.__enter__.return_value = mock_client

        service = SSHService()
        service.execute_command("h", "u", "ls")

        mock_client.execute.assert_called_once_with("ls", timeout=None)
