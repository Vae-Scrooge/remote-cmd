"""
批量命令执行器模块

支持多主机并发执行命令，带超时控制、失败重试和进度回调。
与现有的 HostService 和 SSHClient 集成。

用法:
    >>> from remote_cmd.service.batch_executor import BatchExecutor
    >>> from remote_cmd.service.host_service import HostService
    >>>
    >>> executor = BatchExecutor(host_service, max_concurrency=10)
    >>> result = executor.execute(
    ...     host_names=["web-1", "web-2", "db-1"],
    ...     command="uptime",
    ...     retry_count=2,
    ...     retry_delay=1.0,
    ... )
    >>> print(f"succeeded: {result.success}/{result.total}")
"""

import inspect
import logging
import time
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.core.sync_connection_pool import SyncConnectionPool
from remote_cmd.service.host_service import HostService

logger = logging.getLogger(__name__)

# 进度回调签名：completed, total, current_host_name；async 或 sync 均可
ProgressCallback = Callable[[int, int, str], Optional[Awaitable[None]]]


@dataclass
class BatchHostResult:
    """
    单个主机的批量执行结果

    Attributes:
        host: 主机名称
        success: whether the command succeeded
        command: 执行的命令
        stdout: 标准输出
        stderr: 标准错误
        exit_code: 退出码
        duration: execution took (seconds)
        error: 错误信息（如果有）
    """

    host: str
    success: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration: float = 0.0
    error: Optional[str] = None


@dataclass
class BatchResult:
    """
    批量执行汇总结果

    Attributes:
        total: 总主机数
        success: number of succeeded hosts
        failed: 失败主机数
        duration: total took (seconds)
        results: 按主机名索引的详细结果
    """

    total: int
    success: int
    failed: int
    duration: float
    results: dict[str, BatchHostResult] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """success rate (0.0 ~ 1.0)"""
        if self.total == 0:
            return 1.0
        return self.success / self.total

    @property
    def failed_hosts(self) -> list[str]:
        """失败主机列表"""
        return [h for h, r in self.results.items() if not r.success]

    @property
    def success_hosts(self) -> list[str]:
        """list of succeeded hosts"""
        return [h for h, r in self.results.items() if r.success]

    def summary(self) -> str:
        """生成可读的汇总字符串"""
        return (
            f"Total: {self.total}, "
            f"Succeeded: {self.success}, "
            f"Failed: {self.failed}, "
            f"Duration: {self.duration:.1f}s, "
            f"Success rate: {self.success_rate:.1%}"
        )


