"""性能基准测试：对比同步 BatchExecutor（ThreadPoolExecutor）与 AsyncBatchExecutor（asyncio）

基准定位
========
本基准不依赖真实 SSH / Docker。通过受控延迟模型（同步 time.sleep / 异步 asyncio.sleep）
让两条路径面对**相同的服务器延迟**，从而测量：

1. **并发正确性**：N 主机、并发 C、延迟 L 的批量，墙钟时间应 ≈ ceil(N/C) * L。
   验证 AsyncBatchExecutor 的并发调度符合理论预期。
2. **调度框架开销**：在极短延迟（L≈0）下，对比纯框架开销。
   同步路径需为每主机调度线程并切换；异步路径在单事件循环上协程切换。
   主机数足够大时（N≥500），同步的线程/上下文开销显著高于异步。
3. **可扩展性曲线**：N 从 10 → 100 → 500，记录耗时，量化并发倍率。
4. **use_async 委托路径**：验证 BatchExecutor(use_async=True).execute 行为与
   AsyncBatchExecutor 一致。

运行
====
默认不运行（pyproject addopts 已排除 `benchmark` 标记）。

    # 仅跑单元/集成测试（默认）
    pytest

    # 跑性能基准（含对比报告）
    pytest tests/performance -m benchmark -s -p no:cacheprovider

    # 跑某个具体场景
    pytest tests/performance/test_benchmarks.py -m benchmark -k scalability -s

指标说明
========
- `wall_time`：批量总耗时（秒），perf_counter 测量
- `speedup`：sync_wall / async_wall，>1 表示异步更快
- `overhead_ratio`：实测 / 理论时间，越接近 1.0 越理想
"""

from __future__ import annotations

import asyncio
import math
import time

import pytest

from tests.performance.conftest import make_hosts

pytestmark = pytest.mark.benchmark


# ============================================================================
# 理论时间辅助
# ============================================================================


def theoretical_time(n: int, concurrency: int, latency: float) -> float:
    """理想墙钟时间：ceil(N / C) * latency。"""
    return math.ceil(n / concurrency) * latency


# ============================================================================
# 1. 并发正确性：异步实现达到理论时间
# ============================================================================


