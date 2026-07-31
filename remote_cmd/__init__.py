"""
Remote CMD - SSH 远程服务器管理工具

一个功能强大的 Python 库，用于管理远程服务器。
提供简洁的 API 用于 SSH 连接、命令执行和文件传输。

主要功能：
    - SSH 连接管理（支持密码和密钥认证）
    - 远程命令执行（包括 sudo 命令）
    - 文件上传和下载
    - 主机配置管理
    - 标签分类系统
    - 批量连接测试
    - 凭据加密存储
    - 结构化日志系统

快速开始：
    >>> from remote_cmd import SSHClient, HostManager
    >>> from remote_cmd.core.ssh_client import ConnectionConfig
    >>>
    >>> # 创建连接配置
    >>> config = ConnectionConfig(
    ...     hostname="192.168.1.100",
    ...     username="admin",
    ...     key_filename="~/.ssh/id_rsa"
    ... )
    >>>
    >>> # 执行远程命令
    >>> with SSHClient(config) as client:
    ...     result = client.execute("ls -la")
    ...     print(result.stdout)

新架构（推荐）:
    >>> from remote_cmd.repository.json_host_repository import JsonHostRepository
    >>> from remote_cmd.service.host_service import HostService
    >>>
    >>> repo = JsonHostRepository("hosts.json")
    >>> service = HostService(repo)
    >>> service.add_host(host)

命令行使用：
    $ remote-cmd host add server1 192.168.1.100 admin -k ~/.ssh/id_rsa
    $ remote-cmd host list
    $ remote-cmd run server1 "uptime"

更多信息：
    - GitHub: https://github.com/Vae-Scrooge/remote-cmd
    - 文档: 参见 docs/ 目录

Author: Vae-Scrooge
Version: 1.0.0
License: MIT
"""

__version__ = "1.0.0"
__author__ = "Vae-Scrooge"
__email__ = "vae-scrooge@example.com"
__license__ = "MIT"

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

# 向后兼容导出（原有 API）
# TODO: deprecated, remove in v2.0 —— AsyncSSHClient 与 ConnectionPool 已统一为
#       原生 asyncssh 实现（remote_cmd.core.async_ssh_client / async_connection_pool）
from remote_cmd.core.async_connection_pool import AsyncConnectionPool
from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.host import Host
from remote_cmd.core.host_manager import HostManager
from remote_cmd.core.ssh_client import SSHClient

# 兼容别名（历史名称）
NativeAsyncSSHClient = AsyncSSHClient
ConnectionPool = AsyncConnectionPool

# 新架构导出（推荐）
from remote_cmd.repository import HostRepository, JsonHostRepository

# Phase 2 新组件
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.service import (
    ChainCredentialProvider,
    CredentialProvider,
    EnvCredentialProvider,
    HostService,
    SSHService,
)
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.batch_executor import BatchExecutor, BatchHostResult, BatchResult
from remote_cmd.service.credential_provider import KeyringCredentialProvider
from remote_cmd.service.task_runner import Task, TaskRunner, TaskStatus
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.logging_utils import (
    SensitiveDataFilter,
    get_logger,
    setup_logging,
)

# 定义公开 API
__all__ = [
    # 原有导出（向后兼容）
    "SSHClient",
    "AsyncSSHClient",
    "ConnectionPool",
    "NativeAsyncSSHClient",
    "AsyncConnectionPool",
    "AsyncBatchExecutor",
    "HostManager",
    "Host",
    # 新架构导出
    "HostRepository",
    "JsonHostRepository",
    "HostService",
    "SSHService",
    "CredentialProvider",
    "EnvCredentialProvider",
    "ChainCredentialProvider",
    "CredentialEncryption",
    "setup_logging",
    "SensitiveDataFilter",
    "get_logger",
    # Phase 2 新组件
    "SqliteHostRepository",
    "BatchExecutor",
    "BatchResult",
    "BatchHostResult",
    "TaskRunner",
    "Task",
    "TaskStatus",
    "KeyringCredentialProvider",
    # 元信息
    "__version__",
    "__author__",
    "__license__",
]
