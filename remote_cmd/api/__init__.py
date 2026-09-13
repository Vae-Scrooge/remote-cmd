"""
API facade 包（向后兼容层）

存放从 ``core`` 迁出的公共兼容 facade，使核心层（``core``）保持对
``service`` 的零依赖。当前包含:

    - :class:`remote_cmd.api.host_manager.HostManager`：v1.x 的 HostManager
      API，内部委托给 HostService + JsonHostRepository。

旧导入路径 ``remote_cmd.core.host_manager`` 仍然可用（re-export shim），
但新代码应使用 ``remote_cmd.api.host_manager`` 或直接使用 HostService。

依赖方向（v2.6 契约）::

    cli / api → service → core → utils
    repository ← service / cli
    core 绝不 import service / cli / api 的实现
"""

from remote_cmd.api.host_manager import HostManager

__all__ = ["HostManager"]
