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
    >>> print(f"成功: {result.success}/{result.total}")
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient

logger = logging.getLogger(__name__)


@dataclass
class BatchHostResult:
    """
    单个主机的批量执行结果

    Attributes:
        host: 主机名称
        success: 命令是否成功执行
        command: 执行的命令
        stdout: 标准输出
        stderr: 标准错误
        exit_code: 退出码
        duration: 执行耗时（秒）
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
        success: 成功主机数
        failed: 失败主机数
        duration: 总耗时（秒）
        results: 按主机名索引的详细结果
    """

    total: int
    success: int
    failed: int
    duration: float
    results: dict[str, BatchHostResult] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """成功率（0.0 ~ 1.0）"""
        if self.total == 0:
            return 1.0
        return self.success / self.total

    @property
    def failed_hosts(self) -> list[str]:
        """失败主机列表"""
        return [h for h, r in self.results.items() if not r.success]

    @property
    def success_hosts(self) -> list[str]:
        """成功主机列表"""
        return [h for h, r in self.results.items() if r.success]

    def summary(self) -> str:
        """生成可读的汇总字符串"""
        return (
            f"总执行: {self.total}, "
            f"成功: {self.success}, "
            f"失败: {self.failed}, "
            f"耗时: {self.duration:.1f}s, "
            f"成功率: {self.success_rate:.1%}"
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
        host_service: Any,
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
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BatchResult:
        """
        在指定主机上批量执行命令

        Args:
            host_names: 要执行命令的主机名称列表
            command: 要执行的命令
            retry_count: 失败重试次数，默认 0（不重试）
            retry_delay: 重试间隔（秒），默认 1.0
            progress_callback: 进度回调，参数 (completed, total, current_host_name)

        Returns:
            BatchResult: 批量执行结果

        Raises:
            ValueError: host_names 为空
        """
        if not host_names:
            raise ValueError("主机列表不能为空")

        # 异步内核委托路径：同步接口 + asyncio.run(异步实现)
        if self._async_executor is not None:
            import asyncio

            # 保证空列表校验语义一致（已在上方判断）
            return asyncio.run(
                self._async_executor.execute(
                    host_names=host_names,
                    command=command,
                    retry_count=retry_count,
                    retry_delay=retry_delay,
                    progress_callback=progress_callback,
                )
            )

        total = len(host_names)
        results: dict[str, BatchHostResult] = {}
        completed = 0
        start_time = time.time()

        logger.info(
            f"批量执行开始: {total} 台主机, 并发数={self._max_concurrency}, 命令='{command}'"
        )

        with ThreadPoolExecutor(max_workers=self._max_concurrency) as executor:
            future_map = {
                executor.submit(
                    self._execute_on_host,
                    host_name,
                    command,
                    retry_count,
                    retry_delay,
                ): host_name
                for host_name in host_names
            }

            try:
                for future in as_completed(future_map):
                    host_name = future_map[future]
                    try:
                        result = future.result()
                    except Exception as e:  # noqa: BLE001
                        result = BatchHostResult(
                            host=host_name,
                            success=False,
                            command=command,
                            error=f"计划异常: {e}",
                        )
                    results[host_name] = result
                    completed += 1

                    if progress_callback:
                        progress_callback(completed, total, host_name)

                    logger.debug(
                        f"[{completed}/{total}] {host_name}: "
                        f"{'✓' if result.success else '✗'} "
                        f"({result.duration:.1f}s)"
                    )

            except KeyboardInterrupt:
                logger.warning("用户中断批量执行")
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
                            error="用户中断",
                        )
                        completed += 1

        duration = time.time() - start_time
        success_count = sum(1 for r in results.values() if r.success)
        failed_count = total - success_count

        logger.info(f"批量执行完成: {success_count}/{total} 成功, 耗时 {duration:.1f}s")

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
            host: Host = self._host_service._resolve_host(host_name)
        except KeyError as e:
            return BatchHostResult(
                host=host_name,
                success=False,
                command=command,
                error=f"主机不存在: {e}",
            )
        except (RuntimeError, OSError) as e:
            return BatchHostResult(
                host=host_name,
                success=False,
                command=command,
                error=f"主机解析失败: {e}",
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
                logger.debug(f"{host_name} 第 {attempt + 1}/{retry_count + 1} 次尝试失败: {e}")

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
