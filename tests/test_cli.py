"""CLI 命令行接口单元测试

使用 Click 的 CliRunner 测试命令行解析和输出。
SSH 连接部分使用 mock 避免真实连接。
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from remote_cmd import __version__
from remote_cmd.cli.main import cli
from remote_cmd.core.ssh_client import CommandResult


@pytest.fixture
def runner():
    """提供 Click CliRunner 实例"""
    return CliRunner()


@pytest.fixture
def hosts_file(tmp_path):
    """生成临时的 hosts.json 文件路径"""
    return str(tmp_path / "hosts.json")


@pytest.fixture
def config_file(tmp_path):
    """生成临时的 config.yaml 文件路径"""
    path = tmp_path / "config.yaml"
    path.write_text(f"hosts_file: {tmp_path / 'hosts.json'}", encoding="utf-8")
    return str(path)


# ============================================================================
# 存储引擎自动切换测试
# ============================================================================


class TestStorageBackend:
    """测试：基于扩展名/显式配置的存储引擎自动切换"""

    def _make_config(self, tmp_path, hosts_file_name, backend=None):
        """生成指向指定 hosts 文件的配置，可选显式 storage_backend"""
        cfg = {"hosts_file": str(tmp_path / hosts_file_name)}
        if backend is not None:
            cfg["storage_backend"] = backend
        path = tmp_path / f"cfg_{hosts_file_name.replace('.', '_')}.yaml"
        path.write_text(
            "\n".join(f"{k}: {v}" for k, v in cfg.items()),
            encoding="utf-8",
        )
        return str(path)

    def test_json_extension_uses_json(self, runner, tmp_path):
        """测试：hosts 文件为 .json 时使用 JSON 存储"""
        config_file = self._make_config(tmp_path, "hosts.json")
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.build_repository") as mock_build:
                runner.invoke(cli, ["--config", config_file, "host", "list"])
            _, kwargs = mock_build.call_args
            assert kwargs["storage_backend"] is None
            assert str(tmp_path / "hosts.json") in kwargs["filepath"]

    def test_explicit_sqlite_backend_passed(self, runner, tmp_path):
        """测试：显式 storage_backend 透传给仓库构建"""
        config_file = self._make_config(tmp_path, "hosts.json", backend="sqlite")
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.build_repository") as mock_build:
                runner.invoke(cli, ["--config", config_file, "host", "list"])
            _, kwargs = mock_build.call_args
            assert kwargs["storage_backend"] == "sqlite"

    def test_sqlite_round_trip_add_and_list(self, runner, tmp_path):
        """测试：使用 .db 文件添加并列出主机（真实 SQLite 端到端）"""
        config_file = self._make_config(tmp_path, "hosts.db")
        with runner.isolated_filesystem():
            add = runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "srv1",
                    "10.0.0.1",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            assert add.exit_code == 0, add.output
            result = runner.invoke(cli, ["--config", config_file, "host", "list"])
            assert result.exit_code == 0
            assert "srv1" in result.output

    def test_hosts_file_override_takes_precedence(self, runner, tmp_path):
        """测试：--hosts-file 覆盖配置文件中的 hosts_file，扩展名推断后端"""
        # 配置指向 .json，CLI 用 --hosts-file 覆盖为 .db
        config_file = self._make_config(tmp_path, "hosts.json")
        override_path = str(tmp_path / "override.db")
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.build_repository") as mock_build:
                runner.invoke(
                    cli,
                    ["--config", config_file, "--hosts-file", override_path, "host", "list"],
                )
            assert mock_build.called
            _, kwargs = mock_build.call_args
            assert kwargs["filepath"] == override_path
            # 未显式指定 storage_backend，交给工厂按 .db 扩展名推断
            assert kwargs["storage_backend"] is None

    def test_hosts_file_override_json_inferred(self, runner, tmp_path):
        """测试：--hosts-file 指向 .json 且无配置文件时使用 JSON 后端"""
        override_path = str(tmp_path / "runtime.json")
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.build_repository") as mock_build:
                runner.invoke(cli, ["--hosts-file", override_path, "host", "list"])
            assert mock_build.called
            _, kwargs = mock_build.call_args
            assert kwargs["filepath"] == override_path
            assert kwargs["storage_backend"] is None

    def test_hosts_file_override_sqlite_round_trip(self, runner, tmp_path):
        """测试：仅凭 --hosts-file hosts.db 即可运行时切换到 SQLite（端到端）"""
        db_path = str(tmp_path / "runtime_hosts.db")
        with runner.isolated_filesystem():
            add = runner.invoke(
                cli,
                [
                    "--hosts-file",
                    db_path,
                    "host",
                    "add",
                    "srv1",
                    "10.0.0.1",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            assert add.exit_code == 0, add.output
            result = runner.invoke(cli, ["--hosts-file", db_path, "host", "list"])
            assert result.exit_code == 0, result.output
            assert "srv1" in result.output
            # 确实生成了 SQLite 数据库文件而非 JSON
            assert os.path.exists(db_path)


# ============================================================================
# host add 命令测试
# ============================================================================


class TestHostAdd:
    """测试 host add 命令"""

    def test_add_with_key_success(self, runner, config_file):
        """测试：使用 SSH 密钥添加主机成功"""
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "web-server",
                    "192.168.1.10",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            assert result.exit_code == 0
            assert "added successfully" in result.output

    def test_add_with_env_password(self, runner, config_file):
        """测试：使用环境变量密码添加主机（推荐做法）"""
        with runner.isolated_filesystem():
            env = {"REMOTE_CMD_PASSWORD": "secret123"}
            result = runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "db-server",
                    "10.0.0.1",
                    "root",
                ],
                env=env,
            )
            assert result.exit_code == 0
            assert "added successfully" in result.output

    def test_add_duplicate_fails(self, runner, config_file):
        """测试：添加同名主机应报错"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "dup-host",
                    "1.2.3.4",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            result = runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "dup-host",
                    "5.6.7.8",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            assert result.exit_code != 0
            assert "already exists" in result.output


