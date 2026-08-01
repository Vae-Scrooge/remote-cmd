"""
SSH 连接服务

封装 SSHClient 的连接管理和命令执行逻辑。
与 HostService 配合使用，不直接依赖 HostManager。

区别于 SSHClient:
    - SSHClient: 底层连接和命令执行
    - SSHService: 连接生命周期管理、重试、超时、健康检查
"""

import logging
from typing import Optional

from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig, SSHClient

logger = logging.getLogger(__name__)


class SSHService:
    """
    SSH 连接服务

    提供连接管理、命令执行和健康检查功能。
    支持重试和超时控制。
    """

    def __init__(self, timeout: int = 30):
        self._timeout = timeout

    def create_client(
        self,
        hostname: str,
        username: str,
        port: int = 22,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        known_hosts_file: Optional[str] = None,
    ) -> SSHClient:
        """
        创建并建立 SSH 连接

        Returns:
            已连接的 SSHClient 实例

        Raises:
            SSHConnectionError: 连接失败
        """
        config = ConnectionConfig(
            hostname=hostname,
            username=username,
            port=port,
            password=password,
            key_filename=key_filename,
            timeout=self._timeout,
            known_hosts_file=known_hosts_file,
        )
        client = SSHClient(config)
        return client.connect()

    def test_connection(
        self,
        hostname: str,
        username: str,
        port: int = 22,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
    ) -> bool:
        """
        测试主机连接是否正常

        Returns:
            bool: True if connected
        """
        try:
            with self.create_client(
                hostname=hostname,
                username=username,
                port=port,
                password=password,
                key_filename=key_filename,
            ) as client:
                return client.is_connected()
        except (OSError, Exception) as e:  # noqa: BLE001
            logger.debug(f"connection test failed {hostname}:{port}: {e}")
            return False

    def execute_command(
        self,
        hostname: str,
        username: str,
        command: str,
        port: int = 22,
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """
        在远程主机上执行命令

        Returns:
            CommandResult: 命令执行结果
        """
        with self.create_client(
            hostname=hostname,
            username=username,
            port=port,
            password=password,
            key_filename=key_filename,
        ) as client:
            return client.execute(command, timeout=timeout)
