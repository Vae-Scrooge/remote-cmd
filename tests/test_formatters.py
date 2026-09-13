"""格式化器测试（v2.8）：JSON 稳定 schema 与 table 渲染。"""

import json

import pytest

from remote_cmd.cli.formatters import (
    format_batch_result,
    format_single_result,
    render_batch_json,
    render_batch_table,
    render_single_json,
    render_single_table,
)
from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.service._types import BatchHostResult, BatchResult
from remote_cmd.utils.exceptions import ValidationError


def _single(**kwargs) -> CommandResult:
    defaults = {"command": "uptime", "stdout": "OK\n", "stderr": "", "exit_code": 0}
    defaults.update(kwargs)
    return CommandResult(**defaults)


def _batch() -> BatchResult:
    return BatchResult(
        total=3,
        success=2,
        failed=1,
        duration=1.42,
        results={
            "web1": BatchHostResult(
                host="web1", success=True, command="uptime", stdout="ok", duration=0.1
            ),
            "db1": BatchHostResult(
                host="db1",
                success=False,
                command="uptime",
                error="Connection refused",
                exit_code=-1,
                duration=0.2,
            ),
            "web2": BatchHostResult(
                host="web2", success=True, command="uptime", stdout="ok2", duration=0.3
            ),
        },
    )


class TestSingleJson:
    def test_schema_and_order(self):
        text = render_single_json("web1", "uptime", _single(), 0.42)
        payload = json.loads(text)
        assert list(payload) == [
            "host",
            "command",
            "success",
            "exit_code",
            "duration",
            "stdout",
            "stderr",
        ]
        assert payload["host"] == "web1"
        assert payload["success"] is True
        assert payload["duration"] == 0.42

    def test_duration_none(self):
        payload = json.loads(render_single_json("h", "c", _single(), None))
        assert payload["duration"] is None

    def test_unicode_not_escaped(self):
        text = render_single_json("主机", "echo 你好", _single(stdout="你好"), None)
        assert "你好" in text
        assert json.loads(text)["stdout"] == "你好"


class TestBatchJson:
    def test_schema_and_sorted_keys(self):
        payload = json.loads(render_batch_json(_batch()))
        assert list(payload) == ["total", "success", "failed", "duration", "results"]
        assert list(payload["results"]) == ["db1", "web1", "web2"]  # 排序确定性
        assert payload["results"]["db1"]["error"] == "Connection refused"
        assert payload["results"]["db1"]["success"] is False

    def test_roundtrip_parse(self):
        text = format_batch_result(_batch(), "json")
        json.loads(text)  # 可解析


class TestTable:
    def test_single_table_sections(self):
        text = render_single_table("web1", "uptime", _single(), 0.42)
        assert "HOST" in text and "COMMAND" in text and "STATUS" in text
        assert "web1" in text and "ok" in text and "0.42s" in text
        assert "--- stdout ---" in text
        assert "--- stderr ---" in text

    def test_batch_table_rows_and_error_column(self):
        text = render_batch_table(_batch())
        lines = text.splitlines()
        assert lines[0].startswith("HOST")
        assert "db1" in text and "Connection refused" in text
        # 排序确定性（表体顺序与 JSON 一致）
        body = [ln for ln in lines if ln and not ln.startswith(("HOST", "Total"))]
        assert body[0].startswith("db1")
        assert "Total: 3" in text and "Failed: 1" in text

    def test_show_failures_filters(self):
        text = render_batch_table(_batch(), show_failures=True)
        assert "db1" in text
        assert "web1" not in text

    def test_truncation_marker(self):
        long_line = "x" * 200
        result = BatchResult(
            total=1,
            success=1,
            failed=0,
            duration=0.1,
            results={
                "h": BatchHostResult(host="h", success=True, command="c", stdout=long_line)
            },
        )
        text = render_batch_table(result)
        assert "…" in text
        data_line = [ln for ln in text.splitlines() if ln.startswith("h ")][0]
        assert len(data_line) < 100  # 单行截断生效

    def test_empty_batch(self):
        empty = BatchResult(total=0, success=0, failed=0, duration=0.0, results={})
        assert "No hosts." in render_batch_table(empty)


class TestDispatch:
    def test_single_dispatch_json_table(self):
        assert json.loads(format_single_result("h", "c", _single(), "json", 0.1))["host"] == "h"
        assert "HOST" in format_single_result("h", "c", _single(), "table", 0.1)

    def test_batch_dispatch_json_table(self):
        assert "results" in json.loads(format_batch_result(_batch(), "json"))
        assert "HOST" in format_batch_result(_batch(), "table")

    @pytest.mark.parametrize("fmt", ["rich", "xml", ""])
    def test_unsupported_format_raises(self, fmt):
        with pytest.raises(ValidationError, match="unsupported machine format"):
            format_single_result("h", "c", _single(), fmt)
        with pytest.raises(ValidationError, match="unsupported machine format"):
            format_batch_result(_batch(), fmt)
