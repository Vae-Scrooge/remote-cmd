"""异步批量命令执行器（基于 asyncio + asyncssh 原生异步实现）。

与 `service.batch_executor.BatchExecutor`（基于 ThreadPoolExecutor + 同步
Paramiko）相比，本执行器使用 `asyncio.Semaphore` 控制并发，并在事件循环上
真正并发地执行 SSH 命令，避免线程池上下文切换开销。

数据契约：
- 复用 `BatchResult` / `BatchHostResult`（与同步实现字段完全一致），便于上层
  无差别消费结果。
- Host 解析仍委托给 `HostService`，保持与同步执行器一致的凭据/仓储链路。

后续集成：
- `BatchExecutor` 在构造时接受 `use_async` 开关；开启后，同步 `execute` 将内部
  调用 `asyncio.run(self._async_executor.execute(...))` 完成"同步接口 + 异步内核"
  的平滑切换（详见 `service/batch_executor.py`）。
"""

import asyncio
import logging
import time
from typing import Optional

from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.service.batch_executor import (
    BatchHostResult,
    BatchResult,
    ProgressCallback,
)
from remote_cmd.service.host_service import HostService

logger = logging.getLogger(__name__)


class AsyncBatchExecutor:
    """异步批量命令执行器。

    Args:
        host_service: HostService 实例（提供主机配置与凭据解析）
        max_concurrency: 最大并发主机数，默认 10
        command_timeout: 单条命令超时（秒），默认 30
    """

    def __init__(
        self,
        host_service: HostService,
        max_concurrency: int = 10,
        command_timeout: int = 30,
    ) -> None:
        self._host_service = host_service
        self._max_concurrency = max_concurrency
        self._command_timeout = command_timeout

    async def execute(
        self,
        host_names: list[str],
        command: str,
        retry_count: int = 0,
        retry_delay: float = 1.0,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> BatchResult:
        """在多台主机上异步并发执行同一命令。

        Args:
            host_names: 主机名称列表
            command: 要执行的命令
            retry_count: 失败重试次数（默认 0）
            retry_delay: 重试间隔（秒）
            progress_callback: 进度回调，签名为 `(completed, total, host_name)`；
                回调可为同步或 async 函数（async 时会被 await）。

        Returns:
            BatchResult: 批量结果（与同步 BatchExecutor 完全一致）

        Raises:
            ValueError: host_names 为空
        """
        if not host_names:
            raise ValueError("host_names must not be empty")

        total = len(host_names)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        start = time.time()

        logger.info(
            "异步批量执行开始: %s 台主机, 并发=%s, 命令='%s'",
            total, self._max_concurrency, command,
        )

        completed_counter = 0
        completed_lock = asyncio.Lock()
        results: dict[str, BatchHostResult] = {}

        async def _per_host(name: str) -> None:
            nonlocal completed_counter
            async with semaphore:
                result = await self._execute_on_host(name, command, retry_count, retry_delay)
            async with completed_lock:
                results[name] = result
                completed = completed_counter + 1
                completed_counter = completed

                if progress_callback is not None:
                    rv = progress_callback(completed, total, name)
                    if asyncio.iscoroutine(rv):
                        await rv

                logger.debug(
                    "[%s/%s] %s: %s (%.1fs)",
                    completed, total, name,
                    "✓" if result.success else "✗",
                    result.duration,
                )

        tasks = [asyncio.create_task(_per_host(n)) for n in host_names]
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.warning("batch execution interrupted by user")
            await self._cancel_and_mark_interrupted(tasks, host_names, results, command)

        duration = time.time() - start
        success_count = sum(1 for r in results.values() if r.success)
        failed_count = total - success_count
        logger.info("async batch execution finished: %s/%s succeeded, took %.1fs", success_count, total, duration)

        return BatchResult(
            total=total,
            success=success_count,
            failed=failed_count,
            duration=duration,
            results=results,
        )

    async def _cancel_and_mark_interrupted(
        self,
        tasks: list[asyncio.Task],
        host_names: list[str],
        results: dict[str, BatchHostResult],
        command: str,
    ) -> None:
        """用户中断时取消所有任务，并为未完成主机创建失败记录。"""
        for t in tasks:
            t.cancel()
        # 等待取消完成，避免 pending task 告警
        await asyncio.gather(*tasks, return_exceptions=True)
        # 为尚未有结果的主机创建失败记录
        for name in host_names:
            if name not in results:
                results[name] = BatchHostResult(
                    host=name,
                    success=False,
                    command=command,
                    error="user interrupted",
                )

    async def _execute_on_host(
        self,
        host_name: str,
        command: str,
        retry_count: int,
        retry_delay: float,
    ) -> BatchHostResult:
        """在单台主机上异步执行命令（含重试逻辑）。"""
        # 主机解析
        try:
            host: Host = self._host_service.resolve_host(host_name)
        except KeyError as e:
            return BatchHostResult(
                host=host_name, success=False, command=command,
                error=f"host not found: {e}",
            )
        except (RuntimeError, OSError) as e:
            return BatchHostResult(
                host=host_name, success=False, command=command,
                error=f"host resolution failed: {e}",
            )

        last_error: Optional[str] = None
        for attempt in range(retry_count + 1):
            start = time.time()
            try:
                config = ConnectionConfig(
                    hostname=host.hostname,
                    username=host.username,
                    port=host.port,
                    password=host.password,
                    key_filename=host.key_filename,
                    timeout=self._command_timeout,
                )
                async with AsyncSSHClient(config) as client:
                    cmd_result = await client.execute(
                        command, timeout=self._command_timeout,
                    )
                duration = time.time() - start
                return BatchHostResult(
                    host=host_name,
                    success=cmd_result.success,
                    command=command,
                    stdout=cmd_result.stdout,
                    stderr=cmd_result.stderr,
                    exit_code=cmd_result.exit_code,
                    duration=duration,
                )
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                logger.debug(
                    "%s 第 %s/%s 次尝试失败: %s",
                    host_name, attempt + 1, retry_count + 1, e,
                )
                if attempt < retry_count:
                    await asyncio.sleep(retry_delay)

        return BatchHostResult(
            host=host_name, success=False, command=command,
            error=last_error, duration=0.0,
        )


__all__ = ["AsyncBatchExecutor"]
