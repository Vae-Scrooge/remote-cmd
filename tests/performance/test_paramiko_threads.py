"""P2.4 基准：Paramiko 同步执行路径的每命令线程开销量化。

背景（v2.7 决策依据）
====================
``SSHClient._read_output`` 为每个命令创建一个 stderr 排空线程
（防大输出死锁所必需），并在设置 timeout 时额外创建一个 Timer 线程。
N 个并发命令的瞬时线程数因此约为 ``N(执行器 worker) + 2N``。

本基准量化三件事，为"是否值得重写为集中式 channel polling"提供数据：

1. 每命令线程创建数（确定性；由 tests/test_ssh_client.py 锁定回归）
2. 零延迟下单命令 ``execute`` 的墙钟开销（线程创建/join 成本）
3. 批量执行在不同并发下的**峰值线程数**（与并发的线性关系）

运行
====
默认不运行（pyproject addopts 已排除 `benchmark` 标记）。

    pytest tests/performance/test_paramiko_threads.py -m benchmark -s -p no:cacheprovider

指标说明
========
- ``peak_threads``：执行期间 ``threading.active_count()`` 峰值（2ms 采样）
- ``us/command``：零延迟下包含真实线程创建/join 的单命令开销

实测基线（2026-09-13，Linux，Python 3.14，``_HarnessClient`` 假传输）
====================================================================
::

    zero-latency per-command overhead            ~100 us/command

    concurrency   peak_threads @10ms   peak_threads @50ms
    10                     32                 32
    50                    101                152
    100                   124                302
    250                   160                397

结论（v2.7 决策记录）：持续并发 <250 时维持每命令 2 线程（stderr + timer）
的现有模型；持续并发 >=250 时再评估集中式 channel polling 重写。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.service.batch_executor import BatchExecutor
from tests.performance.conftest import make_hosts, make_mock_service

pytestmark = pytest.mark.benchmark


class _FakeChannel:
    def __init__(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.closed = False

    def recv_exit_status(self) -> int:
        return self._exit_code

    def close(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, data: bytes = b"", channel: _FakeChannel | None = None, delay: float = 0.0):
        self._data = data
        self.channel = channel
        self._delay = delay

    def read(self) -> bytes:
        if self._delay:
            time.sleep(self._delay)
        return self._data


class _FakeParamikoClient:
    def __init__(self, read_delay: float = 0.0) -> None:
        self._read_delay = read_delay

    def exec_command(self, command, **kwargs):  # noqa: ARG002
        channel = _FakeChannel()
        stdout = _FakeStream(b"OK\n", channel=channel, delay=self._read_delay)
        stderr = _FakeStream(b"", delay=self._read_delay)
        return (None, stdout, stderr)

    def close(self) -> None:
        pass


class _HarnessClient(SSHClient):
    """真实 SSHClient 子类：仅替换传输层，_read_output 走真实线程路径。"""

    read_delay = 0.0

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._client = _FakeParamikoClient(read_delay=type(self).read_delay)

    def connect(self) -> _HarnessClient:
        return self

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True


class TestPerCommandThreadCost:
    """单命令开销：线程创建/join 的墙钟成本。"""

    def test_zero_latency_command_overhead(self):
        _HarnessClient.read_delay = 0.0
        client = _HarnessClient(ConnectionConfig(hostname="h", username="u"))

        client.execute("true")  # 预热（线程创建路径缓存/类加载）
        runs = 200
        start = time.perf_counter()
        for _ in range(runs):
            client.execute("true")
        elapsed = time.perf_counter() - start
        per_command_us = elapsed / runs * 1e6

        print(
            f"\n[P2.4] zero-latency per-command overhead "
            f"(1 stderr thread + 1 timer): {per_command_us:.1f} us/command "
            f"({runs} runs)"
        )
        # 健全性上限：单命令线程成本不应接近毫秒级
        assert per_command_us < 5000


class TestPeakThreadsVsConcurrency:
    """批量执行峰值线程数：worker + 每在飞命令 2 线程的线性关系。"""

    @pytest.mark.parametrize("read_delay", [0.01, 0.05])
    @pytest.mark.parametrize("concurrency", [10, 50, 100, 250])
    def test_peak_threads_scales_linearly(self, concurrency, read_delay):
        _HarnessClient.read_delay = read_delay  # 保持命令在飞以观测峰值
        hosts = make_hosts(concurrency)
        service = make_mock_service(hosts)

        peak = {"n": threading.active_count()}
        stop = threading.Event()

        def sampler() -> None:
            while not stop.is_set():
                peak["n"] = max(peak["n"], threading.active_count())
                time.sleep(0.002)

        sampler_thread = threading.Thread(target=sampler, daemon=True)
        sampler_thread.start()
        try:
            with patch("remote_cmd.service.batch_executor.SSHClient", _HarnessClient):
                executor = BatchExecutor(host_service=service, max_concurrency=concurrency)
                result = executor.execute([h.name for h in hosts], "uptime")
        finally:
            stop.set()
            sampler_thread.join(timeout=1.0)

        assert result.success == concurrency
        print(
            f"\n[P2.4] concurrency={concurrency:4d} read_delay={read_delay:.2f}s "
            f"peak_threads={peak['n']:4d} "
            f"(workers={concurrency}, +2 per in-flight command)"
        )
        # 每个命令最多 2 个瞬时线程（stderr + timer）；峰值不应超过
        # worker 数 + 2*并发 + 采样/主线程等余量
        assert peak["n"] <= concurrency * 3 + 20
