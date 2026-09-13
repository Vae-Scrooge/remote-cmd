"""
批量执行单主机策略（同步 / 异步 executor 共用，无 I/O 纯逻辑）

从 ``batch_executor.BatchExecutor`` 与 ``async_batch_executor.AsyncBatchExecutor``
的 ``_execute_on_host`` 中提取的纯构造 / 解析逻辑：

- ``build_connection_config``: 由 Host 构造 ConnectionConfig
- ``resolve_host_or_error``: 解析主机，失败时返回带错误信息的 BatchHostResult
- ``to_host_result``: 由 CommandResult 构造成功结果的 BatchHostResult

设计约定：
- 本模块不涉及任何 I/O（连接、命令执行、探活）；实际执行保留在各 executor 的
  重试循环内，本模块只提供无副作用的纯函数。
- 依赖 ``service._types``（不含业务执行器），避免与两个 executor 形成循环依赖。

用法:
    >>> from remote_cmd.service._host_runner import (
    ...     build_connection_config, resolve_host_or_error, to_host_result,
    ... )
"""

from typing import Optional, Union

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.service._types import BatchHostResult, OutputPolicy
from remote_cmd.service.host_service import HostService
from remote_cmd.utils.exceptions import ConfigError, ValidationError

# 输出截断标记：追加在被截断的输出流末尾（确定性、便于测试与用户识别）
OUTPUT_TRUNCATION_MARKER = "\n[output truncated: {omitted} bytes omitted]"

# host 解析结果：成功返回 Host，失败返回带错误信息的 BatchHostResult
ResolveOutcome = Union[Host, BatchHostResult]


def validate_max_output_bytes(value: Optional[int]) -> Optional[int]:
    """校验批量结果的单流输出上限并原样返回。

    Args:
        value: ``None``（不截断）或正整数（每台主机每个输出流保留的最大字节数）

    Returns:
        Optional[int]: 校验后的值

    Raises:
        ValidationError: 非 None、非正整数（bool 亦被拒绝，避免 True/False
            被当作 1/0 使用）
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"max_output_bytes must be None or a positive integer, got: {value!r}"
        )
    return value


def resolve_max_output_bytes(
    max_output_bytes: Optional[int],
    output_policy: Optional[OutputPolicy],
) -> Optional[int]:
    """解析执行器的输出保留配置，返回生效的 max_output_bytes。

    v2.5 引入 ``OutputPolicy`` 作为 ``max_output_bytes`` 的显式替代；
    两者只能传且最多传一个，防止歧义。

    Args:
        max_output_bytes: legacy 参数（与 ``output_policy`` 互斥）
        output_policy: ``OutputPolicy`` 实例（优先）

    Returns:
        Optional[int]: 生效的保留上限（``None`` 表示不限）

    Raises:
        ValidationError: 两者同时给出，或值非法
    """
    if max_output_bytes is not None and output_policy is not None:
        raise ValidationError(
            "ambiguous output policy: pass either max_output_bytes or "
            "output_policy, not both"
        )
    if output_policy is not None:
        return validate_max_output_bytes(output_policy.max_output_bytes)
    return validate_max_output_bytes(max_output_bytes)



def truncate_output(text: str, max_bytes: Optional[int]) -> str:
    """按 UTF-8 字节上限确定性地截断单个输出流。

    ``max_bytes`` 为 ``None`` 时原样返回（保持既有行为，绝不静默截断）。
    超过上限时按 UTF-8 字节切片（丢弃被切断的多字节序列尾部）并追加固定
    格式的截断标记，标记不计入 ``max_bytes``。截断只影响保留的输出文本，
    不改变命令的成功/失败语义与退出码。

    Args:
        text: 已解码的输出文本
        max_bytes: 保留的最大字节数（按 ``text.encode("utf-8")`` 度量），
            或 ``None`` 表示不截断

    Returns:
        str: 原文本或截断后的文本（含标记）
    """
    if max_bytes is None or not text:
        return text
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    prefix = encoded[:max_bytes].decode("utf-8", errors="ignore")
    # omitted 按“原始字节数 - 实际保留字节数”计算：UTF-8 边界处的
    # 残字节不会被错误计入保留量
    omitted = len(encoded) - len(prefix.encode("utf-8"))
    return prefix + OUTPUT_TRUNCATION_MARKER.format(omitted=omitted)


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
    - ``ConfigError`` -> error="profile resolution failed: ..."（v2.8；
      未知 profile / 仓库不支持 profile）
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
    except ConfigError as e:
        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=f"profile resolution failed: {e}",
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
    max_output_bytes: Optional[int] = None,
) -> BatchHostResult:
    """由命令结果构造成功的单主机结果。

    Args:
        host_name: 主机名
        command: 执行的命令
        cmd_result: 命令执行结果
        duration: 本次执行耗时（秒）
        max_output_bytes: 每个输出流保留的最大字节数；``None`` 表示
            保留完整输出（既有行为）。仅影响保留文本，不影响成功/失败

    Returns:
        BatchHostResult: 成功结果
    """
    return BatchHostResult(
        host=host_name,
        success=cmd_result.success,
        command=command,
        stdout=truncate_output(cmd_result.stdout, max_output_bytes),
        stderr=truncate_output(cmd_result.stderr, max_output_bytes),
        exit_code=cmd_result.exit_code,
        duration=duration,
    )
