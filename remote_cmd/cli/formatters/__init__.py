"""
执行结果格式化层（CLI，v2.8）

职责边界：
- 本层只消费 ``CommandResult`` / ``BatchResult`` 数据对象并渲染字符串，
  **绝不进入执行内核**（BatchExecutor / 连接池等不感知输出格式）。
- ``rich`` 格式（默认）保留在 CLI 原有渲染路径（含颜色与进度条），
  本包提供 ``json`` / ``table`` 两种机器友好格式。
- JSON 输出为稳定 schema（字段与顺序固定），便于脚本消费。

用法:
    >>> from remote_cmd.cli.formatters import format_batch_result
    >>> text = format_batch_result(batch_result, fmt="json")  # doctest: +SKIP
"""

from remote_cmd.cli.formatters.base import format_batch_result, format_single_result
from remote_cmd.cli.formatters.json import render_batch_json, render_single_json
from remote_cmd.cli.formatters.table import render_batch_table, render_single_table

__all__ = [
    "format_batch_result",
    "format_single_result",
    "render_batch_json",
    "render_batch_table",
    "render_single_json",
    "render_single_table",
]
