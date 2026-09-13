"""
Profile 存储能力协议（v2.8）

``HostRepository`` 的 ABC 保持稳定（不新增抽象方法，避免破坏第三方实现）；
支持 Profile 的仓库额外实现本协议。能力检测使用
``isinstance(repo, ProfileStore)``（runtime_checkable）。

存储语义与主机一致：
- JSON：内存保存，``flush()`` 原子落盘
- SQLite：``save_profile`` 即时生效

用法:
    >>> from remote_cmd.repository.profile_store import ProfileStore
    >>> isinstance(repo, ProfileStore)  # JsonHostRepository / SqliteHostRepository 为 True
    True
"""

from typing import Protocol, runtime_checkable

from remote_cmd.core.profile import HostProfile


@runtime_checkable
class ProfileStore(Protocol):
    """可选的 Profile 持久化能力协议。"""

    def save_profile(self, profile: HostProfile) -> None:
        """保存（新增或覆盖）Profile。"""
        ...

    def get_profile(self, name: str) -> HostProfile:
        """按名称获取 Profile，不存在时抛出 ``KeyError``。"""
        ...

    def delete_profile(self, name: str) -> None:
        """按名称删除 Profile，不存在时抛出 ``KeyError``。"""
        ...

    def list_profiles(self) -> list[HostProfile]:
        """列出全部 Profile（按名称排序）。"""
        ...

    def contains_profile(self, name: str) -> bool:
        """检查 Profile 是否存在。"""
        ...


__all__ = ["ProfileStore"]
