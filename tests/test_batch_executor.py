"""BatchExecutor 批量执行器测试"""

from unittest.mock import MagicMock, patch

import pytest

from remote_cmd.core.host import Host
from remote_cmd.service.batch_executor import BatchExecutor, BatchHostResult, BatchResult


class TestBatchHostResult:
    """BatchHostResult 数据类测试"""

    def test_default_values(self):
        """测试默认值"""
        r = BatchHostResult(host="srv1", success=True, command="uptime")
        assert r.host == "srv1"
        assert r.success is True
        assert r.stdout == ""
        assert r.exit_code == -1
        assert r.error is None
        assert r.duration == 0.0


class TestBatchResult:
    """BatchResult 数据类测试"""

    def test_success_rate_all_success(self):
        """测试成功率：全部成功"""
        r = BatchResult(total=10, success=10, failed=0, duration=5.0)
        assert r.success_rate == 1.0

    def test_success_rate_half(self):
        """测试成功率：一半成功"""
        r = BatchResult(total=10, success=5, failed=5, duration=5.0)
        assert r.success_rate == 0.5

    def test_success_rate_empty(self):
        """测试成功率：空结果"""
        r = BatchResult(total=0, success=0, failed=0, duration=0.0)
        assert r.success_rate == 1.0

    def test_failed_hosts_property(self):
        """测试 failed_hosts 属性"""
        r = BatchResult(
            total=3,
            success=1,
            failed=2,
            duration=1.0,
            results={
                "srv1": BatchHostResult(host="srv1", success=True, command="cmd"),
                "srv2": BatchHostResult(host="srv2", success=False, command="cmd", error="err"),
                "srv3": BatchHostResult(host="srv3", success=False, command="cmd", error="err2"),
            },
        )
        assert r.failed_hosts == ["srv2", "srv3"]

    def test_success_hosts_property(self):
        """测试 success_hosts 属性"""
        r = BatchResult(
            total=2,
            success=1,
            failed=1,
            duration=1.0,
            results={
                "srv1": BatchHostResult(host="srv1", success=True, command="cmd"),
                "srv2": BatchHostResult(host="srv2", success=False, command="cmd"),
            },
        )
        assert r.success_hosts == ["srv1"]

    def test_summary_format(self):
        """测试 summary 格式"""
        r = BatchResult(total=5, success=4, failed=1, duration=10.5)
        summary = r.summary()
        assert "Total: 5" in summary
        assert "Succeeded: 4" in summary
        assert "Failed: 1" in summary
        assert "10.5" in summary
        assert "80.0%" in summary


