"""
批量命令执行器模块

支持多主机并发执行命令，带超时控制、失败重试和进度回调。
与现有的 HostService 和 SSHClient 集成。

连接池所有权约定（与 AsyncBatchExecutor 一致）：
- 外部注入（``pool_factory``）→ 调用方拥有，executor 只借用不关闭；
  适合长驻服务跨批次复用连接。
- 内部创建 → executor 拥有，单次 ``execute`` 结束后 ``close_all``；
  适合一次性脚本。

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

import logging
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.core.sync_connection_pool import SyncConnectionPool
from remote_cmd.service._host_runner import (
    build_connection_config,
    resolve_host_or_error,
    to_host_result,
)
from remote_cmd.service._types import (
    BatchHostResult,
    BatchResult,
    ProgressCallback,
)
from remote_cmd.service.host_service import HostService
from remote_cmd.service.retry_policy import compute_backoff_delay, is_retryable

logger = logging.getLogger(__name__)

# 公共 API 兼容 re-export：类型定义已迁移至 service._types
# （外部仍可 from remote_cmd.service.batch_executor import BatchResult）
__all__ = ["BatchExecutor", "BatchResult", "BatchHostResult"]

# 外部连接池工厂签名：接收连接配置，返回已配置的池。
# 调用方保留所有权——executor 绝不 close 外部池。
# use_async=False 时须返回 SyncConnectionPool；use_async=True 时
# 须返回 AsyncConnectionPool（由 AsyncBatchExecutor 消费）。
PoolFactory = Callable[[ConnectionConfig], Any]


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
            降低线程/CPU 开销。注意：启用时调用线程不应已运行 asyncio 事件循环，
            否则抛出 RuntimeError（应改用 AsyncBatchExecutor.execute()）。
        pool_factory: 外部连接池工厂（可选，v2.1）。提供时执行器从工厂获取
            池并复用其连接，**绝不关闭**返回的池（所有权归调用方）；
            未提供时多主机或需重试时内部按主机创建 SyncConnectionPool
            （use_async=True 时为 AsyncConnectionPool），执行结束后自动关闭。
            工厂返回的池类型须与内核匹配（见 PoolFactory 注释）。

    连接池所有权约定（与 AsyncBatchExecutor 一致）：

    - 外部注入（``pool_factory``）→ 调用方拥有，executor 只借用不关闭；
      适合长驻服务跨批次复用连接。
    - 内部创建 → executor 拥有，单次 ``execute`` 结束后 ``close_all``；
      适合一次性脚本。

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
        pool_factory: Optional[PoolFactory] = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got: {max_concurrency}")
        if command_timeout <= 0:
            raise ValueError(f"command_timeout must be > 0, got: {command_timeout}")
        self._host_service = host_service
        self._max_concurrency = max_concurrency
        self._command_timeout = command_timeout
        self._use_async = use_async
        self._pool_factory = pool_factory
        # 延迟导入以避免在未安装 asyncssh 的环境下的导入失败
        # 使用前向引用避免在模块加载期引入 asyncssh 硬依赖（开启 use_async 时才惰性导入）
        self._async_executor: Optional["AsyncBatchExecutor"] = None  # noqa: UP037
        if use_async:
            from remote_cmd.service.async_batch_executor import AsyncBatchExecutor

            self._async_executor = AsyncBatchExecutor(
                host_service=host_service,
                max_concurrency=max_concurrency,
                command_timeout=command_timeout,
                pool_factory=pool_factory,
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
            retry_count: 失败重试次数，默认 0（不重试）。仅对瞬态错误
                （超时/网络中断等）重试；认证、凭据、配置等永久性错误
                立即失败（分类见 service/retry_policy.py）
            retry_delay: 重试基础延迟（秒），默认 1.0。实际等待为
                指数退避 + full jitter：第 n 次失败后等待
                0 到 retry_delay * 2^n（含端点）内的随机值（上限 60s）
            progress_callback: 进度回调，参数 (completed, total, current_host_name)。
                同步内核下回调应为同步函数；异步回调请使用 use_async=True。

        Returns:
            BatchResult: 批量执行结果

        Raises:
            ValueError: host_names 为空
        """
        if not host_names:
            raise ValueError("host_names must not be empty")
        if retry_count < 0:
            raise ValueError(f"retry_count must be >= 0, got: {retry_count}")
        if retry_delay < 0:
            raise ValueError(f"retry_delay must be >= 0, got: {retry_delay}")

        # 去重（保留首次出现顺序）：重复主机名只执行一次，避免 results 覆盖导致
        # total/success/failed 统计错位
        host_names = list(dict.fromkeys(host_names))

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
        # 仅当 _async_executor 已初始化时才进入此路径（见 execute() 的窄化判断）
        assert self._async_executor is not None

        import asyncio

        # 前置检测运行中的事件循环：asyncio.run() 在事件循环内调用只会抛出
        # 通用 Python 错误，替换为可操作的项目级错误提示。
        # 仅此场景抛出，其余 RuntimeError（来自 asyncio.run 本身的其他
        # 失败原因）不经本分支改写。
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # 无运行中的事件循环，asyncio.run 可安全使用
        else:
            raise RuntimeError(
                "BatchExecutor(use_async=True) cannot be used inside a running "
                "event loop; use AsyncBatchExecutor.execute() directly instead"
            )

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

        logger.info(f"batch execution started: {total} hosts, concurrency={self._max_concurrency}")

        pools: dict[str, SyncConnectionPool] = {}
        # 内部创建的池由本批次负责关闭；外部 pool_factory 提供的池
        # 所有权归调用方，绝不登记进此列表
        internal_pools: list[SyncConnectionPool] = []

        try:
            with ThreadPoolExecutor(max_workers=self._max_concurrency) as executor:
                future_map = self._submit_tasks(
                    executor,
                    host_names,
                    command,
                    retry_count,
                    retry_delay,
                    pools,
                    internal_pools,
                    total,
                )
                self._collect_results(
                    future_map, host_names, command, progress_callback, results, total
                )
        finally:
            # 仅关闭内部创建的池；外部 pool_factory 提供的池所有权归调用方
            self._cleanup_pools(internal_pools)

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
        internal_pools: list[SyncConnectionPool],
        total: int,
    ) -> dict:
        """提交任务到线程池，返回 future_map"""
        future_map = {}
        for host_name in host_names:
            # 连接池：外部注入时始终启用；否则多主机或需重试时创建
            pool: Optional[SyncConnectionPool] = None
            if self._pool_factory is not None or retry_count > 0 or total > 1:
                pool = self._prepare_pool(host_name, pools, internal_pools)
            future = executor.submit(
                self._execute_on_host, host_name, command, retry_count, retry_delay, pool
            )
            future_map[future] = host_name
        return future_map

    def _prepare_pool(
        self,
        host_name: str,
        pools: dict[str, SyncConnectionPool],
        internal_pools: list[SyncConnectionPool],
    ) -> Optional[SyncConnectionPool]:
        """为指定主机创建或获取连接池

        主机解析失败时返回 None 而非上抛：保持 execute 的
        "未知主机 → BatchHostResult 错误条目" 契约（由
        _execute_on_host 的 resolve_host_or_error 记录失败详情），
        避免整个批次因单个坏主机以异常收场。

        池所有权：外部 ``pool_factory`` 提供的池绝不登记进
        internal_pools（executor 不负责关闭）；内部创建的池登记后
        由 _cleanup_pools 统一 close_all。
        """
        if host_name in pools:
            return pools[host_name]

        try:
            host = self._host_service.resolve_host(host_name)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"pool preparation skipped for {host_name}: {e}")
            return None
        config = build_connection_config(host, self._command_timeout)
        if self._pool_factory is not None:
            pool = self._pool_factory(config)
            pools[host_name] = pool
            return pool
        pool = SyncConnectionPool(
            config,
            max_connections=max(1, self._max_concurrency),
            client_factory=SSHClient,
        )
        pools[host_name] = pool
        internal_pools.append(pool)
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

    def _process_future_result(self, future, host_name: str, command: str) -> BatchHostResult:
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
            if isinstance(rv, Coroutine):
                logger.warning("同步内核不支持异步进度回调，请使用 use_async=True")
                # 显式关闭未 await 的协程，避免 RuntimeWarning 与资源泄漏
                rv.close()

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

    def _cleanup_pools(self, internal_pools: list[SyncConnectionPool]) -> None:
        """关闭本批次内部创建的所有连接池（外部提供的池绝不关闭）"""
        for pool in internal_pools:
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
        # 解析主机配置（包括凭据解密）；失败返回错误结果
        outcome = resolve_host_or_error(self._host_service, host_name, command)
        if isinstance(outcome, BatchHostResult):
            return outcome
        host: Host = outcome

        last_error: Optional[str] = None
        last_duration = 0.0

        for attempt in range(retry_count + 1):
            start = time.time()
            try:
                config = build_connection_config(host, self._command_timeout)

                if pool is not None:
                    # 连接池模式：复用主机连接，避免每次操作握手
                    with pool.acquire_context() as client:
                        cmd_result = client.execute(command, timeout=self._command_timeout)
                    return to_host_result(host_name, command, cmd_result, time.time() - start)

                # 非连接池路径：try/finally 确保即使 execute() 抛异常，
                # disconnect() 也会执行，避免 SSH 连接泄漏
                client = SSHClient(config)
                try:
                    client.connect()
                    cmd_result = client.execute(command, timeout=self._command_timeout)
                finally:
                    client.disconnect()

                return to_host_result(host_name, command, cmd_result, time.time() - start)

            except Exception as e:  # noqa: BLE001
                duration = time.time() - start
                last_error = str(e)
                last_duration = duration
                logger.debug(f"attempt {attempt + 1}/{retry_count + 1} failed for {host_name}: {e}")

                # 已是最后一次尝试，或异常为永久性（认证/凭据/配置错误等），
                # 立即放弃重试——详见 service/retry_policy.py 的分类契约
                if attempt >= retry_count:
                    break
                if not is_retryable(e):
                    logger.debug(f"non-retryable error for {host_name}, giving up: {e}")
                    break

                # 指数退避 + full jitter（避免多主机同步重试的惊群）
                delay = compute_backoff_delay(attempt, retry_delay)
                time.sleep(delay)

        # 所有重试都失败
        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=last_error,
            duration=last_duration,
        )
