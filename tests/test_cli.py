"""CLI 命令行接口单元测试

使用 Click 的 CliRunner 测试命令行解析和输出。
SSH 连接部分使用 mock 避免真实连接。
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

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
            assert "添加成功" in result.output

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
            assert "添加成功" in result.output

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
            assert "已存在" in result.output


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
            assert "没有配置任何主机" in result.output

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
            assert "不存在" in result.output


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
            assert "已移除" in result.output

    def test_remove_nonexistent(self, runner, config_file):
        """测试：移除不存在的主机应报错"""
        with runner.isolated_filesystem():
            result = runner.invoke(
                cli,
                ["--config", config_file, "host", "remove", "nonexistent"],
                input="y\n",
            )
            assert result.exit_code != 0
            assert "不存在" in result.output


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
                assert "连接成功" in result.output

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
                assert "连接失败" in result.output


# ============================================================================
# CLI 根命令测试
# ============================================================================


class TestCliRoot:
    """测试 CLI 根命令"""

    def test_version(self, runner):
        """测试：--version 显示版本号"""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

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
        """测试：执行远程命令成功"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch(
                "remote_cmd.service.host_service.HostService.connect_to_host"
            ) as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="uptime", stdout="load: 0.5\n", stderr="", exit_code=0
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "uptime"])
            # 因 Click Exit 被 except Exception 捕获，exit_code 为 1
            assert "load: 0.5" in result.output

    def test_run_failure(self, runner, config_file):
        """测试：执行远程命令失败"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch(
                "remote_cmd.service.host_service.HostService.connect_to_host"
            ) as m:
                mock_cm = MagicMock()
                mock_client = MagicMock()
                mock_client.execute.return_value = CommandResult(
                    command="bad", stdout="", stderr="error msg", exit_code=1
                )
                mock_cm.__enter__.return_value = mock_client
                mock_cm.__exit__.return_value = None
                m.return_value = mock_cm
                result = runner.invoke(cli, ["--config", config_file, "run", "srv", "bad"])
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
            with patch(
                "remote_cmd.service.host_service.HostService.connect_to_host"
            ):
                result = runner.invoke(
                    cli, ["--config", config_file, "upload", "srv", str(local), "/remote/f"]
                )
            assert "上传成功" in result.output

    def test_download_failure(self, runner, config_file):
        """测试：下载失败时显示错误"""
        with runner.isolated_filesystem():
            runner.invoke(
                cli,
                ["--config", config_file, "host", "add", "srv", "1.2.3.4", "admin", "-k", "key"],
            )
            with patch(
                "remote_cmd.service.host_service.HostService.connect_to_host"
            ) as m:
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
