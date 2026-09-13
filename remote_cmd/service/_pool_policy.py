"""
向后兼容 shim：连接池策略已迁移到 ``remote_cmd.core.pool_policy``。

v2.6（P2.1）起该模块只被 core 连接池消费，canonical 位置为
``remote_cmd.core.pool_policy``，以保持 ``core`` 对 ``service`` 的零依赖。
本模块仅为保留既有导入路径（含测试）而存在。
"""

from remote_cmd.core.pool_policy import (  # noqa: F401
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)

__all__ = ["ConnectionMeta", "idle_expired", "lifetime_expired", "should_close"]