class TestBatchExecutor:
    """BatchExecutor 执行器测试"""

    def make_mock_service(self, hosts: list):
        """创建模拟的 HostService"""
        service = MagicMock()
        host_dict = {h.name: h for h in hosts}

        def resolve_host(name):
            if name in host_dict:
                return host_dict[name]
            raise KeyError(f"主机 '{name}' 不存在")

        service.resolve_host = resolve_host
        return service

    def test_empty_host_list_raises(self):
        """测试：空主机列表应报错"""
        executor = BatchExecutor(host_service=MagicMock())
        with pytest.raises(ValueError, match="host_names must not be empty"):
            executor.execute([], "uptime")

    def test_invalid_max_concurrency_raises(self):
        """测试：max_concurrency < 1 应报错"""
        with pytest.raises(ValueError, match="max_concurrency must be >= 1"):
            BatchExecutor(host_service=MagicMock(), max_concurrency=0)

    def test_invalid_command_timeout_raises(self):
        """测试：command_timeout <= 0 应报错"""
        with pytest.raises(ValueError, match="command_timeout must be > 0"):
            BatchExecutor(host_service=MagicMock(), command_timeout=0)

    def test_invalid_retry_params_raise(self):
        """测试：retry_count / retry_delay 非法值应报错"""
        executor = BatchExecutor(host_service=MagicMock())
        with pytest.raises(ValueError, match="retry_count must be >= 0"):
            executor.execute(["srv1"], "uptime", retry_count=-1)
        with pytest.raises(ValueError, match="retry_delay must be >= 0"):
            executor.execute(["srv1"], "uptime", retry_delay=-0.5)

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_duplicate_host_deduped(self, mock_ssh_class):
        """测试：重复主机名去重，只执行一次且统计正确"""
        hosts = [Host(name="srv1", hostname="10.0.0.1", username="admin")]
        service = self.make_mock_service(hosts)

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["srv1", "srv1", "srv1"], "uptime")

        assert result.total == 1
        assert result.success == 1
        assert result.failed == 0
        assert mock_instance.execute.call_count == 1

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_single_host_success(self, mock_ssh_class):
        """测试：单主机执行成功"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["srv1"], "uptime")

        assert result.total == 1
        assert result.success == 1
        assert result.failed == 0
        assert "srv1" in result.results
        assert result.results["srv1"].success is True

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_multiple_hosts(self, mock_ssh_class):
        """测试：多主机并发执行"""
        hosts = [
            Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(1, 4)
        ]
        service = self.make_mock_service(hosts)

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_instance.execute.return_value = mock_result

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["srv1", "srv2", "srv3"], "uptime")

        assert result.total == 3
        assert result.success == 3
        assert result.results["srv1"].success is True
        assert result.results["srv2"].success is True
        assert result.results["srv3"].success is True

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_host_not_found(self, _mock_ssh_class):
        """测试：主机不存在"""
        service = self.make_mock_service([])

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["ghost"], "uptime")

        assert result.total == 1
        assert result.success == 0
        assert result.failed == 1
        assert "not found" in (result.results["ghost"].error or "")

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_retry_on_failure(self, mock_ssh_class):
        """测试：失败重试（重试只在连接异常时触发）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance

        # 第一次调用抛出异常触发重试，第二次返回成功
        def execute_side_effect(command, timeout=None):  # noqa: ARG001
            if execute_side_effect.call_count == 0:
                execute_side_effect.call_count += 1
                raise Exception("Connection reset")
            return ok_result

        execute_side_effect.call_count = 0

        ok_result = MagicMock()
        ok_result.success = True
        ok_result.exit_code = 0
        ok_result.stdout = "OK"
        ok_result.stderr = ""

        mock_instance.execute.side_effect = execute_side_effect

        from remote_cmd.service.batch_executor import BatchExecutor

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["srv1"], "uptime", retry_count=1, retry_delay=0.01)

        assert result.total == 1
        assert result.success == 1
        assert mock_instance.execute.call_count == 2

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_retry_exhausted_preserves_duration(self, mock_ssh_class):
        """测试：所有重试失败时，结果保留最后一次尝试的耗时"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance

        def execute_always_fail(command, timeout=None):  # noqa: ARG001
            raise Exception("Connection reset")

        mock_instance.execute.side_effect = execute_always_fail

        from remote_cmd.service.batch_executor import BatchExecutor

        executor = BatchExecutor(host_service=service)
        result = executor.execute(["srv1"], "uptime", retry_count=2, retry_delay=0.01)

        assert result.total == 1
        assert result.success == 0
        host_result = result.results["srv1"]
        assert host_result.success is False
        assert host_result.error == "Connection reset"
        # duration 应为正数（记录了尝试耗时），而非恒为 0
        assert host_result.duration > 0.0
        assert mock_instance.execute.call_count == 3

    def test_progress_callback(self):
        """测试：进度回调"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        progress_data = []

        def callback(completed, total, host_name):
            progress_data.append((completed, total, host_name))

        with patch("remote_cmd.service.batch_executor.SSHClient") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            mock_result = MagicMock()
            mock_result.success = True
            mock_instance.execute.return_value = mock_result

            executor = BatchExecutor(host_service=service)
            executor.execute(["srv1"], "uptime", progress_callback=callback)

        assert len(progress_data) == 1
        assert progress_data[0] == (1, 1, "srv1")

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_keyboard_interrupt_creates_failure_records(self, mock_ssh_class):
        """测试：KeyboardInterrupt 时未完成主机被标记为 user interrupted"""
        hosts = [
            Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(1, 4)
        ]
        service = self.make_mock_service(hosts)

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance

        # 模拟成功执行的结果
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        executor = BatchExecutor(host_service=service)

        # 直接测试 _handle_interrupt 方法：模拟部分完成状态
        from remote_cmd.service.batch_executor import BatchHostResult

        # 模拟已有一个结果，两个未完成
        existing_results = {
            "srv1": BatchHostResult(host="srv1", success=True, command="uptime", exit_code=0)
        }
        future_map = {}

        # 调用 _handle_interrupt
        executor._handle_interrupt(future_map, ["srv1", "srv2", "srv3"], "uptime", existing_results)

        # 验证：srv1 保留原结果，srv2/srv3 被标记为 user interrupted
        assert existing_results["srv1"].success is True
        assert existing_results["srv2"].error == "user interrupted"
        assert existing_results["srv3"].error == "user interrupted"

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_single_host_no_pool(self, mock_ssh_class):
        """测试：单主机无连接池模式（不创建 SyncConnectionPool）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        # patch SyncConnectionPool 追踪是否被创建
        with patch("remote_cmd.service.batch_executor.SyncConnectionPool") as mock_pool_class:
            executor = BatchExecutor(host_service=service)
            result = executor.execute(["srv1"], "uptime")

        assert result.success == 1
        # 单主机无重试时不应创建连接池
        mock_pool_class.assert_not_called()

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_multi_host_with_pool(self, mock_ssh_class):
        """测试：多主机有连接池模式（创建 SyncConnectionPool）"""
        hosts = [
            Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin") for i in range(1, 3)
        ]
        service = self.make_mock_service(hosts)

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        # patch SyncConnectionPool 追踪创建
        mock_pool_instance = MagicMock()
        with patch(
            "remote_cmd.service.batch_executor.SyncConnectionPool",
            return_value=mock_pool_instance,
        ) as mock_pool_class:
            executor = BatchExecutor(host_service=service)
            result = executor.execute(["srv1", "srv2"], "uptime")

        assert result.success == 2
        # 多主机时应创建连接池（每个主机一个）
        assert mock_pool_class.call_count == 2
        mock_pool_instance.close_all.assert_called()

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_retry_uses_pool(self, mock_ssh_class):
        """测试：重试时使用连接池（即使单主机）"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_instance.execute.return_value = mock_result

        mock_pool_instance = MagicMock()
        with patch(
            "remote_cmd.service.batch_executor.SyncConnectionPool",
            return_value=mock_pool_instance,
        ) as mock_pool_class:
            executor = BatchExecutor(host_service=service)
            result = executor.execute(["srv1"], "uptime", retry_count=2)

        assert result.success == 1
        # 重试时应创建连接池
        mock_pool_class.assert_called_once()
        mock_pool_instance.close_all.assert_called_once()

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_async_progress_callback_logs_warning(self, mock_ssh_class, caplog):
        """测试：同步内核收到异步进度回调时记录 warning"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        service = self.make_mock_service([host])

        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_result = MagicMock()
        mock_result.success = True
        mock_instance.execute.return_value = mock_result

        async def async_callback(completed, total, host_name):  # noqa: ARG001
            pass

        import logging

        with caplog.at_level(logging.WARNING, logger="remote_cmd.service.batch_executor"):
            executor = BatchExecutor(host_service=service)
            executor.execute(["srv1"], "uptime", progress_callback=async_callback)

        assert "同步内核不支持异步进度回调" in caplog.text
