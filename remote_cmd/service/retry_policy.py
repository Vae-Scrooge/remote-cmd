"""
重试策略纯函数（同步 / 异步执行器共用）

定义批量执行重试的两个核心决策，均为无副作用纯函数，便于单独测试：

- ``is_retryable``: 异常分类——瞬态（可重试）vs 永久性（绝不重试）
- ``compute_backoff_delay``: 指数退避 + 抖动（full jitter）延迟计算

设计约定（与 ``service/_pool_policy.py`` 一致的纯函数风格）：
- 本模块不做任何 I/O、不 sleep、不记日志；重试循环留在各 executor 内。
- ``compute_backoff_delay`` 支持注入 ``rng``（``random.Random`` 实例），
  保证测试确定性。

分类契约：
    永久性（never retry）——重试必然再次失败或有害：
    - ``SSHAuthenticationError``      认证被拒（重试会加剧账号锁定）
    - ``CredentialError``             本地凭据解析/解密失败（含 CredentialEncryptionError）
    - ``ConfigError`` / ``ConfigurationError`` 配置错误
    - ``ValidationError``             输入校验错误
    - ``ValueError`` / ``TypeError``  无效参数（如非法端口）与编程错误
    - ``KeyError``                    主机不存在
    - ``RuntimeError``                池已关闭等内部状态错误
    - ``KeyboardInterrupt`` 等 ``BaseException``（非 ``Exception``）

    瞬态（retry with backoff）：
    - ``SSHTimeoutError`` / ``SSHCommandTimeoutError``  超时
    - 非认证类 ``SSHConnectionError`` / ``SSHCommandError``  网络中断、通道异常
    - ``OSError`` 及其子类（socket 错误等）
    - 其他未识别的 ``Exception``：**保持历史行为按可重试处理**（向后
      兼容——注入自定义 client_factory 的调用方可能抛出任意异常表示
      瞬态故障）。自定义 client_factory 实现应优先抛出 remote_cmd
      类型化异常（如 ``SSHTimeoutError`` / ``SSHAuthenticationError``），
      以获得精确的永久/瞬态分类。

用法:
    >>> from remote_cmd.service.retry_policy import is_retryable, compute_backoff_delay
    >>> from remote_cmd.utils.exceptions import SSHAuthenticationError
    >>> is_retryable(SSHAuthenticationError("denied"))
    False
    >>> compute_backoff_delay(attempt=0, base_delay=1.0, rng=random.Random(42))
    0.6394267984578837
"""

import random
from typing import Optional

from remote_cmd.utils.exceptions import (
    ConfigError,
    CredentialError,
    SSHAuthenticationError,
    ValidationError,
)

# 永久性异常集合：is_retryable 对这些类型恒返回 False
PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    # 凭据/认证：重试同一凭据只会再次失败，且可能触发账号锁定
    SSHAuthenticationError,
    CredentialError,  # 含 CredentialEncryptionError 子类
    # 配置/校验：调用方输入错误，重试无意义
    ConfigError,  # 含别名 ConfigurationError
    ValidationError,
    # 编程/参数错误：非法端口、非法参数、主机不存在、池已关闭等
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
)

# 默认退避上限（秒）：attempt 很大时避免 2**attempt 溢出为天文数字
DEFAULT_MAX_BACKOFF = 60.0


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否为瞬态（可重试）。

    Args:
        exc: 待分类的异常

    Returns:
        bool: 永久性错误返回 False（绝不重试），瞬态返回 True

    Note:
        未识别的 ``Exception`` 子类默认返回 True（保持 v2.0 及之前
        "except Exception 即重试" 的历史行为，避免破坏注入自定义
        client_factory 的调用方）。自定义 client_factory 实现应优先
        抛出 remote_cmd 类型化异常以获得精确分类；如需收紧本策略
        请改为白名单模式。
        ``BaseException``（KeyboardInterrupt / SystemExit /
        asyncio.CancelledError）恒为 False。
    """
    if isinstance(exc, PERMANENT_ERRORS):
        return False
    # KeyboardInterrupt / SystemExit / asyncio.CancelledError 等
    # BaseException 绝不重试；其余（含未识别的 Exception）保持可重试
    return isinstance(exc, Exception)


def compute_backoff_delay(
    attempt: int,
    base_delay: float,
    max_delay: float = DEFAULT_MAX_BACKOFF,
    jitter: bool = True,
    rng: Optional[random.Random] = None,
) -> float:
    """计算指数退避 + 抖动（full jitter）的重试等待时间。

    公式（AWS "Exponential Backoff and Jitter" 的 full jitter 变体）::

        capped = min(max_delay, base_delay * 2**attempt)
        delay  = uniform(0, capped)          # jitter=True
        delay  = capped                      # jitter=False

    full jitter 将等待打散到 [0, capped] 均匀分布，批量重试场景下
    可有效避免多主机同时重试造成的惊群（thundering herd）。

    Args:
        attempt: 已失败的尝试序号，从 0 开始（第 1 次失败后的等待
            取 attempt=0）
        base_delay: 基础延迟（秒），即执行器 ``retry_delay`` 参数；
            同时是单次等待的上限起点
        max_delay: 等待上限（秒），默认 60.0
        jitter: 是否启用 full jitter，默认 True
        rng: 随机源（可注入 ``random.Random(seed)`` 保证测试确定性），
            默认使用模块级 ``random``

    Returns:
        float: 下一次重试前的等待秒数

    Raises:
        ValueError: attempt 为负或 base_delay / max_delay 为负
    """
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got: {attempt}")
    if base_delay < 0:
        raise ValueError(f"base_delay must be >= 0, got: {base_delay}")
    if max_delay < 0:
        raise ValueError(f"max_delay must be >= 0, got: {max_delay}")

    capped = min(max_delay, base_delay * (2**attempt))
    if not jitter:
        return capped
    source = rng if rng is not None else random
    return source.uniform(0.0, capped)


__all__ = [
    "PERMANENT_ERRORS",
    "is_retryable",
    "compute_backoff_delay",
    "DEFAULT_MAX_BACKOFF",
]
