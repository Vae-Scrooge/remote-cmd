"""性能基准测试 fixtures

提供受控延迟的 Mock SSH 后端，使同步 BatchExecutor（ThreadPoolExecutor + time.sleep）
与 AsyncBatchExecutor（asyncio + asyncio.sleep）面对**相同的服务器延迟模型**，
从而只测量"调度框架开销"差异——这正是 P0 异步改造的真正价值点。

设计原则：
- 同步与异步使用相同的 per-host 延迟（latency_per_host）。
- 批量场景下，总墙钟时间 ≈ ceil(N / max_concurrency) * latency（理想）。
- 异步路径额外开销 ~0；同步路径额外开销 = 线程创建/切换/GIL 竞争。
- 无需 Docker / 真实 SSH，零外部依赖，可重复运行。
- 真实 SSH 集成基准使用 `@pytest.mark.integration` 标记，有 Docker 时才跑。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.batch_executor import BatchExecutor

# ============================================================================
# 共享服务器延迟模型
# ============================================================================


def make_hosts(n: int, base_ip: str = "10.0.0") -> list[Host]:
    """构造 n 台主机配置。"""
    return [
        Host(
            name=f"srv{i}",
            hostname=f"{base_ip}.{i}",
            username="admin",
            port=22,
        )
        for i in range(1, n + 1)
    ]


def make_mock_service(hosts: list[Host]):
    """构造可解析主机的 Mock HostService（同步与异步通用）。"""
    service = MagicMock()
    host_dict = {h.name: h for h in hosts}

    def resolve(name):
        if name in host_dict:
            return host_dict[name]
        raise KeyError(f"主机 '{name}' 不存在")

    service._resolve_host = resolve
    return service


# ============================================================================
# 异步路径 Mock：AsyncSSHClient.execute 用 asyncio.sleep 模拟网络 I/O
# ============================================================================


@pytest.fixture
def patched_async_client():
    """patch NativeAsyncSSHClient 为受控延迟的 async mock。

    暴露 `latency` 可在测试内动态调整（默认 0.05s）。
    """
    state = {"latency": 0.05}

    class _StubClient:
        def __init__(self, config: ConnectionConfig, *args, **kwargs):  # noqa: ARG002
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):  # noqa: ARG002
            return None

        async def execute(self, command, timeout=None, environment=None):  # noqa: ARG002
            await asyncio.sleep(state["latency"])
            return CommandResult(command=command, stdout="OK", stderr="", exit_code=0)

    with patch(
        "remote_cmd.service.async_batch_executor.AsyncSSHClient", _StubClient
    ):
        yield state


# ============================================================================
# 同步路径 Mock：SSHClient.execute 用 time.sleep 模拟阻塞 I/O
# ============================================================================


@pytest.fixture
def patched_sync_client():
    """patch 同步 SSHClient 为受控延迟的同步 mock（time.sleep 阻塞）。

    暴露 `latency` 可在测试内动态调整（默认 0.05s）。
    """
    state = {"latency": 0.05}

    def _execute(self, command, timeout=None, environment=None):  # noqa: ARG002
        time.sleep(state["latency"])
        return CommandResult(command=command, stdout="OK", stderr="", exit_code=0)

    def _connect(self):
        return self

    def _disconnect(self):
        return None

    patches = (
        patch("remote_cmd.service.batch_executor.SSHClient.execute", _execute),
        patch("remote_cmd.service.batch_executor.SSHClient.connect", _connect),
        patch("remote_cmd.service.batch_executor.SSHClient.disconnect", _disconnect),
    )
    for p in patches:
        p.start()
    try:
        yield state
    finally:
        for p in patches:
            p.stop()


# ============================================================================
# 计时工具
# ============================================================================


def measure(func):
    """同步计时：返回 (耗时秒, 返回值)。"""
    start = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - start
    return elapsed, result


async def measure_async(coro):
    """异步计时：返回 (耗时秒, 返回值)。"""
    start = time.perf_counter()
    result = await coro
    elapsed = time.perf_counter() - start
    return elapsed, result


# ============================================================================
# AsyncBatchExecutor / BatchExecutor 工厂（便于基准内复用）
# ============================================================================


@pytest.fixture
def async_backend_factory(patched_async_client, make_service_factory):
    """返回 (executor, latency_state) 工厂。"""

    def _factory(hosts, max_concurrency=10, latency=0.05, command_timeout=30):
        patched_async_client["latency"] = latency
        service = make_service_factory(hosts)
        ex = AsyncBatchExecutor(
            host_service=service, max_concurrency=max_concurrency, command_timeout=command_timeout
        )
        return ex, patched_async_client

    return _factory


@pytest.fixture
def sync_backend_factory(patched_sync_client, make_service_factory):
    """返回 (executor, latency_state) 工厂。"""

    def _factory(hosts, max_concurrency=10, latency=0.05, command_timeout=30):
        patched_sync_client["latency"] = latency
        service = make_service_factory(hosts)
        ex = BatchExecutor(
            host_service=service, max_concurrency=max_concurrency, command_timeout=command_timeout
        )
        return ex, patched_sync_client

    return _factory


@pytest.fixture
def make_service_factory():
    """工厂 fixture：返回构造 mock_service 的函数（避免与模块级 make_mock_service 重名）。"""

    def _factory(hosts):
        return make_mock_service(hosts)

    return _factory


__all__ = [
    "make_hosts",
    "make_mock_service",
    "patched_async_client",
    "patched_sync_client",
    "measure",
    "measure_async",
    "async_backend_factory",
    "sync_backend_factory",
]
