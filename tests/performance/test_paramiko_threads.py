"""Benchmark Paramiko synchronous command resource costs.

The legacy ``SSHClient._read_output`` created a stderr-drain thread per
command, plus a Timer thread when a timeout was configured. The readiness-poll
implementation is evaluated at 100/500/1,000 simultaneous commands for thread
count, throughput, wall time, memory, and timeout reliability.

Run explicitly (benchmarks are excluded from the default suite)::

    pytest tests/performance/test_paramiko_threads.py -m benchmark -s

The high-inflight harness gates every worker at its first channel read. It uses
local fakes, not a remote SSH server; timings are observations rather than
pass/fail thresholds.
"""

from __future__ import annotations

import sys
import threading
import time
import tracemalloc
from unittest.mock import patch

import pytest

from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.service.batch_executor import BatchExecutor
from tests.performance.conftest import make_hosts, make_mock_service

pytestmark = pytest.mark.benchmark


class _FakeChannel:
    def __init__(
        self,
        exit_code: int = 0,
        stdout: bytes = b"OK\n",
        stderr: bytes = b"",
        read_delay: float = 0.0,
        gate: threading.Barrier | None = None,
        gate_release: threading.Event | None = None,
        status_ready: bool = True,
        gate_each_stream: bool = False,
    ) -> None:
        self._exit_code = exit_code
        self.stdout_buffer = bytearray(stdout)
        self.stderr_buffer = bytearray(stderr)
        self._read_delay = read_delay
        self._gate = gate
        self._gate_release = gate_release
        self._status_ready = status_ready
        self._gate_each_stream = gate_each_stream
        self._gated_streams: set[str] = set()
        self._gate_lock = threading.Lock()
        self._first_read = True
        self.closed_event = threading.Event()
        self.closed = False

    def recv_ready(self) -> bool:
        return bool(self.stdout_buffer)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr_buffer)

    def _before_read(self, stream: str) -> None:
        with self._gate_lock:
            first_read = self._first_read
            self._first_read = False
            should_gate = self._gate is not None and (
                (self._gate_each_stream and stream not in self._gated_streams)
                or (not self._gate_each_stream and not self._gated_streams)
            )
            self._gated_streams.add(stream)
        if first_read and self._read_delay:
            time.sleep(self._read_delay)
        if should_gate and self._gate is not None:
            self._gate.wait(timeout=15)
        if should_gate and self._gate_release is not None:
            self._gate_release.wait(timeout=15)

    def recv(self, size: int) -> bytes:
        self._before_read("stdout")
        chunk = bytes(self.stdout_buffer[:size])
        del self.stdout_buffer[:size]
        return chunk

    def recv_stderr(self, size: int) -> bytes:
        self._before_read("stderr")
        chunk = bytes(self.stderr_buffer[:size])
        del self.stderr_buffer[:size]
        return chunk

    def recv_exit_status(self) -> int:
        return self._exit_code

    def exit_status_ready(self) -> bool:
        return self._status_ready or self.closed

    def close(self) -> None:
        self.closed = True
        self.closed_event.set()


class _FakeStream:
    def __init__(self, channel: _FakeChannel, stream: str):
        self.channel = channel
        self._stream = stream

    def read(self) -> bytes:
        self.channel._before_read(self._stream)
        if self._stream == "stdout":
            buffer = self.channel.stdout_buffer
        else:
            buffer = self.channel.stderr_buffer
        if not buffer and not self.channel.exit_status_ready():
            self.channel.closed_event.wait(timeout=15)
        data = bytes(buffer)
        buffer.clear()
        return data


