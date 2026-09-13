"""CLI recipe 子命令测试（v2.9）。"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from remote_cmd.cli.main import cli
from remote_cmd.service._types import BatchHostResult, BatchResult


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(f"hosts_file: {tmp_path / 'hosts.json'}", encoding="utf-8")
    return str(path)


def _invoke(runner, config_file, *args, **kwargs):
    return runner.invoke(cli, ["--config", config_file, *args], **kwargs)


def _ok_batch() -> BatchResult:
    return BatchResult(
        total=1,
        success=1,
        failed=0,
        duration=0.1,
        results={"h1": BatchHostResult(host="h1", success=True, command="c", stdout="ok")},
    )


class TestRecipeCrudCli:
    def test_add_list_show(self, runner, config_file):
        r = _invoke(
            runner, config_file, "recipe", "add", "deploy",
            "-c", "deploy {{ pkg }}",
            "-V", "pkg=app",
            "-E", "TOKEN",
            "-d", "deploy app", "-t", "ops",
        )
        assert r.exit_code == 0, r.output

        r = _invoke(runner, config_file, "recipe", "list")
        assert "deploy" in r.output
        assert "pkg:shell_arg" in r.output
        assert "TOKEN:env" in r.output

        r = _invoke(runner, config_file, "recipe", "show", "deploy")
        assert "deploy {{ pkg }}" in r.output
        assert "pkg (shell_arg, optional, default='app')" in r.output
        assert "TOKEN (env, required)" in r.output

    def test_add_undeclared_placeholder_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "recipe", "add", "bad", "-c", "echo {{ ghost }}")
        assert r.exit_code == 1
        assert "undeclared" in r.output

    def test_duplicate_variable_across_types_rejected(self, runner, config_file):
        """审计修复：同名变量不得同时声明为 shell_arg 与 env（会改变安全语义）。"""
        r = _invoke(
            runner, config_file, "recipe", "add", "bad",
            "-c", "echo {{ TOKEN }}", "-V", "TOKEN", "-E", "TOKEN",
        )
        assert r.exit_code == 1
        assert "both shell_arg and env" in r.output
        assert "TOKEN" in r.output

    def test_duplicate_shell_variable_last_wins(self, runner, config_file):
        """同类型重复声明保持 last-wins（不扩大修复范围）。"""
        r = _invoke(
            runner, config_file, "recipe", "add", "r",
            "-c", "echo {{ x }}", "-V", "x=first", "-V", "x=second",
        )
        assert r.exit_code == 0, r.output
        r = _invoke(runner, config_file, "recipe", "show", "r")
        assert "default='second'" in r.output

    def test_duplicate_env_variable_last_wins(self, runner, config_file):
        r = _invoke(
            runner, config_file, "recipe", "add", "r",
            "-c", "echo {{ T }}", "-E", "T=first", "-E", "T=second",
        )
        assert r.exit_code == 0, r.output
        r = _invoke(runner, config_file, "recipe", "show", "r")
        assert "default='second'" in r.output

    def test_duplicate_add_fails(self, runner, config_file):
        _invoke(runner, config_file, "recipe", "add", "r", "-c", "uptime")
        r = _invoke(runner, config_file, "recipe", "add", "r", "-c", "uptime")
        assert r.exit_code == 1
        assert "already exists" in r.output

    def test_remove_flow(self, runner, config_file):
        _invoke(runner, config_file, "recipe", "add", "r", "-c", "uptime")
        r = _invoke(runner, config_file, "recipe", "remove", "r", input="y\n")
        assert r.exit_code == 0
        assert "removed" in r.output

    def test_show_unknown_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "recipe", "show", "ghost")
        assert r.exit_code == 1
        assert "not found" in r.output


class TestRecipeRunCli:
    def test_run_renders_shell_arg_safely(self, runner, config_file):
        _invoke(
            runner, config_file, "recipe", "add", "echo",
            "-c", "printf '%s' {{ msg }}", "-V", "msg",
        )
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            mock_executor.return_value.execute.return_value = _ok_batch()
            r = _invoke(
                runner, config_file, "recipe", "run", "echo", "h1",
                "-V", "msg=a;b",
            )
        assert r.exit_code == 0, r.output
        kwargs = mock_executor.return_value.execute.call_args.kwargs
        assert kwargs["command"] == "printf '%s' 'a;b'"
        assert kwargs["environment"] is None

    def test_run_env_type_passes_environment(self, runner, config_file):
        _invoke(
            runner, config_file, "recipe", "add", "env-r",
            "-c", "printf '%s' {{ TOKEN }}", "-E", "TOKEN",
        )
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            mock_executor.return_value.execute.return_value = _ok_batch()
            r = _invoke(
                runner, config_file, "recipe", "run", "env-r", "h1",
                "-V", "TOKEN=s3cr3t",
            )
        assert r.exit_code == 0, r.output
        kwargs = mock_executor.return_value.execute.call_args.kwargs
        assert kwargs["command"] == "printf '%s' \"$TOKEN\""
        assert kwargs["environment"] == {"TOKEN": "s3cr3t"}

    def test_run_missing_required_fails_before_execution(self, runner, config_file):
        _invoke(runner, config_file, "recipe", "add", "r", "-c", "echo {{ a }}", "-V", "a")
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            r = _invoke(runner, config_file, "recipe", "run", "r", "h1")
        assert r.exit_code == 1
        assert "missing required" in r.output
        mock_executor.assert_not_called()

    def test_run_unknown_variable_fails(self, runner, config_file):
        _invoke(runner, config_file, "recipe", "add", "r", "-c", "uptime")
        r = _invoke(runner, config_file, "recipe", "run", "r", "h1", "-V", "zzz=1")
        assert r.exit_code == 1
        assert "unknown variable" in r.output

    def test_run_format_json(self, runner, config_file):
        _invoke(runner, config_file, "recipe", "add", "r", "-c", "uptime")
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            mock_executor.return_value.execute.return_value = _ok_batch()
            r = _invoke(
                runner, config_file, "recipe", "run", "r", "h1", "--format", "json",
            )
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["total"] == 1
        assert payload["results"]["h1"]["success"] is True
        assert "Recipe 'r'" not in r.output  # 机器格式无装饰输出

    def test_run_unknown_recipe_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "recipe", "run", "ghost", "h1")
        assert r.exit_code == 1
        assert "not found" in r.output
