"""Scale-oriented benchmarks for scheduling, storage, output, pools, and TaskRunner.

Run explicitly (these measurements are excluded from the default test command)::

    python -m pytest tests/performance/test_scalability.py -m benchmark -s -q

All SSH work is represented by deterministic local fakes. Timings are observations,
not pass/fail thresholds; hard assertions cover capacity, result shape, and resource
accounting. Memory is Python allocation peak from ``tracemalloc`` (not process RSS).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from remote_cmd.core.budget import ConnectionBudget
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import CommandResult
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.service._host_runner import to_host_result
from remote_cmd.service.batch_executor import BatchExecutor, BatchHostResult
from remote_cmd.service.task_runner import TaskRunner, TaskStatus

pytestmark = pytest.mark.benchmark


def _service(hosts: list[Host]):
    service = MagicMock()
    by_name = {host.name: host for host in hosts}

    def resolve(name: str) -> Host:
        if name not in by_name:
            raise KeyError(name)
        return by_name[name]

    service.resolve_host = resolve
    return service


@pytest.mark.parametrize("host_count", [10, 100, 1_000, 10_000, 50_000, 100_000])
def test_sync_batch_scheduling_scale(host_count: int) -> None:
    """Report elapsed time/memory and prove the pending Future set is bounded."""
    concurrency = min(64, host_count)
    metrics = {"submitted": 0, "pending": 0, "peak_pending": 0}
    lock = threading.Lock()

    class TrackingExecutor(RealThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            future = super().submit(fn, *args, **kwargs)
            with lock:
                metrics["submitted"] += 1
                metrics["pending"] += 1
                metrics["peak_pending"] = max(metrics["peak_pending"], metrics["pending"])

            def completed(_future):
                with lock:
                    metrics["pending"] -= 1

            future.add_done_callback(completed)
            return future

    executor = BatchExecutor(host_service=MagicMock(), max_concurrency=concurrency)

    def execute_one(host_name, command, _retry_count, _retry_delay, *_args):
        return BatchHostResult(host=host_name, success=True, command=command)

    executor._execute_on_host = MagicMock(side_effect=execute_one)
    host_names = (
        (f"srv{i}" for i in range(host_count))
        if host_count >= 50_000
        else [f"srv{i}" for i in range(host_count)]
    )
    tracemalloc.start()
    started = time.perf_counter()
    with patch("remote_cmd.service.batch_executor.ThreadPoolExecutor", TrackingExecutor):
        result = executor.execute(host_names, "true")
    elapsed = time.perf_counter() - started
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result.total == host_count
    assert result.success == host_count
    assert metrics["submitted"] == min(concurrency, host_count)
    assert metrics["peak_pending"] <= concurrency
    # The API retains one result per distinct host, so overall memory remains
    # O(N); this generous upper bound catches accidental scheduler O(N*C) growth.
    if host_count == 100_000:
        assert peak_bytes < 200 * 1024 * 1024
    print(
        f"\n[BATCH sync] hosts={host_count} input={'iterator' if host_count >= 50_000 else 'list'} "
        f"concurrency={concurrency} "
        f"worker_futures={metrics['submitted']} future_peak={metrics['peak_pending']} "
        f"peak_python={peak_bytes / 1024 / 1024:.2f}MiB "
        f"wall={elapsed:.4f}s"
    )


def test_legacy_unbounded_vs_bounded_batch_scheduler_memory() -> None:
    """Same 10k local hosts: compare retained Future count and Python peak memory."""
    host_count = 10_000
    concurrency = 64
    host_names = [f"srv{i}" for i in range(host_count)]

    def execute_one(name: str) -> BatchHostResult:
        return BatchHostResult(host=name, success=True, command="true")

    # Reference implementation of the previous submit-all scheduling pattern.
    tracemalloc.start()
    started = time.perf_counter()
    legacy_results: dict[str, BatchHostResult] = {}
    with RealThreadPoolExecutor(max_workers=concurrency) as executor:
        future_map = {executor.submit(execute_one, name): name for name in host_names}
        legacy_future_count = len(future_map)
        for future in as_completed(future_map):
            legacy_results[future_map[future]] = future.result()
    legacy_elapsed = time.perf_counter() - started
    _current, legacy_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Current executor uses the same output shape but holds at most C futures.
    executor = BatchExecutor(host_service=MagicMock(), max_concurrency=concurrency)
    executor._execute_on_host = MagicMock(
        side_effect=lambda name, command, *_args: BatchHostResult(
            host=name, success=True, command=command
        )
    )
    tracemalloc.start()
    started = time.perf_counter()
    bounded = executor.execute(host_names, "true")
    bounded_elapsed = time.perf_counter() - started
    _current, bounded_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(legacy_results) == bounded.total == host_count
    assert legacy_future_count == host_count
    assert bounded.success == host_count
    print(
        f"\n[BATCH 10k reference] future_count={legacy_future_count} "
        f"peak_python={legacy_peak / 1024 / 1024:.2f}MiB wall={legacy_elapsed:.4f}s"
    )
    print(
        f"[BATCH 10k bounded] future_limit={concurrency} "
        f"peak_python={bounded_peak / 1024 / 1024:.2f}MiB wall={bounded_elapsed:.4f}s"
    )


@pytest.mark.parametrize("output_bytes", [1024, 64 * 1024, 1024 * 1024, 10 * 1024 * 1024])
def test_output_retention_scale(output_bytes: int) -> None:
    """Record retained bytes and Python allocation peak for cap vs full retention."""
    retained_cap = 64 * 1024
    tracemalloc.start()
    raw = "x" * output_bytes
    result = to_host_result(
        "srv", "generate", CommandResult("generate", raw, raw, 0), 0.01, retained_cap
    )
    current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    retained = len(result.stdout.encode()) + len(result.stderr.encode())
    assert result.success
    assert len(result.stdout.encode()) <= retained_cap + 100
    print(
        f"\n[OUTPUT] stream={output_bytes}B retained_both={retained}B "
        f"current_python={current / 1024 / 1024:.2f}MiB peak_python={peak_bytes / 1024 / 1024:.2f}MiB"
    )


@pytest.mark.parametrize("host_count", [100, 1_000, 10_000])
def test_sqlite_storage_scale(host_count: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure high-level SQLite inserts/list/tag-filter at supported fleet sizes."""
    db_path = str(tmp_path / f"hosts-{host_count}.db")
    connection_count = 0
    original_connect = sqlite3.connect

    def counted_connect(*args, **kwargs):
        nonlocal connection_count
        connection_count += 1
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("remote_cmd.repository.sqlite_host_repository.sqlite3.connect", counted_connect)
    repo = SqliteHostRepository(db_path)
    tracemalloc.start()
    started = time.perf_counter()
    for index in range(host_count):
        repo.save(
            Host(
                name=f"srv{index:05}",
                hostname=f"10.0.{index // 255}.{index % 255}",
                username="deploy",
                tags=["fleet", f"shard-{index % 10}"],
            )
        )
    save_elapsed = time.perf_counter() - started
    read_started = time.perf_counter()
    selected = repo.list(tag="shard-3")
    total = repo.count()
    read_elapsed = time.perf_counter() - read_started
    search_started = time.perf_counter()
    matches = repo.search(f"srv{host_count - 1:05}")
    search_elapsed = time.perf_counter() - search_started
    current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert total == host_count
    assert len(selected) == sum(1 for index in range(host_count) if index % 10 == 3)
    assert len(matches) == 1
    assert connection_count == 1
    repo.close()
    print(
        f"\n[STORAGE sqlite] hosts={host_count} connections={connection_count} "
        f"save={save_elapsed:.3f}s tag_list+count={read_elapsed:.4f}s "
        f"substring_search={search_elapsed:.4f}s "
        f"current_python={current / 1024 / 1024:.2f}MiB peak_python={peak_bytes / 1024 / 1024:.2f}MiB"
    )


