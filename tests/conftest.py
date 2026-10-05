"""
pytest 共享配置和 Mock SSH Server

提供 MockSSHServer 用于集成测试，无需真实 SSH 连接。
"""

from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# Mock SSH Server
# ============================================================================


class MockSSHServer:
    """
    模拟 SSH 服务器，用于测试 SSHClient 和 SSHService。

    模拟 paramiko.SSHClient 的行为，支持：
    - 连接/断开
    - 命令执行
    - 连接测试（成功/失败模式）
    """

    def __init__(self, hostname="test-host", should_fail=False):
        self.hostname = hostname
        self.should_fail = should_fail
        self.is_connected_flag = False
        self.executed_commands = []
        self.connect_count = 0

    def create_mock_ssh(self) -> MagicMock:
        """创建一个模拟的 paramiko.SSHClient"""
        mock_ssh = MagicMock()

        if self.should_fail:
            mock_ssh.connect.side_effect = Exception("Connection refused")
        else:
            mock_ssh.connect.side_effect = lambda **_: setattr(self, "is_connected_flag", True)

        mock_ssh.get_transport.return_value = MagicMock()
        mock_ssh.get_transport.return_value.is_active.return_value = True
        mock_ssh.close.side_effect = lambda: setattr(self, "is_connected_flag", False)

        return mock_ssh


@pytest.fixture
def mock_ssh_client():
    """Fixture: 自动 mock paramiko.SSHClient"""
    server = MockSSHServer()
    with patch("remote_cmd.core.ssh_client.paramiko.SSHClient") as mock_class:
        mock_class.return_value = server.create_mock_ssh()
        yield mock_class


@pytest.fixture
def mock_ssh_fail():
    """Fixture: mock 连接失败"""
    server = MockSSHServer(should_fail=True)
    with patch("remote_cmd.core.ssh_client.paramiko.SSHClient") as mock_class:
        mock_class.return_value = server.create_mock_ssh()
        yield mock_class


@pytest.fixture
def temp_db_path(tmp_path):
    """Fixture: 临时 SQLite 数据库路径"""
    return str(tmp_path / "test_hosts.db")


@pytest.fixture
def temp_json_path(tmp_path):
    """Fixture: 临时 JSON 文件路径"""
    return str(tmp_path / "test_hosts.json")


# ============================================================================
# 异步测试辅助
# ============================================================================

def make_cli_runner():
    """创建 stderr 分离的 Click CliRunner（兼容 Click <8.2 与 >=8.2）。

    Click 8.2 移除了 ``CliRunner(mix_stderr=...)`` 参数，且默认即分离
    stderr（与旧版 ``mix_stderr=False`` 语义一致）。此处按签名探测，
    保证 ``click>=8.0`` 全范围可用。
    """
    import inspect

    from click.testing import CliRunner

    if "mix_stderr" in inspect.signature(CliRunner.__init__).parameters:
        return CliRunner(mix_stderr=False)
    return CliRunner()
