"""
连接池策略纯函数（同步 / 异步连接池共用）

从 SyncConnectionPool 与 AsyncConnectionPool 中提取的纯时间判断逻辑：
不涉及任何 I/O 与并发原语，便于两个池共享并单独测试。

注意：探活（liveness probe，如 ``conn.execute("true")``）是真实 I/O，
不属于本模块职责，保留在各池的 ``_check_connection`` 内。

设计约定：
- 所有函数均可注入 ``now``（默认 ``time.time()``），便于确定性测试。
- ``ConnectionMeta`` 是连接池元数据副表条目，替代 ``dict[int, dict[str, Any]]``
  的弱类型写法。

用法:
    >>> from remote_cmd.service._pool_policy import (
    ...     ConnectionMeta, lifetime_expired, idle_expired, should_close,
    ... )
    >>> meta = ConnectionMeta(created_at=1000.0, last_used=1000.0, conn_id="x")
    >>> lifetime_expired(meta.created_at, max_lifetime=3600, now=2000.0)
    False
    >>> should_close(meta, max_lifetime=60, idle_timeout=300, connected=True, now=1100.0)
    True
"""

from dataclasses import dataclass
from time import time as _now
from typing import Optional


@dataclass
class ConnectionMeta:
    """连接元数据（池的副表条目）。

    Attributes:
        created_at: 连接创建时间戳（unix 秒）
        last_used: 最近一次使用时间戳（unix 秒）
        conn_id: 连接唯一标识（用于日志定位）
    """

    created_at: float
    last_used: float
    conn_id: str


def lifetime_expired(
    created_at: float,
    max_lifetime: int,
    now: Optional[float] = None,
) -> bool:
    """判断连接是否超过最大生命周期。

    Args:
        created_at: 连接创建时间戳
        max_lifetime: 最大生命周期（秒）
        now: 当前时间戳（默认 ``time.time()``）

    Returns:
        bool: 超过生命周期为 True，否则 False

    Note:
        使用 ``>`` 判定（与 ``release`` / ``_check_connection`` 的历史行为一致）。
    """
    return (now if now is not None else _now()) - created_at > max_lifetime


def idle_expired(
    last_used: float,
    idle_timeout: int,
    now: Optional[float] = None,
) -> bool:
    """判断连接是否空闲超时。

    Note:
        使用 ``>=`` 判定（``now - last_used >= idle_timeout`` 视为超时），
        保证调用方 ``not idle_expired(...)`` 等价于原 ``_check_connection`` 的
        ``idle < idle_timeout`` 信任条件，边界处行为一致。

    Args:
        last_used: 最近使用时间戳
        idle_timeout: 空闲超时（秒）
        now: 当前时间戳（默认 ``time.time()``）

    Returns:
        bool: 空闲超时为 True，否则 False
    """
    return (now if now is not None else _now()) - last_used >= idle_timeout


def should_close(
    meta: Optional[ConnectionMeta],
    max_lifetime: int,
    idle_timeout: int,
    connected: bool,
    now: Optional[float] = None,
) -> bool:
    """判断连接是否应被关闭（用于清理 / 归还判定）。

    任一条件满足即应关闭：
    - 元数据缺失（``meta is None``）
    - 连接已断开
    - 超过最大生命周期
    - 空闲超时

    Note:
        生命周期用 ``>``（与 ``release`` / ``_check_connection`` 的历史判定一致）；
        空闲用 ``>=``（与 ``_cleanup_expired`` 的 keep 条件 ``idle < idle_timeout``
        的德摩根恒等一致）。极端边界 ``age == max_lifetime`` 时与原 ``_cleanup_expired``
        的 ``>=`` 存在低风险差异（再存活一个 tick），无实际危害。

    Args:
        meta: 连接元数据（可为 None）
        max_lifetime: 最大生命周期（秒）
        idle_timeout: 空闲超时（秒）
        connected: 连接是否仍处于活动状态
        now: 当前时间戳（默认 ``time.time()``）

    Returns:
        bool: 应关闭为 True，否则 False
    """
    if meta is None or not connected:
        return True
    current = now if now is not None else _now()
    if current - meta.created_at > max_lifetime:
        return True
    return current - meta.last_used >= idle_timeout
