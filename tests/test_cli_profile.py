"""CLI profile 子命令与 --format 集成测试（v2.8）。"""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from remote_cmd.cli.main import cli
from remote_cmd.core.ssh_client import CommandResult
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


class TestProfileCli:
    def test_add_list_show(self, runner, config_file):
        r = _invoke(
            runner,
            config_file,
            "profile",
            "add",
            "aws",
            "-u",
            "ec2-user",
            "-p",
            "2222",
            "-k",
            "/aws.pem",
            "-t",
            "cloud",
            "-t",
            "prod",
            "-d",
            "AWS fleet",
        )
        assert r.exit_code == 0, r.output

        r = _invoke(runner, config_file, "profile", "list")
        assert r.exit_code == 0
        assert "aws" in r.output
        assert "ec2-user" in r.output
        assert "2222" in r.output

        r = _invoke(runner, config_file, "profile", "show", "aws")
        assert r.exit_code == 0
        assert "aws.pem" in r.output
        assert "cloud, prod" in r.output

    def test_duplicate_add_fails(self, runner, config_file):
        _invoke(runner, config_file, "profile", "add", "aws")
        r = _invoke(runner, config_file, "profile", "add", "aws")
        assert r.exit_code == 1
        assert "already exists" in r.output

    def test_invalid_port_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "profile", "add", "aws", "-p", "99999")
        assert r.exit_code == 1
        assert "port" in r.output

    def test_show_unknown_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "profile", "show", "ghost")
        assert r.exit_code == 1
        assert "not found" in r.output

    def test_remove_flow(self, runner, config_file):
        _invoke(runner, config_file, "profile", "add", "aws")
        r = _invoke(runner, config_file, "profile", "remove", "aws", input="y\n")
        assert r.exit_code == 0
        assert "removed" in r.output

    def test_remove_referenced_requires_force(self, runner, config_file):
        _invoke(runner, config_file, "profile", "add", "aws")
        _invoke(
            runner, config_file, "host", "add", "web1", "10.0.0.1", "admin",
            "--profile", "aws", "-k", "/k",
        )

        r = _invoke(runner, config_file, "profile", "remove", "aws", input="y\n")
        assert r.exit_code == 1
        assert "still referenced" in r.output

        r = _invoke(runner, config_file, "profile", "remove", "aws", "--force", input="y\n")
        assert r.exit_code == 0