# ============================================================================
# host list 命令测试
# ============================================================================


class TestHostList:
    """测试 host list 命令"""

    def test_list_empty(self, runner, config_file):
        """测试：没有主机时列出应为空"""
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--config", config_file, "host", "list"])
            assert result.exit_code == 0
            assert "No hosts configured" in result.output

    def test_list_with_hosts(self, runner, config_file):
        """测试：列出已添加的主机"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "srv1",
                    "10.0.0.1",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            result = runner.invoke(cli, ["--config", config_file, "host", "list"])
            assert result.exit_code == 0
            assert "srv1" in result.output
            assert "10.0.0.1" in result.output

    def test_list_filter_by_tag(self, runner, config_file):
        """测试：按标签筛选主机"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "prod-web",
                    "10.0.0.1",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                    "-t",
                    "production",
                    "-t",
                    "web",
                ],
            )
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "dev-web",
                    "10.0.0.2",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                    "-t",
                    "development",
                ],
            )
            result = runner.invoke(
                cli,
                ["--config", config_file, "host", "list", "-t", "production"],
            )
            assert result.exit_code == 0
            assert "prod-web" in result.output
            assert "dev-web" not in result.output


# ============================================================================
# host show 命令测试
# ============================================================================


class TestHostShow:
    """测试 host show 命令"""

    def test_show_existing_host(self, runner, config_file):
        """测试：显示已存在主机的详细信息"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "my-server",
                    "192.168.1.1",
                    "ubuntu",
                    "-k",
                    "~/.ssh/id_rsa",
                    "-d",
                    "My server",
                ],
            )
            result = runner.invoke(cli, ["--config", config_file, "host", "show", "my-server"])
            assert result.exit_code == 0
            assert "my-server" in result.output
            assert "192.168.1.1" in result.output
            assert "ubuntu" in result.output
            assert "My server" in result.output

    def test_show_nonexistent_host(self, runner, config_file):
        """测试：显示不存在的主机应报错"""
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--config", config_file, "host", "show", "ghost"])
            assert result.exit_code != 0
            assert "not found" in result.output

    def test_show_masks_secrets(self, runner, config_file):
        """回归测试：host show 不泄露密码明文和私钥完整路径"""
        with runner.isolated_filesystem():
            # 通过环境变量添加带密码的主机
            env = {"REMOTE_CMD_PASSWORD": "supersecret"}
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "prod-host",
                    "10.0.0.1",
                    "root",
                    "-k",
                    "/home/user/.ssh/production_key",
                ],
                env=env,
            )
            result = runner.invoke(cli, ["--config", config_file, "host", "show", "prod-host"])
            assert result.exit_code == 0
            # 密码不应以明文出现
            assert "supersecret" not in result.output
            # 私钥路径仅显示文件名
            assert "/home/user/.ssh/production_key" not in result.output
            assert "production_key" in result.output
            # 认证类型显示
            assert "Auth:" in result.output
            assert "SSH key" in result.output or "Password" in result.output


# ============================================================================
# host remove 命令测试
# ============================================================================


class TestHostRemove:
    """测试 host remove 命令"""

    def test_remove_with_confirmation(self, runner, config_file):
        """测试：带确认的移除操作"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "to-delete",
                    "1.2.3.4",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            result = runner.invoke(
                cli,
                ["--config", config_file, "host", "remove", "to-delete"],
                input="y\n",
            )
            assert result.exit_code == 0
            assert "removed" in result.output

    def test_remove_nonexistent(self, runner, config_file):
        """测试：移除不存在的主机应报错"""
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                ["--config", config_file, "host", "remove", "nonexistent"],
                input="y\n",
            )
            assert result.exit_code != 0
            assert "not found" in result.output


