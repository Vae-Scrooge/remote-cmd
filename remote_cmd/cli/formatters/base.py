"""
格式化器分发与校验（CLI，v2.8）
"""

from typing import Optional

from remote_cmd.cli.formatters.json import render_batch_json, render_single_json
from remote_cmd.cli.formatters.table import render_batch_table, render_single_table
from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.service._types import BatchResult
from remote_cmd.utils.exceptions import ValidationError

#: CLI ``--format`` 可选值（rich 由 CLI 默认渲染路径处理）
MACHINE_FORMATS = ("json", "table")
FORMAT_CHOICES = ("rich", "json", "table")


def format_single_result(
    host_name: str,
    command: str,
    result: CommandResult,
    fmt: str,
    duration: Optional[float] = None,
) -> str:
    """渲染单主机执行结果为 ``fmt`` 字符串。

    Args:
        host_name: 主机名
        command: 执行的命令
        result: 命令结果
        fmt: ``json`` 或 ``table``
        duration: 可选耗时（秒），JSON 中作为 ``duration`` 字段

    Raises:
        ValidationError: fmt 不是机器格式
    """
    if fmt == "json":
        return render_single_json(host_name, command, result, duration)
    if fmt == "table":
        return render_single_table(host_name, command, result, duration)
    raise ValidationError(f"unsupported machine format: {fmt!r} (expected json/table)")


def format_batch_result(
    result: BatchResult,
    fmt: str,
    show_failures: bool = False,
) -> str:
    """渲染批量执行结果为 ``fmt`` 字符串。

    Args:
        result: 批量结果
        fmt: ``json`` 或 ``table``
        show_failures: table 格式下仅显示失败主机（JSON 始终完整）

    Raises:
        ValidationError: fmt 不是机器格式
    """
    if fmt == "json":
        return render_batch_json(result)
    if fmt == "table":
        return render_batch_table(result, show_failures=show_failures)
    raise ValidationError(f"unsupported machine format: {fmt!r} (expected json/table)")


__all__ = [
    "FORMAT_CHOICES",
    "MACHINE_FORMATS",
    "format_batch_result",
    "format_single_result",
]
