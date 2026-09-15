"""
向后兼容 shim：HostManager 实现已迁移到 ``remote_cmd.api.host_manager``。

v2.6 起 ``HostManager`` 的 canonical 位置是 ``remote_cmd.api.host_manager``，
使 ``core`` 层不再依赖 ``service``。本模块仅为保留既有导入路径而存在：

    >>> from remote_cmd.core.host_manager import HostManager  # 仍可用（shim）
    >>> from remote_cmd.api.host_manager import HostManager   # canonical

``Host`` 也原样 re-export，兼容历史用法
``from remote_cmd.core.host_manager import Host, HostManager``。
"""

from remote_cmd.api.host_manager import HostManager
from remote_cmd.core.host import Host

__all__ = ["Host", "HostManager"]