class TestHostAddWithProfile:
    def test_username_resolved_live_from_profile(self, runner, config_file, tmp_path):
        """P1 回归（审计）：--profile 省略 USERNAME 时不得物化 profile.username。"""
        from remote_cmd.repository.json_host_repository import JsonHostRepository
        from remote_cmd.service.profile_service import ProfileService

        _invoke(runner, config_file, "profile", "add", "aws", "-u", "ec2-user", "-p", "2222")
        r = _invoke(
            runner,
            config_file,
            "host",
            "add",
            "web1",
            "10.0.0.1",
            "--profile",
            "aws",
            "-k",
            "/k.pem",
        )
        assert r.exit_code == 0, r.output

        # 存储层：username 为空（live default），profile 引用保留
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        stored = repo.get("web1")
        assert stored.username == ""
        assert stored.profile == "aws"

        # 有效视图：解析出 profile.username 与 port
        r = _invoke(runner, config_file, "host", "show", "web1")
        assert "ec2-user" in r.output
        assert "2222" in r.output
        assert "Profile:    aws" in r.output

        # live default：更新 profile.username 后引用主机随之变化
        ProfileService(store=repo, host_repository=repo).update_profile(
            "aws", username="ubuntu"
        )
        r = _invoke(runner, config_file, "host", "show", "web1")
        assert "ubuntu" in r.output
        assert "ec2-user" not in r.output

    def test_explicit_username_is_host_override(self, runner, config_file, tmp_path):
        from remote_cmd.repository.json_host_repository import JsonHostRepository
        from remote_cmd.service.profile_service import ProfileService

        _invoke(runner, config_file, "profile", "add", "aws", "-u", "ec2-user")
        _invoke(
            runner, config_file, "host", "add", "web1", "10.0.0.1", "deploy",
            "--profile", "aws", "-k", "/k",
        )
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        assert repo.get("web1").username == "deploy"

        ProfileService(store=repo, host_repository=repo).update_profile(
            "aws", username="ubuntu"
        )
        r = _invoke(runner, config_file, "host", "show", "web1")
        assert "deploy" in r.output

    def test_profile_key_skips_password_prompt(self, runner, config_file):
        """profile 提供私钥时，host add 不再提示密码（无输入也不应失败）。"""
        _invoke(runner, config_file, "profile", "add", "aws", "-u", "ec2-user", "-k", "/aws.pem")
        r = _invoke(runner, config_file, "host", "add", "web1", "10.0.0.1", "--profile", "aws")
        assert r.exit_code == 0, r.output
        assert "added successfully" in r.output

    def test_explicit_port_wins_over_profile(self, runner, config_file):
        _invoke(runner, config_file, "profile", "add", "aws", "-u", "ec2-user", "-p", "2222")
        _invoke(
            runner, config_file, "host", "add", "web1", "10.0.0.1", "admin",
            "--profile", "aws", "-p", "2020", "-k", "/k.pem",
        )
        r = _invoke(runner, config_file, "host", "show", "web1")
        assert "2020" in r.output
        assert "2222" not in r.output

    def test_unknown_profile_fails(self, runner, config_file):
        r = _invoke(
            runner, config_file, "host", "add", "web1", "10.0.0.1", "admin",
            "--profile", "ghost", "-k", "/k.pem",
        )
        assert r.exit_code == 1
        assert "unknown profile" in r.output

    def test_missing_username_without_profile_fails(self, runner, config_file):
        r = _invoke(runner, config_file, "host", "add", "web1", "10.0.0.1", "-k", "/k.pem")
        assert r.exit_code == 1
        assert "USERNAME is required" in r.output

    def test_profile_tag_visible_and_filterable(self, runner, config_file):
        _invoke(runner, config_file, "profile", "add", "aws", "-u", "u", "-t", "cloud")
        _invoke(
            runner, config_file, "host", "add", "web1", "10.0.0.1", "--profile", "aws", "-k", "/k"
        )
        r = _invoke(runner, config_file, "host", "list", "-t", "cloud")
        assert "web1" in r.output


class TestFormatFlags:
    def _mock_connect(self):
        cm = MagicMock()
        client = MagicMock()
        client.execute.return_value = CommandResult("uptime", "ok", "", 0)
        cm.__enter__.return_value = client
        cm.__exit__.return_value = None
        return cm

    def test_run_format_json(self, runner, config_file):
        _invoke(runner, config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key")
        with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
            m.return_value = self._mock_connect()
            r = _invoke(runner, config_file, "run", "--format", "json", "srv", "uptime")
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["host"] == "srv"
        assert payload["stdout"] == "ok"
        assert payload["duration"] is not None

    def test_run_default_format_unchanged(self, runner, config_file):
        """默认 rich：纯 stdout 输出（无 JSON 包裹）。"""
        _invoke(runner, config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key")
        with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
            m.return_value = self._mock_connect()
            r = _invoke(runner, config_file, "run", "srv", "uptime")
        assert r.exit_code == 0
        assert r.output.strip() == "ok"

    def test_batch_run_format_json_no_progress(self, runner, config_file):
        batch = BatchResult(
            total=2,
            success=1,
            failed=1,
            duration=0.5,
            results={
                "h1": BatchHostResult(host="h1", success=True, command="c", stdout="ok"),
                "h2": BatchHostResult(host="h2", success=False, command="c", error="boom"),
            },
        )
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            mock_executor.return_value.execute.return_value = batch
            r = _invoke(runner, config_file, "batch-run", "h1", "h2", "uptime", "--format", "json")
        assert r.exit_code == 1  # 有失败主机
        payload = json.loads(r.output)
        assert payload["total"] == 2
        assert payload["results"]["h2"]["error"] == "boom"
        assert "Batch running" not in r.output
        assert "Progress" not in r.output

    def test_batch_run_format_table(self, runner, config_file):
        batch = BatchResult(
            total=1,
            success=1,
            failed=0,
            duration=0.1,
            results={"h1": BatchHostResult(host="h1", success=True, command="c", stdout="ok")},
        )
        with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor:
            mock_executor.return_value.execute.return_value = batch
            r = _invoke(runner, config_file, "batch-run", "h1", "uptime", "--format", "table")
        assert r.exit_code == 0
        assert "HOST" in r.output
        assert "h1" in r.output
