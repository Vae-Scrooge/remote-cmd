"""
Table 格式化器（纯文本对齐，无颜色；v2.8）
"""

from typing import Optional

from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.service._types import BatchResult

# 单元格最大宽度（字符）；超出截断并追加省略号
_MAX_CELL = 60
_ELLIPSIS = "…"


def _shorten(text: str, limit: int = _MAX_CELL) -> str:
    """单元格内容单行化 + 截断（表格不展开多行输出）。"""
    first_line = (text or "").splitlines()[0] if text else ""
    if len(first_line) <= limit:
        return first_line
    return first_line[: limit - 1] + _ELLIPSIS


def _render_rows(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for row in rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


def render_single_table(
    host_name: str,
    command: str,
    result: CommandResult,
    duration: Optional[float] = None,
) -> str:
    """渲染单主机结果：摘要表 + stdout/stderr 分节。"""
    duration_text = f"{duration:.2f}s" if duration is not None else "-"
    summary = _render_rows(
        ["HOST", "COMMAND", "STATUS", "EXIT", "DURATION"],
        [[host_name, _shorten(command), "ok" if result.success else "fail", str(result.exit_code), duration_text]],
    )
    parts = [summary, "", "--- stdout ---", result.stdout.rstrip("\n"), "", "--- stderr ---", result.stderr.rstrip("\n")]
    return "\n".join(parts)


def render_batch_table(result: BatchResult, show_failures: bool = False) -> str:
    """渲染批量结果表：HOST/STATUS/EXIT/DURATION/OUTPUT。

    输出列优先展示错误信息（失败主机），否则展示 stdout 首行；
    ``show_failures=True`` 时仅渲染失败主机。
    """
    hosts = sorted(result.results)
    if show_failures:
        hosts = [h for h in hosts if not result.results[h].success]

    rows: list[list[str]] = []
    for host in hosts:
        r = result.results[host]
        output = r.error if (r.error and not r.success) else r.stdout
        rows.append(
            [
                host,
                "ok" if r.success else "fail",
                str(r.exit_code),
                f"{r.duration:.2f}s",
                _shorten(output or ""),
            ]
        )

    header = _render_rows(["HOST", "STATUS", "EXIT", "DURATION", "OUTPUT"], rows)
    summary = (
        f"Total: {result.total}  Succeeded: {result.success}  "
        f"Failed: {result.failed}  Duration: {result.duration:.2f}s"
    )
    if not rows and not show_failures:
        return f"{header}\n\nNo hosts.\n{summary}"
    return f"{header}\n\n{summary}"


__all__ = ["render_batch_table", "render_single_table"]