@pytest.mark.parametrize("host_count", [10, 100])
def test_pool_budget_connection_scale(host_count: int) -> None:
    """Compare direct, per-host pool, and shared-budget fake connection counts."""
    metrics_lock = threading.Lock()
    connection_wave = threading.Event()
    connection_wave_size = 1

    class FakeClient:
        def __init__(self, _config):
            self.connected = False

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.connected = False

        def is_connected(self):
            return self.connected

        def execute(self, command, timeout=None, environment=None):  # noqa: ARG002
            with metrics_lock:
                if metrics["live"] >= connection_wave_size:
                    connection_wave.set()
            if not connection_wave.wait(timeout=5):
                raise TimeoutError("benchmark connection wave did not fill")
            return CommandResult(command, "ok", "", 0)

    hosts = [Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="u") for i in range(host_count)]
    metrics: dict[str, int] = {"created": 0, "live": 0, "peak_live": 0}

    class CountingClient(FakeClient):
        def connect(self):
            super().connect()
            with metrics_lock:
                metrics["created"] += 1
                metrics["live"] += 1
                metrics["peak_live"] = max(metrics["peak_live"], metrics["live"])

        def disconnect(self):
            with metrics_lock:
                if self.connected:
                    metrics["live"] -= 1
            super().disconnect()

    service = _service(hosts)
    # One host/no retry uses a direct connection rather than a pool.
    direct = BatchExecutor(service, max_concurrency=1)
    with patch("remote_cmd.service.batch_executor.SSHClient", CountingClient):
        direct_result = direct.execute([hosts[0].name], "true")
    assert direct_result.success == 1
    direct_created = metrics["created"]

    for budget_limit in (None, 4):
        for key in metrics:
            metrics[key] = 0
        connection_wave.clear()
        connection_wave_size = min(8, budget_limit or 8, host_count)
        budget = ConnectionBudget(budget_limit) if budget_limit is not None else None
        pooled = BatchExecutor(service, max_concurrency=8, connection_budget=budget)
        with (
            patch("remote_cmd.service.batch_executor.SSHClient", CountingClient),
            patch("remote_cmd.core.sync_connection_pool.SSHClient", CountingClient),
        ):
            result = pooled.execute([host.name for host in hosts], "true")
        assert result.success == host_count
        assert metrics["live"] == 0
        cap = connection_wave_size
        assert metrics["peak_live"] <= cap
        assert metrics["peak_live"] == cap
        assert budget is None or budget.get_metrics()["in_use"] == 0
        print(
            f"\n[POOL per-host] hosts={host_count} direct_created={direct_created} "
            f"pooled_created={metrics['created']} peak_connections={metrics['peak_live']} "
            f"shared_budget={budget_limit if budget_limit else 'disabled'}"
        )


