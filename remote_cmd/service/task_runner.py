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
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


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
    使用 threading.Thread 执行后台任务，通过 Semaphore 控制并发。

    Args:
        max_workers: 最大并发任务数，默认 10
    """

    def __init__(self, max_workers: int = 10) -> None:
        self._max_workers = max_workers
        self._tasks: dict[str, Task] = {}
        self._events: dict[str, threading.Event] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._semaphore = threading.Semaphore(max_workers)
        self._lock = threading.Lock()

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
            metadata=metadata or {},
        )

        with self._lock:
            self._tasks[task_id] = task
            self._events[task_id] = threading.Event()
            self._cancel_flags[task_id] = threading.Event()

        # 获取信号量后再启动线程（限制并发）
        self._semaphore.acquire()

        # 若在等待信号量期间任务已被取消，放弃启动并归还槽位。
        # （cancel(PENDING) 只 set cancel_flag 不 release，槽位配对关系：
        #  submit.acquire() ↔ 本处 release 或 _execute_wrapper.finally）
        if self._cancel_flags.get(task_id, threading.Event()).is_set():
            self._semaphore.release()
            logger.info(f"task cancelled before scheduling: [{task_id[:8]}] {task.name}")
            return task_id

        thread = threading.Thread(
            target=self._execute_wrapper,
            args=(task_id, fn, args, kwargs),
            daemon=True,
            name=f"task-{name[:16]}-{task_id[:8]}",
        )
        thread.start()

        logger.info(f"task submitted: [{task_id[:8]}] {name}")
        return task_id

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
            # 返回副本避免外部修改
            import dataclasses

            return dataclasses.replace(task)

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
            tasks = list(self._tasks.values())

        # 按创建时间降序排序
        tasks.sort(key=lambda t: t.created_at, reverse=True)

        # 按状态筛选
        if status:
            tasks = [t for t in tasks if t.status == status]

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

        if not event.wait(timeout=timeout):
            raise TimeoutError(f"Timeout waiting for task '{task_id[:8]}'")

        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id[:8]}' not found")
        return task

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
        return self._max_workers - self._semaphore._value  # noqa: SLF001

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
        fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """
        任务执行包装器

        负责状态转换、取消检查、异常处理和资源释放。
        """
        if self._check_cancelled_before_start(task_id):
            return

        self._mark_running(task_id)

        try:
            result = self._run_task_function(task_id, fn, args, kwargs)
            self._handle_completion(task_id, result)
        except Exception as e:  # noqa: BLE001
            self._handle_exception(task_id, e)
        finally:
            self._cleanup_resources(task_id)

    def _check_cancelled_before_start(self, task_id: str) -> bool:
        """检查任务在启动前是否已被取消（竞态窗口处理）。

        Returns:
            bool: True if task was cancelled and cleanup done, False to continue
        """
        if not self._cancel_flags.get(task_id, threading.Event()).is_set():
            return False

        self._semaphore.release()
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now()
        if task_id in self._events:
            self._events[task_id].set()
        return True

    def _mark_running(self, task_id: str) -> None:
        """将任务标记为运行中状态"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()

    def _run_task_function(self, task_id: str, fn: Callable, args: tuple, kwargs: dict) -> Any:
        """执行任务函数，返回结果"""
        logger.debug(f"task started: [{task_id[:8]}] running...")
        return fn(*args, **kwargs)

    def _handle_completion(self, task_id: str, result: Any) -> None:
        """处理任务正常完成（成功或被取消）"""
        if self._cancel_flags.get(task_id, threading.Event()).is_set():
            with self._lock:
                task = self._tasks.get(task_id)
                if task:
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now()
            logger.info(f"task was cancelled: [{task_id[:8]}]")
        else:
            with self._lock:
                task = self._tasks.get(task_id)
                if task:
                    task.result = result
                    task.status = TaskStatus.SUCCESS
                    task.completed_at = datetime.now()
            logger.info(f"task finished: [{task_id[:8]}]")

    def _handle_exception(self, task_id: str, exc: Exception) -> None:
        """处理任务执行异常"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status != TaskStatus.CANCELLED:
                task.error = str(exc)
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
        logger.error(f"task failed: [{task_id[:8]}] {exc}")

    def _cleanup_resources(self, task_id: str) -> None:
        """清理资源：释放信号量、触发完成事件"""
        self._semaphore.release()
        if task_id in self._events:
            self._events[task_id].set()


__all__ = ["TaskRunner", "Task", "TaskStatus"]