# ============================================================================
# host test 命令测试
# ============================================================================


class TestHostTest:
    """测试 host test 命令"""

    def test_connection_success(self, runner, config_file):
        """测试：连接成功"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "good-host",
                    "10.0.0.1",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            with patch(
                "remote_cmd.service.host_service.HostService.test_connection",
                return_value=True,
            ):
                result = runner.invoke(
                    cli,
                    ["--config", config_file, "host", "test", "good-host"],
                )
                assert result.exit_code == 0
                assert "connection successful" in result.output

    def test_connection_failure(self, runner, config_file):
        """测试：连接失败"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                [
                    "--config",
                    config_file,
                    "host",
                    "add",
                    "bad-host",
                    "10.0.0.99",
                    "admin",
                    "-k",
                    "~/.ssh/id_rsa",
                ],
            )
            with patch(
                "remote_cmd.service.host_service.HostService.test_connection",
                return_value=False,
            ):
                result = runner.invoke(
                    cli,
                    ["--config", config_file, "host", "test", "bad-host"],
                )
                assert result.exit_code != 0
                assert "connection failed" in result.output


# ============================================================================
# CLI 根命令测试
# ============================================================================


class TestCliRoot:
    """测试 CLI 根命令"""

    def test_version(self, runner):
        """测试：--version 显示版本号"""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help(self, runner):
        """测试：--help 显示帮助信息"""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "host" in result.output
        assert "run" in result.output
        assert "upload" in result.output
        assert "download" in result.output


# ============================================================================
# run 命令测试
# ============================================================================


class TestRun:
    """测试 run 命令"""

    def test_run_success(self, runner, config_file):
        """测试：执行远程命令成功，应返回 exit_code 0"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="uptime", stdout="load: 0.5\n", stderr="", exit_code=0
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "uptime"])
            assert result.exit_code == 0
            assert "load: 0.5" in result.output

    def test_run_failure(self, runner, config_file):
        """测试：执行远程命令失败，应返回 exit_code 1"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="bad", stdout="", stderr="error msg", exit_code=1
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "bad"])
            assert result.exit_code == 1
            assert "error msg" in result.output


# ============================================================================
# upload / download 命令测试
# ============================================================================


