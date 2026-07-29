"""SSH 连接集成测试

使用本地 Mock SSH Server 测试完整的 SSH 连接生命周期。
标记为 integration，默认不执行：
    pytest -m integration
"""

import paramiko
import pytest

from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.utils.exceptions import SSHConnectionError


@pytest.mark.integration
class TestSSHConnectionIntegration:
    """完整的 SSH 连接集成测试"""

    TEST_CONFIG = dict(
        hostname="127.0.0.1",
        username="testuser",
        password="testpass",
        host_key_policy=paramiko.AutoAddPolicy(),
    )

    def test_connect_with_password(self, mock_ssh_server, integration_host):
        """密码认证连接成功"""
        config = ConnectionConfig(
            port=integration_host.port, **self.TEST_CONFIG
        )
        client = SSHClient(config)
        client.connect()
        assert client.is_connected()
        client.disconnect()

    def test_connect_and_disconnect(self, mock_ssh_server, integration_host):
        """连接后断开"""
        config = ConnectionConfig(
            port=integration_host.port, **self.TEST_CONFIG
        )
        client = SSHClient(config)
        client.connect()
        assert client.is_connected()
        client.disconnect()
        assert not client.is_connected()

    def test_connect_wrong_port(self, mock_ssh_server, integration_host):
        """错误端口应抛出 SSHConnectionError"""
        config = ConnectionConfig(port=9999, **self.TEST_CONFIG)
        client = SSHClient(config)
        with pytest.raises(SSHConnectionError):
            client.connect()
        client.disconnect()

    def test_connect_timeout(self):
        """超时连接应抛出 SSHConnectionError"""
        config = ConnectionConfig(
            hostname="203.0.113.1",
            username="test",
            password="test",
            port=22,
            timeout=1,
            host_key_policy=paramiko.AutoAddPolicy(),
        )
        client = SSHClient(config)
        with pytest.raises(SSHConnectionError):
            client.connect()
        client.disconnect()

    def test_context_manager(self, mock_ssh_server, integration_host):
        """上下文管理器自动关闭连接"""
        config = ConnectionConfig(
            port=integration_host.port, **self.TEST_CONFIG
        )
        with SSHClient(config) as client:
            assert client.is_connected()
        assert not client.is_connected()

    def test_double_disconnect_safe(self, mock_ssh_server, integration_host):
        """重复 disconnect 不抛异常"""
        config = ConnectionConfig(
            port=integration_host.port, **self.TEST_CONFIG
        )
        client = SSHClient(config)
        client.connect()
        client.disconnect()
        client.disconnect()
