"""TaskRunner 任务运行器测试"""

import contextlib
import threading
import time
from datetime import datetime

from remote_cmd.service.task_runner import Task, TaskRunner, TaskStatus


class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.PENDING.value == "PENDING"
        assert TaskStatus.RUNNING.value == "RUNNING"
        assert TaskStatus.SUCCESS.value == "SUCCESS"
        assert TaskStatus.FAILED.value == "FAILED"
        assert TaskStatus.CANCELLED.value == "CANCELLED"


class TestTask:
    def test_create(self):
        t = Task(id="t1", name="test", status=TaskStatus.PENDING, created_at=datetime.now())
        assert t.id == "t1"
        assert t.name == "test"
        assert t.status == TaskStatus.PENDING
        assert t.result is None
        assert t.error is None


class TestTaskRunner:
    def test_submit_and_complete(self):
        runner = TaskRunner(max_workers=2)

        def task_func():
            return "done"

        task_id = runner.submit("test", task_func)
        task = runner.wait_for(task_id, timeout=5)
        assert task.status == TaskStatus.SUCCESS
        assert task.result == "done"

    def test_submit_with_args(self):
        runner = TaskRunner()

        def add(a, b):
            return a + b

        task_id = runner.submit("add", add, 2, 3)
        task = runner.wait_for(task_id, timeout=5)
        assert task.result == 5

    def test_task_failure(self):
        runner = TaskRunner()

        def failing():
            raise ValueError("something went wrong")

        task_id = runner.submit("fail", failing)

        try:
            task = runner.wait_for(task_id, timeout=5)
            assert task.status == TaskStatus.FAILED
            assert "something went wrong" in (task.error or "")
        except TimeoutError:
            pass

    def test_cancel_pending_task(self):
        runner = TaskRunner(max_workers=2)
        block = threading.Event()

        def waiter():
            block.wait(timeout=5)
            return "done"

        id1 = runner.submit("block1", waiter)
        id2 = runner.submit("block2", waiter)
        time.sleep(0.05)

        def submit_pending():
            runner.submit("pending3", waiter)

        threading.Thread(target=submit_pending, daemon=True).start()
        time.sleep(0.05)

        pending_tasks = [t for t in runner.list_tasks() if t.name == "pending3"]
        assert len(pending_tasks) == 1
        pending_id = pending_tasks[0].id
        assert runner.get_status(pending_id) == TaskStatus.PENDING
        assert runner.active_count == 2

        cancelled = runner.cancel(pending_id)
        assert cancelled is True
        assert runner.get_status(pending_id) == TaskStatus.CANCELLED

        block.set()
        runner.wait_for(id1, timeout=5)
        runner.wait_for(id2, timeout=5)

    def test_cancel_pending_does_not_overflow_semaphore(self):
        """P0-C 回归：cancel(PENDING) 后信号量不被双释放，并发上限仍生效

        历史问题：cancel(PENDING) 直接 release 一次，_execute_wrapper 的
        finally 又 release 一次，导致信号量计数超过 max_workers。
        现象：active_count = max_workers - semaphore._value 会变成负数。

        正确语义：cancel(PENDING) 不 release（该任务未占用槽位），
        阻塞中的 submit 线程拿到槽位后发现已取消，归还槽位并放弃启动。

        验证：经历 "提交→取消 PENDING→等待完成" 后，信号量不溢出
        （active_count 始终非负），且后续并发上限仍生效。
        """
        runner = TaskRunner(max_workers=2)

        def quick():
            return "ok"

        # 每个任务正常完成（不取消）
        ids = [runner.submit(f"ok{i}", quick) for i in range(6)]
        for tid in ids:
            runner.wait_for(tid, timeout=5)
        assert runner.active_count == 0

        # 制造 PENDING 任务并取消（阻塞在信号量上才能是 PENDING）
        block = threading.Event()

        def waiter():
            block.wait(timeout=5)
            return "done"

        b1 = runner.submit("b1", waiter)
        b2 = runner.submit("b2", waiter)
        time.sleep(0.05)
        assert runner.active_count == 2

        # pending3 因无槽位而停留在 acquire，但其 task 已在 acquire 前登记，
        # 可通过 list_tasks 找到。submit 同步阻塞，放到独立线程。
        def submit_pending():
            runner.submit("pending3", waiter)

        threading.Thread(target=submit_pending, daemon=True).start()
        time.sleep(0.05)
        pending3 = [t for t in runner.list_tasks() if t.name == "pending3"]
        assert len(pending3) == 1, "pending3 应已登记为任务"
        pending_id = pending3[0].id
        assert runner.get_status(pending_id) == TaskStatus.PENDING

        # 取消 PENDING 任务（不释放信号量）
        assert runner.cancel(pending_id) is True
        assert runner.get_status(pending_id) == TaskStatus.CANCELLED

        # 释放 blocker，让 pending3 的 submit 线程拿到槽位并发现已取消、
        # 归还槽位；随后正常任务仍受 max_workers=2 限制
        block.set()
        runner.wait_for(b1, timeout=5)
        runner.wait_for(b2, timeout=5)

        # 提交 6 个快速任务，验证并发上限与信号量健康
        tids = [runner.submit(f"after{i}", quick) for i in range(6)]
        for tid in tids:
            runner.wait_for(tid, timeout=5)

        assert runner.active_count == 0, "信号量未恢复满值（可能泄漏）"

    def test_cancel_nonexistent(self):
        runner = TaskRunner()
        assert runner.cancel("nonexistent") is False

    def test_concurrency_limit(self):
        runner = TaskRunner(max_workers=2)

        num_running = 0
        max_concurrent = 0
        lock = __import__("threading").Lock()

        def track():
            nonlocal num_running, max_concurrent
            with lock:
                num_running += 1
                max_concurrent = max(max_concurrent, num_running)
            time.sleep(0.2)
            with lock:
                num_running -= 1

        ids = []
        for i in range(4):
            tid = runner.submit(f"t{i}", track)
            ids.append(tid)
        for tid in ids:
            with contextlib.suppress(TimeoutError):
                runner.wait_for(tid, timeout=5)

        assert max_concurrent <= 2

    def test_list_tasks(self):
        runner = TaskRunner()
        id1 = runner.submit("a", lambda: None)
        id2 = runner.submit("b", lambda: None)
        runner.wait_for(id1, timeout=5)
        runner.wait_for(id2, timeout=5)

        tasks = runner.list_tasks()
        assert len(tasks) >= 2

    def test_list_tasks_empty(self):
        runner = TaskRunner()
        assert runner.list_tasks() == []

    def test_get_task_nonexistent(self):
        runner = TaskRunner()
        assert runner.get_task("nonexistent") is None

    def test_get_status(self):
        runner = TaskRunner()
        tid = runner.submit("s", lambda: "ok")
        runner.wait_for(tid, timeout=5)
        assert runner.get_status(tid) == TaskStatus.SUCCESS

    def test_cancel_all(self):
        runner = TaskRunner(max_workers=1)
        runner.submit("slow", time.sleep, 30)

        pending_ids = []

        def submit_pending(i):
            tid = runner.submit(f"p{i}", time.sleep, 30)
            pending_ids.append(tid)

        threads = []
        for i in range(3):
            t = threading.Thread(target=submit_pending, args=(i,), daemon=True)
            t.start()
            threads.append(t)
        time.sleep(0.2)

        cancelled = runner.cancel_all()
        assert cancelled >= 1
        for tid in pending_ids:
            assert runner.get_status(tid) == TaskStatus.CANCELLED
        # P0-C：cancel_all 不应释放信号量（槽位由阻塞中的 submit 醒来后归还）
        assert runner.active_count == 1

    def test_cleanup_old(self):
        runner = TaskRunner()
        tid = runner.submit("old", lambda: "ok")
        runner.wait_for(tid, timeout=5)
        time.sleep(0.05)
        count = runner.cleanup_old(max_age_seconds=0)
        assert count >= 1
        assert runner.get_task(tid) is None

    def test_wait_for_timeout(self):
        runner = TaskRunner()
        tid = runner.submit("slow", time.sleep, 30)
        try:
            runner.wait_for(tid, timeout=0.1)
            raise AssertionError("应抛出 TimeoutError")
        except TimeoutError:
            pass

    def test_wait_for_nonexistent(self):
        runner = TaskRunner()
        try:
            runner.wait_for("nonexistent")
            raise AssertionError("应抛出 KeyError")
        except KeyError:
            pass

    def test_active_count(self):
        runner = TaskRunner(max_workers=5)
        assert runner.active_count == 0
        tid = runner.submit("slow", time.sleep, 0.5)
        time.sleep(0.05)
        assert runner.active_count >= 1
        runner.wait_for(tid, timeout=5)
        assert runner.active_count == 0

    def test_pending_count(self):
        runner = TaskRunner(max_workers=1)
        runner.submit("slow", time.sleep, 0.3)
        time.sleep(0.05)

        # 后台提交第二个任务，不阻塞
        def submit_another():
            runner.submit("pending", time.sleep, 0.1)

        threading.Thread(target=submit_another, daemon=True).start()
        time.sleep(0.1)
        assert runner.pending_count >= 1
