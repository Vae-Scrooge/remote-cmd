"""
批量执行数据契约（同步 / 异步 executor 共用）

集中定义批量执行相关的数据类与回调签名，供 ``service.batch_executor``、
``service.async_batch_executor`` 与 ``service._host_runner`` 共享，
避免类型定义散落与循环依赖。

``batch_executor`` 仍 re-export 这些符号以保持公共 API 兼容：
    >>> from remote_cmd.service.batch_executor import BatchResult  # noqa: F401
"""

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Callable, Optional

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


__all__ = ["BatchHostResult", "BatchResult", "ProgressCallback"]
