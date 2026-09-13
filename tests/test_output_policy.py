"""OutputPolicy（v2.5 输出保留策略）与执行器接入测试。

覆盖评审 P1：
- ``OutputPolicy`` 构造校验与默认值（None = 完整保留，兼容 v2.4）
- legacy ``max_output_bytes`` 与 ``OutputPolicy`` 的消歧解析
- 同步 / 异步执行器通过 policy 截断输出
- ``BatchExecutor(use_async=True)`` 正确转发解析后的策略
"""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.service._host_runner import (
    OUTPUT_TRUNCATION_MARKER,
    resolve_max_output_bytes,
)
from remote_cmd.service._types import OutputPolicy
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.batch_executor import BatchExecutor
from remote_cmd.utils.exceptions import ValidationError


def make_mock_service(hosts: list[Host]):
    service = MagicMock()
    host_dict = {h.name: h for h in hosts}

    def _resolve(name):
        if name in host_dict:
            return host_dict[name]
        raise KeyError(name)

    service.resolve_host = _resolve
    return service


class TestOutputPolicy:
    def test_default_is_unlimited(self):
        """默认与 v2.4 一致：None 表示完整保留（兼容默认）。"""
        assert OutputPolicy().max_output_bytes is None
        assert OutputPolicy(max_output_bytes=None).max_output_bytes is None

    def test_positive_value(self):
        assert OutputPolicy(max_output_bytes=1024).max_output_bytes == 1024

    @pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "10"])
    def test_invalid_value_raises(self, bad):
        with pytest.raises(ValidationError, match="max_output_bytes"):
            OutputPolicy(max_output_bytes=bad)

    def test_frozen(self):
        policy = OutputPolicy(max_output_bytes=10)
        with pytest.raises(FrozenInstanceError):
            policy.max_output_bytes = 20  # type: ignore[misc]


class TestResolveMaxOutputBytes:
    def test_both_none(self):
        assert resolve_max_output_bytes(None, None) is None

    def test_legacy_value(self):
        assert resolve_max_output_bytes(512, None) == 512

    def test_policy_value(self):
        assert resolve_max_output_bytes(None, OutputPolicy(max_output_bytes=256)) == 256

    def test_policy_unlimited(self):
        assert resolve_max_output_bytes(None, OutputPolicy()) is None

    def test_ambiguous_raises(self):
        with pytest.raises(ValidationError, match="ambiguous"):
            resolve_max_output_bytes(10, OutputPolicy(max_output_bytes=20))

    def test_legacy_invalid_raises(self):
        with pytest.raises(ValidationError, match="max_output_bytes"):
            resolve_max_output_bytes(0, None)


class TestExecutorOutputPolicy:
    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_sync_executor_truncates_with_policy(self, mock_ssh_class):
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_instance.execute.return_value = CommandResult("cmd", "x" * 100, "y" * 100, 0)

        ex = BatchExecutor(
            host_service=make_mock_service([host]),
            max_concurrency=1,
            output_policy=OutputPolicy(max_output_bytes=10),
        )
        result = ex.execute(["srv1"], "cmd")

        r = result.results["srv1"]
        marker = OUTPUT_TRUNCATION_MARKER.format(omitted=90)
        assert r.stdout == "x" * 10 + marker
        assert r.stderr == "y" * 10 + marker

    @pytest.mark.asyncio
    async def test_async_executor_truncates_with_policy(self):
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        instance = MagicMock()
        instance.connect = AsyncMock(return_value=instance)
        instance.disconnect = AsyncMock()
        instance.execute = AsyncMock(return_value=CommandResult("cmd", "x" * 50, "y" * 50, 0))
        instance.is_connected.return_value = True
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "remote_cmd.service.async_batch_executor.AsyncSSHClient",
            MagicMock(return_value=instance),
        ):
            ex = AsyncBatchExecutor(
                host_service=make_mock_service([host]),
                max_concurrency=1,
                output_policy=OutputPolicy(max_output_bytes=5),
            )
            result = await ex.execute(["srv1"], "cmd")

        r = result.results["srv1"]
        marker = OUTPUT_TRUNCATION_MARKER.format(omitted=45)
        assert r.stdout == "x" * 5 + marker
        assert r.stderr == "y" * 5 + marker

    def test_sync_executor_ambiguous_params_raise(self):
        with pytest.raises(ValidationError, match="ambiguous"):
            BatchExecutor(
                host_service=MagicMock(),
                max_output_bytes=1,
                output_policy=OutputPolicy(),
            )

    def test_async_executor_ambiguous_params_raise(self):
        with pytest.raises(ValidationError, match="ambiguous"):
            AsyncBatchExecutor(
                host_service=MagicMock(),
                max_output_bytes=1,
                output_policy=OutputPolicy(),
            )

    @patch("remote_cmd.service.batch_executor.SSHClient")
    def test_legacy_max_output_bytes_still_works(self, mock_ssh_class):
        """legacy 参数路径保持 v2.4 行为（截断 + 标记）。"""
        host = Host(name="srv1", hostname="10.0.0.1", username="admin")
        mock_instance = MagicMock()
        mock_ssh_class.return_value = mock_instance
        mock_instance.execute.return_value = CommandResult("cmd", "z" * 20, "", 0)

        ex = BatchExecutor(
            host_service=make_mock_service([host]),
            max_concurrency=1,
            max_output_bytes=4,
        )
        result = ex.execute(["srv1"], "cmd")
        assert result.results["srv1"].stdout == "z" * 4 + OUTPUT_TRUNCATION_MARKER.format(
            omitted=16
        )

    def test_policy_forwarded_to_async_kernel(self):
        """use_async=True 时 policy 解析结果转发给异步内核。"""
        with patch("remote_cmd.service.async_batch_executor.AsyncBatchExecutor") as mock_async:
            ex = BatchExecutor(
                host_service=MagicMock(),
                use_async=True,
                output_policy=OutputPolicy(max_output_bytes=77),
            )
        assert ex._async_executor is mock_async.return_value
        assert mock_async.call_args.kwargs["max_output_bytes"] == 77