class BatchExecutor:
    """
    批量命令执行器

    支持多主机并发执行，带超时控制、失败重试和进度回调。

    Args:
        host_service: HostService 实例，用于获取主机配置和凭据
        max_concurrency: 最大并发数，默认 10
        command_timeout: 单个命令超时时间（秒），默认 30
        use_async: 是否启用异步内核（基于 asyncssh 的原生异步实现）。默认 False
            保持原有 ThreadPoolExecutor 行为；设为 True 时，execute 会在内部使用
            asyncio.run 调用 AsyncBatchExecutor 完成并发调度，从而在大规模场景下
            降低线程/CPU 开销。注意：启用时调用线程不应已运行 asyncio 事件循环。

    Note:
        无论 `use_async` 取值，对外 `execute` 始终为同步接口，返回类型一致，
        便于上层无差别切换。
    """

    def __init__(
        self,
        host_service: HostService,
        max_concurrency: int = 10,
        command_timeout: int = 30,
        use_async: bool = False,
    ):
        self._host_service = host_service
        self._max_concurrency = max_concurrency
        self._command_timeout = command_timeout
        self._use_async = use_async
        # 延迟导入以避免在未安装 asyncssh 的环境下的导入失败
        # 使用前向引用避免在模块加载期引入 asyncssh 硬依赖（开启 use_async 时才惰性导入）
        self._async_executor: Optional["AsyncBatchExecutor"] = None  # noqa: UP037
        if use_async:
            from remote_cmd.service.async_batch_executor import AsyncBatchExecutor

            self._async_executor = AsyncBatchExecutor(
                host_service=host_service,
                max_concurrency=max_concurrency,
                command_timeout=command_timeout,
            )

    def execute(
        self,
        host_names: list[str],
        command: str,
        retry_count: int = 0,
        retry_delay: float = 1.0,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> BatchResult:
        """
        在指定主机上批量执行命令

        Args:
            host_names: 要执行命令的主机名称列表
            command: 要执行的命令
            retry_count: 失败重试次数，默认 0（不重试）
            retry_delay: 重试间隔（秒），默认 1.0
            progress_callback: 进度回调，参数 (completed, total, current_host_name)。
                同步内核下回调应为同步函数；异步回调请使用 use_async=True。

        Returns:
            BatchResult: 批量执行结果

        Raises:
            ValueError: host_names 为空
        """
        if not host_names:
            raise ValueError("host_names must not be empty")

        # 异步内核委托路径：同步接口 + asyncio.run(异步实现)
        if self._async_executor is not None:
            return self._delegate_to_async(
                host_names, command, retry_count, retry_delay, progress_callback
            )

        return self._execute_sync(host_names, command, retry_count, retry_delay, progress_callback)

    def _delegate_to_async(
        self,
        host_names: list[str],
        command: str,
        retry_count: int,
        retry_delay: float,
        progress_callback: Optional[ProgressCallback],
    ) -> BatchResult:
        """异步内核委托路径：同步接口 + asyncio.run(异步实现)"""
        import asyncio

        return asyncio.run(
            self._async_executor.execute(
                host_names=host_names,
                command=command,
                retry_count=retry_count,
                retry_delay=retry_delay,
                progress_callback=progress_callback,
            )
        )

    def _execute_sync(
        self,
        host_names: list[str],
        command: str,
        retry_count: int,
        retry_delay: float,
        progress_callback: Optional[ProgressCallback],
    ) -> BatchResult:
        """同步路径：ThreadPoolExecutor + 连接池复用"""
        total = len(host_names)
        results: dict[str, BatchHostResult] = {}
        start_time = time.time()

        logger.info(
            f"batch execution started: {total} hosts, concurrency={self._max_concurrency}, command='{command}'"
        )

        pools: dict[str, SyncConnectionPool] = {}

        try:
            with ThreadPoolExecutor(max_workers=self._max_concurrency) as executor:
                future_map = self._submit_tasks(
                    executor, host_names, command, retry_count, retry_delay, pools, total
                )
                self._collect_results(
                    future_map, host_names, command, progress_callback, results, total
                )
        finally:
            self._cleanup_pools(pools)

        duration = time.time() - start_time
        return self._build_result(total, results, duration)

    def _submit_tasks(
        self,
        executor: ThreadPoolExecutor,
        host_names: list[str],
        command: str,
        retry_count: int,
        retry_delay: float,
        pools: dict[str, SyncConnectionPool],
        total: int,
    ) -> dict:
        """提交任务到线程池，返回 future_map"""
        future_map = {}
        for host_name in host_names:
            # 连接池：多主机或需重试时创建
            pool = None
            if retry_count > 0 or total > 1:
                pool = self._prepare_pool(host_name, pools)
            future = executor.submit(
                self._execute_on_host, host_name, command, retry_count, retry_delay, pool
            )
            future_map[future] = host_name
        return future_map

    def _prepare_pool(
        self, host_name: str, pools: dict[str, SyncConnectionPool]
    ) -> SyncConnectionPool:
        """为指定主机创建或获取连接池"""
        if host_name in pools:
            return pools[host_name]

        host = self._host_service.resolve_host(host_name)
        config = ConnectionConfig(
            hostname=host.hostname,
            username=host.username,
            port=host.port,
            password=host.password,
            key_filename=host.key_filename,
            timeout=self._command_timeout,
        )
        pool = SyncConnectionPool(
            config,
            max_connections=max(1, self._max_concurrency),
            client_factory=SSHClient,
        )
        pools[host_name] = pool
        return pool

    def _collect_results(
        self,
        future_map: dict,
        host_names: list[str],
        command: str,
        progress_callback: Optional[ProgressCallback],
        results: dict[str, BatchHostResult],
        total: int,
    ) -> int:
        """收集结果并处理进度回调与中断"""
        completed = 0
        try:
            for future in as_completed(future_map):
                host_name = future_map[future]
                result = self._process_future_result(future, host_name, command)
                results[host_name] = result
                completed += 1
                self._invoke_progress_callback(
                    progress_callback, completed, total, host_name, result
                )
        except KeyboardInterrupt:
            logger.warning("batch execution interrupted by user")
            self._handle_interrupt(future_map, host_names, command, results)
            completed = len(results)

        return completed

    def _process_future_result(
        self, future, host_name: str, command: str
    ) -> BatchHostResult:
        """处理单个 future 结果，捕获调度异常"""
        try:
            return future.result()
        except Exception as e:  # noqa: BLE001
            return BatchHostResult(
                host=host_name,
                success=False,
                command=command,
                error=f"scheduling error: {e}",
            )

    def _invoke_progress_callback(
        self,
        progress_callback: Optional[ProgressCallback],
        completed: int,
        total: int,
        host_name: str,
        result: BatchHostResult,
    ) -> None:
        """调用进度回调并记录日志"""
        if progress_callback:
            rv = progress_callback(completed, total, host_name)
            if inspect.isawaitable(rv):
                logger.warning("同步内核不支持异步进度回调，请使用 use_async=True")

        logger.debug(
            f"[{completed}/{total}] {host_name}: "
            f"{'✓' if result.success else '✗'} "
            f"({result.duration:.1f}s)"
        )

    def _handle_interrupt(
        self,
        future_map: dict,
        host_names: list[str],
        command: str,
        results: dict[str, BatchHostResult],
    ) -> None:
        """处理键盘中断：取消任务并为未完成主机创建失败记录"""
        # 取消所有未完成的任务
        for future in future_map:
            future.cancel()

        # 为尚未有结果的主机创建失败记录
        for host_name in host_names:
            if host_name not in results:
                results[host_name] = BatchHostResult(
                    host=host_name,
                    success=False,
                    command=command,
                    error="user interrupted",
                )

    def _cleanup_pools(self, pools: dict[str, SyncConnectionPool]) -> None:
        """关闭本批次创建的所有连接池"""
        for pool in pools.values():
            pool.close_all()

    def _build_result(
        self, total: int, results: dict[str, BatchHostResult], duration: float
    ) -> BatchResult:
        """构建批量执行汇总结果"""
        success_count = sum(1 for r in results.values() if r.success)
        failed_count = total - success_count

        logger.info(
            f"batch execution finished: {success_count}/{total} succeeded, took {duration:.1f}s"
        )

        return BatchResult(
            total=total,
            success=success_count,
            failed=failed_count,
            duration=duration,
            results=results,
        )

    def _execute_on_host(
        self,
        host_name: str,
        command: str,
        retry_count: int,
        retry_delay: float,
        pool: Optional[SyncConnectionPool] = None,
    ) -> BatchHostResult:
        """
        在单台主机上执行命令（包含重试逻辑）

        Args:
            host_name: 主机名称
            command: 要执行的命令
            retry_count: 重试次数
            retry_delay: 重试间隔

        Returns:
            BatchHostResult: 单台主机的执行结果
        """
        # 解析主机配置（包括凭据解密）
        try:
            host: Host = self._host_service.resolve_host(host_name)
        except KeyError as e:
            return BatchHostResult(
                host=host_name,
                success=False,
                command=command,
                error=f"host not found: {e}",
            )
        except (RuntimeError, OSError) as e:
            return BatchHostResult(
                host=host_name,
                success=False,
                command=command,
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

                if pool is not None:
                    # 连接池模式：复用主机连接，避免每次操作握手
                    with pool.acquire_context() as client:
                        cmd_result = client.execute(command, timeout=self._command_timeout)
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

                client = SSHClient(config)
                client.connect()
                cmd_result = client.execute(command, timeout=self._command_timeout)
                client.disconnect()

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
                duration = time.time() - start
                last_error = str(e)
                logger.debug(f"attempt {attempt + 1}/{retry_count + 1} failed for {host_name}: {e}")

                # 如果不是最后一次尝试，等待后重试
                if attempt < retry_count:
                    time.sleep(retry_delay)

        # 所有重试都失败
        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=last_error,
            duration=0.0,
        )


__all__ = ["BatchExecutor", "BatchResult", "BatchHostResult"]