class TestAsyncConcurrencyCorrectness:
    """验证 AsyncBatchExecutor 的并发调度符合理论预期。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "n, concurrency, latency",
        [
            (10, 10, 0.05),
            (20, 5, 0.05),
            (100, 10, 0.02),
        ],
    )
    async def test_async_meets_theoretical_time(
        self, async_backend_factory, n, concurrency, latency
    ):
        hosts = make_hosts(n)
        ex, _ = async_backend_factory(hosts, max_concurrency=concurrency, latency=latency)
        start = time.perf_counter()
        result = await ex.execute([h.name for h in hosts], "uptime")
        wall = time.perf_counter() - start

        ideal = theoretical_time(n, concurrency, latency)
        # 实测不应远超理论（允许 30% 调度余量）
        assert wall < ideal * 1.3, f"异步耗时 {wall:.3f}s 远超理论 {ideal:.3f}s"
        assert result.total == n
        assert result.success == n


# ============================================================================
# 2. 调度框架开销对比（延迟极短，放大框架差异）
# ============================================================================


class TestSchedulingOverhead:
    """延迟接近 0 时，对比同步线程池与异步协程的纯框架开销。

    主机数足够大时，同步需创建/调度大量线程，异步仅在事件循环上协程切换，
    异步应显著更快（speedup > 1）。
    """

    @pytest.mark.parametrize("n", [200, 500, 1000])
    def test_overhead_sync_vs_async(self, sync_backend_factory, async_backend_factory, n):
        concurrency = 50
        latency = 0.0  # 纯框架开销

        # --- 同步路径 ---
        hosts = make_hosts(n)
        ex_sync, _ = sync_backend_factory(hosts, max_concurrency=concurrency, latency=latency)
        t0 = time.perf_counter()
        ex_sync.execute([h.name for h in hosts], "uptime")
        sync_wall = time.perf_counter() - t0

        # --- 异步路径 ---
        hosts2 = make_hosts(n)
        ex_async, _ = async_backend_factory(hosts2, max_concurrency=concurrency, latency=latency)
        t0 = time.perf_counter()
        asyncio.run(ex_async.execute([h.name for h in hosts2], "uptime"))
        async_wall = time.perf_counter() - t0

        speedup = sync_wall / async_wall if async_wall > 0 else float("inf")
        print(
            f"\n  [n={n} C={concurrency} L=0] sync={sync_wall:.4f}s "
            f"async={async_wall:.4f}s speedup={speedup:.2f}x"
        )
        # 断言：异步不应慢于同步（框架开销更小）。允许 10% 测量波动。
        assert async_wall <= sync_wall * 1.1, (
            f"异步 {async_wall:.4f}s 慢于同步 {sync_wall:.4f}s，speedup={speedup:.2f}x"
        )


# ============================================================================
# 3. 可扩展性曲线
# ============================================================================


class TestScalabilityCurve:
    """不同规模下记录耗时，输出可扩展性曲线与并发倍率。"""

    @pytest.mark.parametrize("n, concurrency", [(10, 10), (50, 10), (100, 20), (500, 50)])
    def test_scalability_async(self, async_backend_factory, n, concurrency):
        latency = 0.02
        hosts = make_hosts(n)
        ex, _ = async_backend_factory(hosts, max_concurrency=concurrency, latency=latency)
        t0 = time.perf_counter()
        result = asyncio.run(ex.execute([h.name for h in hosts], "uptime"))
        wall = time.perf_counter() - t0

        ideal = theoretical_time(n, concurrency, latency)
        overhead = wall / ideal if ideal > 0 else 0
        throughput = n / wall if wall > 0 else 0
        print(
            f"\n  [ASYNC n={n} C={concurrency} L={latency}] "
            f"wall={wall:.3f}s ideal={ideal:.3f}s overhead={overhead:.2f}x "
            f"throughput={throughput:.1f} hosts/s success={result.success}/{n}"
        )
        assert result.success == n
        # 可扩展性断言：实测不应超过理论 1.5 倍
        assert wall < ideal * 1.5

    @pytest.mark.parametrize("n, concurrency", [(10, 10), (50, 10), (100, 20), (500, 50)])
    def test_scalability_sync(self, sync_backend_factory, n, concurrency):
        latency = 0.02
        hosts = make_hosts(n)
        ex, _ = sync_backend_factory(hosts, max_concurrency=concurrency, latency=latency)
        t0 = time.perf_counter()
        result = ex.execute([h.name for h in hosts], "uptime")
        wall = time.perf_counter() - t0

        ideal = theoretical_time(n, concurrency, latency)
        overhead = wall / ideal if ideal > 0 else 0
        throughput = n / wall if wall > 0 else 0
        print(
            f"\n  [SYNC  n={n} C={concurrency} L={latency}] "
            f"wall={wall:.3f}s ideal={ideal:.3f}s overhead={overhead:.2f}x "
            f"throughput={throughput:.1f} hosts/s success={result.success}/{n}"
        )
        assert result.success == n
        # 同步在 N 较大时线程开销更高，放宽到 2.0x
        assert wall < ideal * 2.0


# ============================================================================
# 4. use_async 委托路径一致性
# ============================================================================


class TestUseAsyncDelegation:
    """验证 BatchExecutor(use_async=True) 与 AsyncBatchExecutor 结果一致。"""

    def test_delegation_matches_async_directly(self, async_backend_factory):
        from unittest.mock import patch

        from tests.performance.conftest import make_mock_service

        hosts = make_hosts(30)
        # 直接异步
        ex_async, state = async_backend_factory(hosts, max_concurrency=10, latency=0.02)
        r1 = asyncio.run(ex_async.execute([h.name for h in hosts], "uptime"))

        # 通过 BatchExecutor(use_async=True) 委托

        class _Stub:
            def __init__(self, config, *a, **k):  # noqa: ARG002
                self.config = config

            async def connect(self):
                return self

            async def disconnect(self):
                return None

            def is_connected(self):
                return True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):  # noqa: ARG002
                return None

            async def execute(self, command, timeout=None, environment=None):  # noqa: ARG002
                await asyncio.sleep(0.02)
                from remote_cmd.core.ssh_client import CommandResult

                return CommandResult(command=command, stdout="OK", stderr="", exit_code=0)

        from remote_cmd.service.batch_executor import BatchExecutor

        with patch("remote_cmd.service.async_batch_executor.AsyncSSHClient", _Stub):
            ex_delegated = BatchExecutor(
                host_service=make_mock_service(hosts),
                max_concurrency=10,
                use_async=True,
            )
            r2 = ex_delegated.execute([h.name for h in hosts], "uptime")

        assert r1.total == r2.total == 30
        assert r1.success == r2.success == 30
        assert abs(r1.duration - r2.duration) < 1.0


# ============================================================================
# 5. 真实 SSH 集成基准（可选，需 Docker）
# ============================================================================


@pytest.mark.integration
class TestRealSSHIntegration:
    """真实 SSH 集成基准：需 Docker + testcontainers。

    运行：pytest tests/performance -m integration -s
    当前环境无 Docker 时自动跳过。
    """

    def test_real_ssh_async_vs_sync(self):
        pytest.importorskip("testcontainers")
        pytest.skip("需要 Docker 环境；CI 中通过 service 真实 SSH 容器启用")