class _FakeParamikoClient:
    read_gate: threading.Barrier | None = None
    read_gate_release: threading.Event | None = None
    exec_gate: threading.Barrier | None = None
    silent_commands = False
    gate_each_stream = False

    def __init__(self, read_delay: float = 0.0) -> None:
        self._read_delay = read_delay

    def exec_command(self, command, **kwargs):  # noqa: ARG002
        if type(self).exec_gate is not None:
            type(self).exec_gate.wait(timeout=15)
        silent = type(self).silent_commands
        channel = _FakeChannel(
            stdout=b"" if silent else b"OK\n",
            read_delay=self._read_delay,
            gate=type(self).read_gate,
            gate_release=type(self).read_gate_release,
            status_ready=not silent,
            gate_each_stream=type(self).gate_each_stream,
        )
        stdout = _FakeStream(channel, "stdout")
        stderr = _FakeStream(channel, "stderr")
        return (None, stdout, stderr)

    def close(self) -> None:
        pass


class _HarnessClient(SSHClient):
    """Real SSHClient command path with a deterministic channel fake."""

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


class _LegacyHarnessClient(_HarnessClient):
    """Benchmark reference for the prior blocking stdout + stderr-thread drain."""

    def _read_output(
        self,
        stdout,
        stderr,
        timeout,
        *,
        timed_out_event=None,
        manage_timeout=True,
        deadline=None,
    ):
        del manage_timeout
        channel = stdout.channel
        timed_out = timed_out_event or threading.Event()
        stderr_data = bytearray()
        stderr_error = []

        def drain_stderr():
            try:
                stderr_data.extend(stderr.read())
            except BaseException as exc:  # noqa: BLE001 - legacy reference behavior
                stderr_error.append(exc)

        reader = threading.Thread(target=drain_stderr, name="ssh-stderr-drain", daemon=True)
        reader.start()
        try:
            stdout_data = stdout.read()
            reader.join(timeout=5)
            if timed_out.is_set():
                from remote_cmd.utils.exceptions import SSHCommandTimeoutError

                raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")
            if stderr_error:
                raise stderr_error[0]
            if deadline is not None:
                while not channel.exit_status_ready():
                    if time.monotonic() >= deadline or timed_out.is_set():
                        from remote_cmd.utils.exceptions import SSHCommandTimeoutError

                        raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")
                    time.sleep(0.01)
            exit_code = channel.recv_exit_status()
            return (
                exit_code,
                stdout_data.decode("utf-8", errors="replace"),
                bytes(stderr_data).decode("utf-8", errors="replace"),
            )
        finally:
            if reader.is_alive():
                reader.join(timeout=5)


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
            f"\n[Paramiko] zero-latency per-command overhead "
            f"(no reader thread + one timeout Timer): {per_command_us:.1f} us/command "
            f"({runs} runs)"
        )
        # 健全性上限：单命令线程成本不应接近毫秒级
        assert per_command_us < 5000


