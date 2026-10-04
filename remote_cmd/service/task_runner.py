"""
任务运行器模块

提供后台任务执行、状态跟踪和取消能力。
线程安全，支持并发限制和任务超时等待。

用法:
    >>> from remote_cmd.service.task_runner import TaskRunner, TaskStatus
    >>>
    >>> runner = TaskRunner(max_workers=5)
    >>> task_id = runner.submit("deploy", deploy_fn, host="web-1")
    >>>
    >>> # 查询状态
    >>> status = runner.get_status(task_id)
    >>> print(status)  # TaskStatus.PENDING
    >>>
    >>> # 等待完成
    >>> task = runner.wait_for(task_id, timeout=60.0)
    >>> print(task.status, task.result)
"""

import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from types import TracebackType
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_WORKER_IDLE_TIMEOUT = 0.25


class TaskStatus(str, Enum):
    """任务状态枚举"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    def __str__(self) -> str:
        return self.value


@dataclass
class Task:
    """
    任务数据类

    Attributes:
        id: 任务 ID（UUID）
        name: 任务名称
        status: 任务状态
        created_at: 创建时间
        started_at: 开始时间
        completed_at: 完成时间
        result: 任务结果
        error: 错误信息
        metadata: 附加元数据
    """

    id: str
    name: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Any = None
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskRunner:
    """
    后台任务运行器

    支持任务提交、取消、状态查询和等待完成。
    使用 bounded work queue 和可复用 daemon workers 执行后台任务，通过
    Semaphore 保持提交端背压和最大并发语义。空闲 worker 会在短暂空闲后退出，
    避免 TaskRunner 实例长期持有线程。

    Args:
        max_workers: 最大并发任务数，默认 10
    """

    def __init__(self, max_workers: int = 10) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError(f"max_workers must be a positive integer, got: {max_workers!r}")
        self._max_workers = max_workers
        self._tasks: dict[str, Task] = {}
        self._events: dict[str, threading.Event] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._waiters: dict[str, int] = {}
        self._slot_tasks: set[str] = set()
        self._active_count = 0
        self._submitter_count = 0
        self._semaphore = threading.Semaphore(max_workers)
        self._lock = threading.Lock()
        self._idle_condition = threading.Condition(self._lock)
        self._work_queue: queue.Queue[
            tuple[str, Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = queue.Queue(maxsize=max_workers)
        self._workers: set[threading.Thread] = set()
        self._worker_count = 0
        self._worker_sequence = 0
        self._closed = False

    # ========================================================================
    # 任务管理
    # ========================================================================

    def submit(
        self,
        name: str,
        fn: Callable[..., Any],
        *args: Any,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> str:
        """
        提交一个后台任务

        Args:
            name: 任务名称（用于显示和日志）
            fn: 要执行的函数
            *args: 函数参数
            metadata: 任务元数据（可选）
            **kwargs: 函数关键字参数

        Returns:
            str: 任务 ID

        Raises:
            ValueError: 任务名称不能为空
        """
        if not name:
            raise ValueError("task name must not be empty")

        task_id = uuid.uuid4().hex
        task = Task(
            id=task_id,
            name=name,
            status=TaskStatus.PENDING,
            created_at=datetime.now(),
            metadata=dict(metadata) if metadata is not None else {},
        )

        with self._idle_condition:
            if self._closed:
                raise RuntimeError("TaskRunner is closed")
            self._tasks[task_id] = task
            self._events[task_id] = threading.Event()
            self._cancel_flags[task_id] = threading.Event()
            self._submitter_count += 1

        # 获取信号量后再启动线程（限制并发）。active_count 使用受锁保护的
        # 显式计数，不依赖 Semaphore 私有的 _value 字段。
        registered_submitter = True
        try:
            try:
                self._semaphore.acquire()
            except BaseException as exc:
                with self._idle_condition:
                    current = self._tasks.get(task_id)
                    if current is not None and current.status == TaskStatus.PENDING:
                        current.completed_at = datetime.now()
                        if isinstance(exc, Exception):
                            current.status = TaskStatus.FAILED
                            current.error = str(exc)
                        else:
                            current.status = TaskStatus.CANCELLED
                        self._events[task_id].set()
                    self._submitter_count -= 1
                    registered_submitter = False
                    self._idle_condition.notify_all()
                raise

            with self._lock:
                self._active_count += 1
                self._slot_tasks.add(task_id)
                task_was_cancelled = (
                    task_id not in self._tasks
                    or self._tasks[task_id].status == TaskStatus.CANCELLED
                    or self._cancel_flags[task_id].is_set()
                    or self._closed
                )
                start_error: Optional[BaseException] = None
                if not task_was_cancelled:
                    try:
                        self._ensure_workers_locked(min(self._max_workers, self._active_count))
                        self._work_queue.put_nowait((task_id, fn, args, kwargs))
                    except BaseException as exc:  # noqa: BLE001 - preserve slot/status on startup failure
                        start_error = exc
                        current = self._tasks.get(task_id)
                        if current is not None:
                            current.completed_at = datetime.now()
                            if isinstance(exc, Exception):
                                current.status = TaskStatus.FAILED
                                current.error = str(exc)
                            else:
                                current.status = TaskStatus.CANCELLED
                            self._events[task_id].set()

            # 若任务在等待信号量期间已被取消，放弃启动并归还唯一槽位。
            if task_was_cancelled:
                self._release_slot(task_id)
                logger.info(f"task cancelled before scheduling: [{task_id[:8]}] {task.name}")
                return task_id
            if start_error is not None:
                self._release_slot(task_id)
                raise start_error

            logger.info(f"task submitted: [{task_id[:8]}] {name}")
            return task_id
        finally:
            if registered_submitter:
                with self._idle_condition:
                    self._submitter_count -= 1
                    self._idle_condition.notify_all()

    def close(self, wait: bool = True) -> None:
        """Stop accepting work, cancel queued PENDING tasks, and retire workers.

        Running functions remain cooperative: they finish naturally, and with
        ``wait=True`` this method waits for them before returning. Workers are
        daemon threads and also retire automatically after a short idle period,
        so callers which don't need deterministic shutdown retain the previous
        fire-and-forget lifecycle.
        """
        current = threading.current_thread()
        with self._idle_condition:
            if not self._closed:
                self._closed = True
                for task_id, task in self._tasks.items():
                    if task.status == TaskStatus.PENDING:
                        task.status = TaskStatus.CANCELLED
                        task.completed_at = datetime.now()
                        self._cancel_flags[task_id].set()
                        self._events[task_id].set()
            # A task cannot wait for its own worker to exit. In that case close
            # still stops future work and lets the current worker retire on its
            # next idle iteration.
            if wait and current not in self._workers:
                while self._active_count or self._submitter_count:
                    self._idle_condition.wait()
            workers = tuple(self._workers)

        if wait:
            for worker in workers:
                if worker is not current:
                    worker.join()

    def __enter__(self) -> "TaskRunner":
        with self._lock:
            if self._closed:
                raise RuntimeError("TaskRunner is closed")
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.close(wait=True)

    def cancel(self, task_id: str) -> bool:
        """
        取消一个任务

        对于 PENDING 状态的任务直接标记取消。
        对于 RUNNING 状态的任务设置取消标志（需要函数内部检查）。

        Args:
            task_id: 任务 ID

        Returns:
            bool: True if the cancel succeeded
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False

            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
                # 设置取消标志：阻塞在信号量 acquire 的 submit 线程在拿到
                # 槽位后会检查该标志，发现已取消则放弃启动并归还槽位。
                # 注意：此处不 release 信号量 —— 槽位配对关系为
                # submit.acquire() ↔ (_execute_wrapper.finally 或 submit 放弃时)
                # 的 release，cancel 介入会破坏对称性导致双 release（P0-C）。
                if task_id in self._cancel_flags:
                    self._cancel_flags[task_id].set()
                if task_id in self._events:
                    self._events[task_id].set()
                logger.info(f"task cancelled: [{task_id[:8]}] {task.name}")
                return True

            if task.status == TaskStatus.RUNNING:
                # 设置取消标志
                if task_id in self._cancel_flags:
                    self._cancel_flags[task_id].set()
                logger.info(f"cancelling task: [{task_id[:8]}] {task.name}")
                return True

            return False

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取任务信息

        Args:
            task_id: 任务 ID

        Returns:
            Optional[Task]: 任务对象，不存在时返回 None
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return self._snapshot_task(task)

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """
        获取任务状态

        Args:
            task_id: 任务 ID

        Returns:
            Optional[TaskStatus]: 任务状态，不存在时返回 None
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return task.status if task else None

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> list[Task]:
        """
        列出任务

        Args:
            status: 按状态筛选（可选）
            limit: 最大返回数量，默认 50

        Returns:
            List[Task]: 任务列表（按创建时间降序）
        """
        with self._lock:
            tasks = [
                self._snapshot_task(task)
                for task in self._tasks.values()
                if status is None or task.status == status
            ]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def wait_for(self, task_id: str, timeout: Optional[float] = None) -> Task:
        """
        等待任务完成

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            Task: 已完成的任务

        Raises:
            TimeoutError: 等待超时
            KeyError: 任务不存在
        """
        with self._lock:
            if task_id not in self._events:
                raise KeyError(f"Task '{task_id[:8]}' not found")
            event = self._events[task_id]
            self._waiters[task_id] = self._waiters.get(task_id, 0) + 1

        try:
            if not event.wait(timeout=timeout):
                raise TimeoutError(f"Timeout waiting for task '{task_id[:8]}'")

            with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    raise KeyError(f"Task '{task_id[:8]}' not found")
                return self._snapshot_task(task)
        finally:
            with self._lock:
                waiters = self._waiters.get(task_id, 0) - 1
                if waiters > 0:
                    self._waiters[task_id] = waiters
                else:
                    self._waiters.pop(task_id, None)

    def cancel_all(self) -> int:
        """
        取消所有 PENDING 状态的任务

        Returns:
            int: 已取消的任务数
        """
        count = 0
        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now()
                    # 与 cancel(PENDING) 一致：只设标志不释放信号量。
                    # 阻塞在 acquire 的 submit 线程拿到槽位后会检查标志，
                    # 发现已取消则归还槽位并放弃启动（P0-C）。
                    if task_id in self._cancel_flags:
                        self._cancel_flags[task_id].set()
                    if task_id in self._events:
                        self._events[task_id].set()
                    count += 1

        if count > 0:
            logger.info(f"cancelled {count} pending tasks")
        return count

    def cleanup_old(self, max_age_seconds: int = 3600) -> int:
        """
        清理过期任务

        Args:
            max_age_seconds: 最大保留时间（秒），默认 1 小时

        Returns:
            int: 已清理的任务数
        """
        now = datetime.now()
        to_remove: list[str] = []

        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if (
                    task.status
                    in (
                        TaskStatus.SUCCESS,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    )
                    and task.completed_at
                    and not self._waiters.get(task_id, 0)
                ):
                    age = (now - task.completed_at).total_seconds()
                    if age > max_age_seconds:
                        to_remove.append(task_id)

            for task_id in to_remove:
                del self._tasks[task_id]
                self._events.pop(task_id, None)
                self._cancel_flags.pop(task_id, None)

        if to_remove:
            logger.debug(f"cleaned up {len(to_remove)} expired tasks")
        return len(to_remove)

    # ========================================================================
    # 属性
    # ========================================================================

    @property
    def active_count(self) -> int:
        """当前运行中的任务数"""
        with self._lock:
            return self._active_count

    @property
    def pending_count(self) -> int:
        """当前待处理的任务数"""
        count = 0
        with self._lock:
            for task in self._tasks.values():
                if task.status == TaskStatus.PENDING:
                    count += 1
        return count

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _execute_wrapper(
        self,
        task_id: str,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """
        任务执行包装器

        负责状态转换、取消检查、异常处理和资源释放。
        """
        if not self._try_mark_running(task_id):
            self._release_slot(task_id)
            return

        try:
            result = self._run_task_function(task_id, fn, args, kwargs)
            self._handle_completion(task_id, result)
        except BaseException as e:  # noqa: BLE001 - worker must not leave a task RUNNING
            self._handle_exception(task_id, e)
        finally:
            self._cleanup_resources(task_id)

    def _ensure_workers_locked(self, required: int) -> None:
        """Start enough persistent workers while the caller holds ``_lock``."""
        while self._worker_count < required:
            self._worker_sequence += 1
            worker = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"taskrunner-worker-{self._worker_sequence}",
            )
            self._worker_count += 1
            self._workers.add(worker)
            try:
                worker.start()
            except BaseException as exc:  # noqa: BLE001 - existing workers may still service this item
                if worker.ident is None:
                    self._worker_count -= 1
                    self._workers.discard(worker)
                if self._worker_count == 0:
                    raise
                logger.warning("could not start another TaskRunner worker: %s", exc)
                return

    def _worker_loop(self) -> None:
        """Consume bounded work items until idle expiry or explicit close."""
        current = threading.current_thread()
        registered = True
        try:
            while True:
                try:
                    item = self._work_queue.get(timeout=_WORKER_IDLE_TIMEOUT)
                except queue.Empty:
                    with self._lock:
                        if self._work_queue.empty() or self._closed:
                            self._workers.discard(current)
                            self._worker_count -= 1
                            registered = False
                            return
                    continue

                task_id, fn, args, kwargs = item
                try:
                    self._execute_wrapper(task_id, fn, args, kwargs)
                finally:
                    self._work_queue.task_done()
        finally:
            if registered:
                with self._lock:
                    self._workers.discard(current)
                    self._worker_count -= 1

    def _try_mark_running(self, task_id: str) -> bool:
        """原子执行 PENDING → RUNNING 或观察已取消状态。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status == TaskStatus.CANCELLED or self._cancel_flags[task_id].is_set():
                task.status = TaskStatus.CANCELLED
                task.completed_at = task.completed_at or datetime.now()
                self._events[task_id].set()
                return False
            if task.status != TaskStatus.PENDING:
                return False
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()
            return True

    def _run_task_function(
        self, task_id: str, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        """执行任务函数，返回结果"""
        logger.debug(f"task started: [{task_id[:8]}] running...")
        return fn(*args, **kwargs)

    def _handle_completion(self, task_id: str, result: Any) -> None:
        """处理任务正常完成（成功或被取消）"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                if self._cancel_flags[task_id].is_set():
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now()
                else:
                    task.result = result
                    task.status = TaskStatus.SUCCESS
                    task.completed_at = datetime.now()
        if task is not None and task.status == TaskStatus.CANCELLED:
            logger.info(f"task was cancelled: [{task_id[:8]}]")
        else:
            logger.info(f"task finished: [{task_id[:8]}]")

    def _handle_exception(self, task_id: str, exc: BaseException) -> None:
        """处理任务执行异常"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                if self._cancel_flags[task_id].is_set():
                    task.status = TaskStatus.CANCELLED
                elif task.status != TaskStatus.CANCELLED:
                    task.error = str(exc)
                    task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
        logger.error(f"task failed: [{task_id[:8]}] {exc}")

    def _cleanup_resources(self, task_id: str) -> None:
        """清理资源：释放信号量、触发完成事件"""
        self._release_slot(task_id)
        with self._lock:
            event = self._events.get(task_id)
            if event is not None:
                event.set()

    def _release_slot(self, task_id: str) -> None:
        """按 task id 恰好释放一次 worker 槽位。"""
        with self._idle_condition:
            if task_id not in self._slot_tasks:
                return
            self._slot_tasks.remove(task_id)
            self._active_count -= 1
            self._semaphore.release()
            self._idle_condition.notify_all()

    @staticmethod
    def _snapshot_task(task: Task) -> Task:
        """返回脱离内部状态的 Task 副本（metadata 至少浅复制一层）。"""
        return replace(task, metadata=dict(task.metadata))


__all__ = ["TaskRunner", "Task", "TaskStatus"]