@pytest.mark.parametrize("task_count", [100, 1_000, 10_000])
def test_task_runner_thread_and_memory_scale(task_count: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quantify TaskRunner's per-task daemon-thread creation model."""
    import remote_cmd.service.task_runner as task_runner_module

    real_thread = threading.Thread
    state = {"started": 0, "active": 0, "peak": 0}
    state_lock = threading.Lock()

    class CountingThread(real_thread):
        def start(self):
            is_task_thread = self.name.startswith("taskrunner-worker-")
            if is_task_thread:
                with state_lock:
                    state["started"] += 1
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
            try:
                super().start()
            except BaseException:  # noqa: BLE001 - restore active-thread accounting on start error
                if is_task_thread:
                    with state_lock:
                        state["active"] -= 1
                raise

        def run(self):
            is_task_thread = self.name.startswith("taskrunner-worker-")
            try:
                super().run()
            finally:
                if is_task_thread:
                    with state_lock:
                        state["active"] -= 1

    monkeypatch.setattr(task_runner_module.threading, "Thread", CountingThread)
    runner = TaskRunner(max_workers=8)
    tracemalloc.start()
    started = time.perf_counter()
    task_ids = [runner.submit(f"task-{index}", lambda: None) for index in range(task_count)]
    for task_id in task_ids:
        runner.wait_for(task_id, timeout=10)
    elapsed = time.perf_counter() - started
    current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    worker_limit = min(8, task_count)
    assert state["started"] <= worker_limit
    assert state["started"] < task_count
    assert state["peak"] <= 8
    assert runner.active_count == 0
    assert all(runner.get_status(task_id) == TaskStatus.SUCCESS for task_id in task_ids)
    shutdown_started = time.perf_counter()
    runner.close()
    shutdown_elapsed = time.perf_counter() - shutdown_started
    assert runner._worker_count == 0
    print(
        f"\n[TASK RUNNER pooled] tasks={task_count} workers_started={state['started']} "
        f"peak_task_threads={state['peak']} wall={elapsed:.3f}s "
        f"shutdown={shutdown_elapsed:.3f}s current_python={current / 1024 / 1024:.2f}MiB "
        f"peak_python={peak_bytes / 1024 / 1024:.2f}MiB"
    )