class TestPeakThreadsVsConcurrency:
    """Batch peaks should be bounded by workers plus one Timer per command."""

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
            f"\n[Paramiko] concurrency={concurrency:4d} read_delay={read_delay:.2f}s "
            f"peak_threads={peak['n']:4d} "
            f"(workers={concurrency}, +1 timeout Timer per in-flight command)"
        )
        assert peak["n"] <= concurrency * 2 + 20

    @pytest.mark.parametrize("concurrency", [100, 500, 1000])
    def test_high_inflight_command_thread_scale(self, concurrency):
        """Compare the legacy reader thread with one-worker dual-stream polling."""
        if sys.platform == "win32" and concurrency >= 500:
            pytest.skip("high thread stress is Linux/macOS only")
        _HarnessClient.read_delay = 0.0
        hosts = make_hosts(concurrency)
        service = make_mock_service(hosts)

        def measure(client_class, streams_per_command: int):
            at_gate = threading.Event()
            release = threading.Event()
            _FakeParamikoClient.read_gate = threading.Barrier(
                concurrency * streams_per_command,
                action=at_gate.set,
            )
            _FakeParamikoClient.read_gate_release = release
            _FakeParamikoClient.gate_each_stream = streams_per_command == 2
            peak = {"n": threading.active_count()}
            stop = threading.Event()
            outcome = {}

            def sampler() -> None:
                while not stop.is_set():
                    peak["n"] = max(peak["n"], threading.active_count())
                    stop.wait(0.001)

            sampler_thread = threading.Thread(target=sampler, daemon=True)
            sampler_thread.start()

            def run_batch():
                with patch("remote_cmd.service.batch_executor.SSHClient", client_class):
                    outcome["result"] = BatchExecutor(
                        host_service=service,
                        max_concurrency=concurrency,
                    ).execute([host.name for host in hosts], "uptime")

            batch_thread = threading.Thread(target=run_batch, daemon=True)
            tracemalloc.start()
            started = time.perf_counter()
            batch_thread.start()
            try:
                assert at_gate.wait(timeout=15), "not all command streams reached the gate"
                active = threading.enumerate()
                peak["n"] = max(peak["n"], len(active))
                workers = sum("ThreadPoolExecutor" in thread.name for thread in active)
                readers = sum(thread.name == "ssh-stderr-drain" for thread in active)
                timers = sum(isinstance(thread, threading.Timer) for thread in active)
            finally:
                release.set()
                batch_thread.join(timeout=15)
                stop.set()
                sampler_thread.join(timeout=2.0)
                _FakeParamikoClient.read_gate = None
                _FakeParamikoClient.read_gate_release = None
                _FakeParamikoClient.gate_each_stream = False
            elapsed = time.perf_counter() - started
            _current, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert not batch_thread.is_alive()
            result = outcome["result"]
            assert result.success == concurrency
            return {
                "threads": peak["n"],
                "workers": workers,
                "readers": readers,
                "timers": timers,
                "seconds": elapsed,
                "throughput": concurrency / elapsed,
                "peak_mib": peak_bytes / 1024 / 1024,
                "total": result.total,
            }

        legacy = measure(_LegacyHarnessClient, streams_per_command=2)
        polling = measure(_HarnessClient, streams_per_command=1)
        print(f"\n[Paramiko legacy] concurrency={concurrency} {legacy}")
        print(f"[Paramiko polling] concurrency={concurrency} {polling}")
        assert legacy["total"] == polling["total"] == concurrency
        assert polling["readers"] == 0
        assert polling["threads"] <= concurrency * 2 + 20

    @pytest.mark.parametrize("concurrency", [100, 500, 1000])
    def test_concurrent_silent_command_timeouts(self, concurrency):
        """Both drain designs enforce wall-clock timeout at high in-flight counts."""
        if sys.platform == "win32" and concurrency >= 500:
            pytest.skip("high thread stress is Linux/macOS only")
        _HarnessClient.read_delay = 0.0
        _FakeParamikoClient.silent_commands = True
        hosts = make_hosts(concurrency)
        service = make_mock_service(hosts)

        def run_batch(batch_runner, client_class, outcome_holder):
            with patch("remote_cmd.service.batch_executor.SSHClient", client_class):
                outcome_holder["result"] = batch_runner.execute(
                    [host.name for host in hosts], "sleep forever"
                )

        try:
            for variant in (_LegacyHarnessClient, _HarnessClient):
                batch = BatchExecutor(
                    service,
                    max_concurrency=concurrency,
                    command_timeout=0.15,
                )
                outcome = {}

                started = time.perf_counter()
                worker = threading.Thread(
                    target=run_batch,
                    args=(batch, variant, outcome),
                    daemon=True,
                )
                worker.start()
                worker.join(timeout=10)
                elapsed = time.perf_counter() - started

                assert not worker.is_alive()
                result = outcome["result"]
                assert result.failed == concurrency
                assert all(
                    "timed out" in (host_result.error or "")
                    for host_result in result.results.values()
                )
                max_duration = max(host_result.duration for host_result in result.results.values())
                print(
                    f"\n[Paramiko timeout reliability] variant={variant.__name__} "
                    f"concurrency={concurrency} wall={elapsed:.3f}s "
                    f"max_command={max_duration:.3f}s timed_out={result.failed}/{result.total}"
                )
                assert 0.1 <= max_duration < 2.0
                assert elapsed < 10.0
        finally:
            _FakeParamikoClient.silent_commands = False
            _FakeParamikoClient.exec_gate = None
