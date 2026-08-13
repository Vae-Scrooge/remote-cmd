"""
批量执行单主机策略（同步 / 异步 executor 共用，无 I/O 纯逻辑）

从 ``batch_executor.BatchExecutor`` 与 ``async_batch_executor.AsyncBatchExecutor``
的 ``_execute_on_host`` 中提取的纯构造 / 解析逻辑：

- ``HostExecutionPolicy``: 单主机执行参数（超时 / 重试次数 / 重试间隔）
- ``build_connection_config``: 由 Host 构造 ConnectionConfig
- ``resolve_host_or_error``: 解析主机，失败时返回带错误信息的 BatchHostResult
- ``to_host_result``: 由 CommandResult 构造成功结果的 BatchHostResult

设计约定：
- 本模块不涉及任何 I/O（连接、命令执行、探活）；实际执行保留在各 executor 的
  重试循环内，本模块只提供无副作用的纯函数。
- 依赖 ``service._types``（不含业务执行器），避免与两个 executor 形成循环依赖。

用法:
    >>> from remote_cmd.service._host_runner import (
    ...     HostExecutionPolicy, build_connection_config, resolve_host_or_error,
    ...     to_host_result,
    ... )
"""

from dataclasses import dataclass
from typing import Union

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.service._types import BatchHostResult
from remote_cmd.service.host_service import HostService

# host 解析结果：成功返回 Host，失败返回带错误信息的 BatchHostResult
ResolveOutcome = Union[Host, BatchHostResult]


@dataclass
class HostExecutionPolicy:
    """单主机执行参数。

    Attributes:
        command_timeout: 单条命令超时（秒）
        retry_count: 失败重试次数
        retry_delay: 重试间隔（秒）
    """

    command_timeout: int
    retry_count: int = 0
    retry_delay: float = 1.0


def build_connection_config(host: Host, timeout: int) -> ConnectionConfig:
    """由主机配置构造 SSH 连接配置。

    Args:
        host: 已解析（含解密凭据）的主机
        timeout: 命令超时（秒）

    Returns:
        ConnectionConfig: 可直接用于 SSH 客户端的连接配置
    """
    return ConnectionConfig(
        hostname=host.hostname,
        username=host.username,
        port=host.port,
        password=host.password,
        key_filename=host.key_filename,
        timeout=timeout,
    )


def resolve_host_or_error(
    host_service: HostService,
    host_name: str,
    command: str,
) -> ResolveOutcome:
    """解析主机；失败时返回带错误信息的 BatchHostResult。

    错误映射（与两个 executor 的历史行为一致）：
    - ``KeyError`` -> error="host not found: ..."
    - ``RuntimeError`` / ``OSError`` -> error="host resolution failed: ..."

    Args:
        host_service: 主机服务（提供 resolve_host）
        host_name: 主机名
        command: 执行的命令（用于失败结果的 command 字段）

    Returns:
        Host 或携带错误信息的 BatchHostResult
    """
    try:
        return host_service.resolve_host(host_name)
    except KeyError as e:
        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=f"host not found: {e}",
        )
    except (RuntimeError, OSError) as e:
        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=f"host resolution failed: {e}",
        )


def to_host_result(
    host_name: str,
    command: str,
    cmd_result: CommandResult,
    duration: float,
) -> BatchHostResult:
    """由命令结果构造成功的单主机结果。

    Args:
        host_name: 主机名
        command: 执行的命令
        cmd_result: 命令执行结果
        duration: 本次执行耗时（秒）

    Returns:
        BatchHostResult: 成功结果
    """
    return BatchHostResult(
        host=host_name,
        success=cmd_result.success,
        command=command,
        stdout=cmd_result.stdout,
        stderr=cmd_result.stderr,
        exit_code=cmd_result.exit_code,
        duration=duration,
    )