class TestUploadDownload:
    """测试 upload 和 download 命令"""

    def test_upload_success(self, runner, config_file, tmp_path):
        """测试：上传文件成功"""
        local = tmp_path / "myfile.txt"
        local.write_text("data")
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host"):
                result = runner.invoke(
                    cli, ["--config", config_file, "upload", "srv", str(local), "/remote/f"]
                )
            assert "Uploaded" in result.output

    def test_download_failure(self, runner, config_file):
        """测试：下载失败时显示错误"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.download_file.side_effect = Exception("connection lost")
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(
                    cli, ["--config", config_file, "download", "srv", "/local/f", "/remote/f"]
                )
            assert "connection lost" in result.output


# ============================================================================
# Exit 捕获 bug 回归测试
# ============================================================================


class TestExitCodePropagation:
    """回归测试：确保 ctx.exit() 抛出的 SystemExit 不被 except Exception 捕获"""

    def test_run_success_exit_code_zero(self, runner, config_file):
        """run 命令成功时应返回 exit_code 0"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="ls", stdout="file.txt\n", stderr="", exit_code=0
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "ls"])
            assert result.exit_code == 0

    def test_run_nonzero_exit_code_propagated(self, runner, config_file):
        """run 命令应正确传播远程命令的非零退出码"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="false", stdout="", stderr="", exit_code=42
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "false"])
            assert result.exit_code == 42

    def test_upload_success_exit_code_zero(self, runner, config_file, tmp_path):
        """upload 命令成功时应返回 exit_code 0"""
        local = tmp_path / "myfile.txt"
        local.write_text("data")
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host"):
                result = runner.invoke(
                    cli, ["--config", config_file, "upload", "srv", str(local), "/remote/f"]
                )
            assert result.exit_code == 0

    def test_download_success_exit_code_zero(self, runner, config_file):
        """download 命令成功时应返回 exit_code 0"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.service.host_service.HostService.connect_to_host") as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(
                    cli, ["--config", config_file, "download", "srv", "/local/f", "/remote/f"]
                )
            assert result.exit_code == 0

    def test_host_add_error_exits_nonzero(self, runner, config_file):
        """host add 重复主机时应返回非零退出码"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "dup", "1.2.3.4", "admin", "-k", "key"],
            )
            result = runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "dup", "5.6.7.8", "admin", "-k", "key"],
            )
            assert result.exit_code != 0


# ============================================================================
# batch-run 命令测试
# ============================================================================


def make_batch_result(success_hosts=(), failed_hosts=()):
    """构造 BatchResult，results 中每个主机附带 BatchHostResult"""
    from remote_cmd.service.batch_executor import BatchHostResult, BatchResult

    results = {}
    for h in success_hosts:
        results[h] = BatchHostResult(host=h, success=True, command="uptime", exit_code=0)
    for h in failed_hosts:
        results[h] = BatchHostResult(
            host=h, success=False, command="uptime", exit_code=1, error="boom"
        )
    return BatchResult(
        total=len(success_hosts) + len(failed_hosts),
        success=len(success_hosts),
        failed=len(failed_hosts),
        duration=1.5,
        results=results,
    )


class TestBatchRun:
    """测试 batch-run 命令"""

    def test_batch_run_success(self, runner, config_file):
        """测试：全部成功时 exit_code 0 且显示成功主机"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv1", "1.2.3.4", "admin", "-k", "key"],
            )
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv2", "5.6.7.8", "admin", "-k", "key"],
            )
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(
                    success_hosts=["srv1", "srv2"]
                )
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    [
                        "--config",
                        config_file,
                        "batch-run",
                        "srv1",
                        "srv2",
                        "uptime",
                        "-C",
                        "5",
                        "-T",
                        "10",
                        "-r",
                        "2",
                    ],
                )
            assert result.exit_code == 0
            assert "Batch result summary" in result.output
            assert "Succeeded: 2" in result.output
            assert "Failed:   0" in result.output
            assert "srv1" in result.output
            assert "srv2" in result.output

    def test_batch_run_partial_failure_exits_1(self, runner, config_file):
        """测试：部分失败时 exit_code 1 且显示失败主机"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv1", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(
                    success_hosts=["srv1"], failed_hosts=["srv2"]
                )
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    ["--config", config_file, "batch-run", "srv1", "srv2", "uptime"],
                )
            assert result.exit_code == 1
            assert "Failed hosts:" in result.output
            assert "boom" in result.output

    def test_batch_run_show_failures_only(self, runner, config_file):
        """测试：--show-failures 时不显示成功主机"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv1", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(
                    success_hosts=["srv1"], failed_hosts=["srv2"]
                )
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    [
                        "--config",
                        config_file,
                        "batch-run",
                        "srv1",
                        "srv2",
                        "uptime",
                        "--show-failures",
                    ],
                )
            assert result.exit_code == 1
            assert "Successful hosts:" not in result.output
            assert "Failed hosts:" in result.output

    def test_batch_run_passes_options(self, runner, config_file):
        """测试：并发/超时/重试参数传递给 BatchExecutor"""
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(success_hosts=["srv1"])
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    [
                        "--config",
                        config_file,
                        "batch-run",
                        "srv1",
                        "uptime",
                        "-C",
                        "3",
                        "-T",
                        "15",
                        "-r",
                        "4",
                        "--retry-delay",
                        "2.5",
                    ],
                )
            assert result.exit_code == 0
            mock_executor_cls.assert_called_once()
            _, kwargs = mock_executor_cls.call_args
            assert kwargs["max_concurrency"] == 3
            assert kwargs["command_timeout"] == 15
            assert kwargs["use_async"] is False
            call_kwargs = mock_executor.execute.call_args.kwargs
            assert call_kwargs["host_names"] == ["srv1"]
            assert call_kwargs["command"] == "uptime"
            assert call_kwargs["retry_count"] == 4
            assert call_kwargs["retry_delay"] == 2.5
            assert callable(call_kwargs["progress_callback"])

    def test_batch_run_async_flag_passed(self, runner, config_file):
        """测试：--async 时 use_async=True 透传给 BatchExecutor"""
        with runner.isolated_filesystem():
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(
                    success_hosts=["srv1", "srv2"]
                )
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    [
                        "--config",
                        config_file,
                        "batch-run",
                        "srv1",
                        "srv2",
                        "uptime",
                        "--async",
                    ],
                )
            assert result.exit_code == 0
            assert "Batch result summary" in result.output
            assert "Succeeded: 2" in result.output
            _, kwargs = mock_executor_cls.call_args
            assert kwargs["use_async"] is True

    def test_batch_run_async_executes_same_result(self, runner, config_file):
        """测试：--async 与默认模式返回码/输出一致"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv1", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch("remote_cmd.cli.main.BatchExecutor") as mock_executor_cls:
                mock_executor = MagicMock()
                mock_executor.execute.return_value = make_batch_result(
                    success_hosts=["srv1"], failed_hosts=["srv2"]
                )
                mock_executor_cls.return_value = mock_executor
                result = runner.invoke(
                    cli,
                    ["--config", config_file, "batch-run", "srv1", "srv2", "uptime", "--async"],
                )
            assert result.exit_code == 1
            assert "Failed hosts:" in result.output
            assert "boom" in result.output

    def test_batch_run_missing_command_usage_error(self, runner, config_file):
        """测试：缺少 COMMAND 参数时返回 usage error（异步/同步同一解析错误）"""
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                ["--config", config_file, "batch-run", "srv1", "--async"],
            )
            assert result.exit_code != 0
            assert "Error" in result.output or "Usage" in result.output

    def test_batch_run_invalid_concurrency_usage_error(self, runner, config_file):
        """测试：--concurrency 非整数时返回 usage 错误"""
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                ["--config", config_file, "batch-run", "srv1", "uptime", "-C", "abc"],
            )
            assert result.exit_code != 0
            assert "Invalid value" in result.output
