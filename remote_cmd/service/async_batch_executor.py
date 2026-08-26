"""异步批量命令执行器（基于 asyncio + asyncssh 原生异步实现）。

与 `service.batch_executor.BatchExecutor`（基于 ThreadPoolExecutor + 同步
Paramiko）相比，本执行器使用 `asyncio.Semaphore` 控制并发，并在事件循环上
真正并发地执行 SSH 命令，避免线程池上下文切换开销。

连接池集成（v2.1）：
- 多主机或需重试时，内部按主机创建 ``AsyncConnectionPool`` 复用连接
  （与同步 BatchExecutor 的 SyncConnectionPool 行为对齐），批结束后自动关闭。
- 构造参数 ``pool_factory`` 支持注入外部池：调用方持有所有权，
  executor 绝不关闭外部池，适合长驻服务跨批次复用连接。

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
from typing import Callable, Optional

from remote_cmd.core.async_connection_pool import AsyncConnectionPool
from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.service._host_runner import (
    build_connection_config,
    resolve_host_or_error,
    to_host_result,
)
from remote_cmd.service._types import BatchHostResult, BatchResult, ProgressCallback
from remote_cmd.service.host_service import HostService
from remote_cmd.service.retry_policy import compute_backoff_delay, is_retryable

logger = logging.getLogger(__name__)

# 外部连接池工厂签名：接收连接配置，返回已配置的池。
# 调用方保留所有权——executor 绝不 close 外部池。
PoolFactory = Callable[[ConnectionConfig], AsyncConnectionPool]


class AsyncBatchExecutor:
    """异步批量命令执行器。

    Args:
        host_service: HostService 实例（提供主机配置与凭据解析）
        max_concurrency: 最大并发主机数，默认 10
        command_timeout: 单条命令超时（秒），默认 30
        pool_factory: 外部连接池工厂（可选）。提供时执行器从工厂获取
            池并复用其连接，**绝不关闭**返回的池（所有权归调用方）；
            未提供时与同步 BatchExecutor 行为对齐——多主机或需重试时
            内部按主机创建 AsyncConnectionPool，执行结束后自动关闭。

    连接池所有权约定（与同步 BatchExecutor 一致）：

    - 外部注入（``pool_factory``）→ 调用方拥有，executor 只借用不关闭；
      适合长驻服务跨批次复用连接。
    - 内部创建 → executor 拥有，单次 ``execute`` 结束后 ``close_all``；
      适合一次性脚本。
    """

    def __init__(
        self,
        host_service: HostService,
        max_concurrency: int = 10,
        command_timeout: int = 30,
        pool_factory: Optional[PoolFactory] = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got: {max_concurrency}")
        if command_timeout <= 0:
            raise ValueError(f"command_timeout must be > 0, got: {command_timeout}")
        self._host_service = host_service
        self._max_concurrency = max_concurrency
        self._command_timeout = command_timeout
        self._pool_factory = pool_factory

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
            retry_count: 失败重试次数（默认 0）。仅对瞬态错误
                （超时/网络中断等）重试；认证、凭据、配置等永久性错误
                立即失败（分类见 service/retry_policy.py）
            retry_delay: 重试基础延迟（秒），默认 1.0。实际等待为
                指数退避 + full jitter：第 n 次失败后等待
                0 到 retry_delay * 2^n（含端点）内的随机值（上限 60s）
            progress_callback: 进度回调，签名为 `(completed, total, host_name)`；
                回调可为同步或 async 函数（async 时会被 await）。

        Returns:
            BatchResult: 批量结果（与同步 BatchExecutor 完全一致）

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

        total = len(host_names)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        start = time.time()

        logger.info(
            "异步批量执行开始: %s 台主机, 并发=%s",
            total,
            self._max_concurrency,
        )

        # 连接池准备（与同步 BatchExecutor 对齐）：
        # - 外部 pool_factory 提供时始终启用（调用方持有所有权，绝不关闭）
        # - 否则多主机或需重试时创建内部池，批结束后统一 close_all
        pools: dict[str, AsyncConnectionPool] = {}
        internal_pools: list[AsyncConnectionPool] = []
        if self._pool_factory is not None or retry_count > 0 or total > 1:
            for name in host_names:
                self._prepare_pool(name, pools, internal_pools)

        completed_counter = 0
        completed_lock = asyncio.Lock()
        results: dict[str, BatchHostResult] = {}

        async def _per_host(name: str) -> None:
            nonlocal completed_counter
            async with semaphore:
                result = await self._execute_on_host(
                    name, command, retry_count, retry_delay, pool=pools.get(name)
                )
            async with completed_lock:
                results[name] = result
                completed = completed_counter + 1
                completed_counter = completed

                if progress_callback is not None:
                    # 包裹回调：用户提供的 progress_callback 抛异常时不应中断整个批次，
                    # 否则 gather 会向上抛出首异常、BatchResult 永不构建、
                    # 已完成结果丢失且其余 task 沦为孤儿。
                    try:
                        rv = progress_callback(completed, total, name)
                        if asyncio.iscoroutine(rv):
                            await rv
                    except Exception as e:  # noqa: BLE001
                        logger.warning("progress_callback for %s raised: %s", name, e)

                logger.debug(
                    "[%s/%s] %s: %s (%.1fs)",
                    completed,
                    total,
                    name,
                    "✓" if result.success else "✗",
                    result.duration,
                )

        tasks = [asyncio.create_task(_per_host(n)) for n in host_names]
        try:
            # return_exceptions=True 作为兜底：即使 _per_host 意外抛出异常，
            # 也不会中断其他任务或使 BatchResult 构建被跳过。
            # KeyboardInterrupt 属于 BaseException，仍会被下方 except 捕获。
            await asyncio.gather(*tasks, return_exceptions=True)
        except KeyboardInterrupt:
            logger.warning("batch execution interrupted by user")
            await self._cancel_and_mark_interrupted(tasks, host_names, results, command)
        finally:
            # 仅关闭内部创建的池；外部 pool_factory 提供的池所有权归调用方
            await self._cleanup_pools(internal_pools)

        duration = time.time() - start
        success_count = sum(1 for r in results.values() if r.success)
        failed_count = total - success_count
        logger.info(
            "async batch execution finished: %s/%s succeeded, took %.1fs",
            success_count,
            total,
            duration,
        )

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

    def _prepare_pool(
        self,
        host_name: str,
        pools: dict[str, AsyncConnectionPool],
        internal_pools: list[AsyncConnectionPool],
    ) -> None:
        """为指定主机准备连接池（与同步 BatchExecutor._prepare_pool 对称）。

        主机解析失败时跳过（不写入 pools），由 _execute_on_host 的
        resolve_host_or_error 记录失败条目，保持 execute 的
        "错误结果而非异常" 契约。

        池所有权：
        - 外部 ``pool_factory`` 提供的池：调用方持有，绝不登记进
          internal_pools（executor 不负责关闭）
        - 内部创建的池：登记进 internal_pools，批结束后统一 close_all
        """
        if host_name in pools:
            return
        try:
            host = self._host_service.resolve_host(host_name)
        except Exception as e:  # noqa: BLE001
            logger.debug("pool preparation skipped for %s: %s", host_name, e)
            return
        config = build_connection_config(host, self._command_timeout)
        if self._pool_factory is not None:
            pools[host_name] = self._pool_factory(config)
            return
        pool = AsyncConnectionPool(
            config,
            max_connections=max(1, self._max_concurrency),
            client_factory=AsyncSSHClient,
        )
        pools[host_name] = pool
        internal_pools.append(pool)

    async def _cleanup_pools(self, internal_pools: list[AsyncConnectionPool]) -> None:
        """关闭本批次内部创建的所有连接池（外部提供的池绝不关闭）。"""
        for pool in internal_pools:
            await pool.close_all()

    async def _execute_on_host(
        self,
        host_name: str,
        command: str,
        retry_count: int,
        retry_delay: float,
        pool: Optional[AsyncConnectionPool] = None,
    ) -> BatchHostResult:
        """在单台主机上异步执行命令（含重试逻辑）。

        Args:
            host_name: 主机名称
            command: 要执行的命令
            retry_count: 重试次数
            retry_delay: 重试基础延迟（秒，指数退避基准）
            pool: 可选连接池（外部注入或内部创建），提供时复用连接
        """
        # 主机解析（失败返回错误结果）
        outcome = resolve_host_or_error(self._host_service, host_name, command)
        if isinstance(outcome, BatchHostResult):
            return outcome
        host: Host = outcome

        last_error: Optional[str] = None
        last_duration = 0.0
        for attempt in range(retry_count + 1):
            start = time.time()
            try:
                if pool is not None:
                    # 连接池模式：复用主机连接，避免每次操作握手
                    # （外部池与内部池语义一致，仅生命周期归属不同）
                    async with pool.acquire_context() as client:
                        cmd_result = await client.execute(
                            command,
                            timeout=self._command_timeout,
                        )
                else:
                    config = build_connection_config(host, self._command_timeout)
                    async with AsyncSSHClient(config) as client:
                        cmd_result = await client.execute(
                            command,
                            timeout=self._command_timeout,
                        )
                return to_host_result(host_name, command, cmd_result, time.time() - start)
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                last_duration = time.time() - start
                logger.debug(
                    "%s 第 %s/%s 次尝试失败: %s",
                    host_name,
                    attempt + 1,
                    retry_count + 1,
                    e,
                )
                # 已是最后一次尝试，或异常为永久性（认证/凭据/配置错误等），
                # 立即放弃重试——详见 service/retry_policy.py 的分类契约
                if attempt >= retry_count:
                    break
                if not is_retryable(e):
                    logger.debug("non-retryable error for %s, giving up: %s", host_name, e)
                    break
                # 指数退避 + full jitter（避免多主机同步重试的惊群）
                delay = compute_backoff_delay(attempt, retry_delay)
                await asyncio.sleep(delay)

        return BatchHostResult(
            host=host_name,
            success=False,
            command=command,
            error=last_error,
            duration=last_duration,
        )


__all__ = ["AsyncBatchExecutor"]
