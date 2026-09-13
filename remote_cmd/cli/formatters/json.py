"""
JSON 格式化器（稳定 schema，机器友好；v2.8）
"""

import json
from typing import Any, Optional

from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.service._types import BatchResult


def _single_payload(
    host_name: str,
    command: str,
    result: CommandResult,
    duration: Optional[float],
) -> dict[str, Any]:
    return {
        "host": host_name,
        "command": command,
        "success": result.success,
        "exit_code": result.exit_code,
        "duration": duration,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def render_single_json(
    host_name: str,
    command: str,
    result: CommandResult,
    duration: Optional[float] = None,
) -> str:
    """渲染单主机结果为 JSON（字段顺序固定，ensure_ascii=False）。"""
    return json.dumps(
        _single_payload(host_name, command, result, duration),
        ensure_ascii=False,
        indent=2,
    )


def render_batch_json(result: BatchResult) -> str:
    """渲染批量结果为 JSON。

    schema（稳定）::

        {
          "total": 3, "success": 2, "failed": 1, "duration": 1.42,
          "results": {
            "web1": {"success": true, "exit_code": 0, "duration": 0.1,
                      "stdout": "...", "stderr": "", "error": null,
                      "command": "uptime"},
            ...
          }
        }

    ``results`` 按主机名排序，保证与完成顺序无关的确定性输出。
    """
    results: dict[str, Any] = {}
    for host in sorted(result.results):
        host_result = result.results[host]
        results[host] = {
            "success": host_result.success,
            "exit_code": host_result.exit_code,
            "duration": host_result.duration,
            "stdout": host_result.stdout,
            "stderr": host_result.stderr,
            "error": host_result.error,
            "command": host_result.command,
        }
    payload = {
        "total": result.total,
        "success": result.success,
        "failed": result.failed,
        "duration": result.duration,
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


__all__ = ["render_batch_json", "render_single_json"]
