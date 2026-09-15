"""
Profile 业务服务（v2.8）

协调 :class:`ProfileStore` 完成 profile 的 CRUD，并可选地在删除时检查
主机引用（避免产生"指向已删除 profile"的坏引用）：

    >>> from remote_cmd.service.profile_service import ProfileService
    >>> service = ProfileService(store=repo, host_repository=repo)
    >>> service.add_profile(HostProfile(name="aws", username="ec2-user"))

设计约定：
- 存储能力来自 ProfileStore 协议（JSON/SQLite 仓库均实现）；
  仓库自身若有 ``flush()``（JSON 的内存+落盘模型），服务在写操作后调用。
- 删除保护：提供 ``host_repository`` 时，仍被主机引用的 profile 默认
  拒绝删除（``ValueError``），可 ``force=True`` 强制删除（主机之后会在
  解析时报 unknown profile）。
- 本服务不接触任何凭据：HostProfile 没有密码字段。
"""

import logging
from typing import Any, Optional

from remote_cmd.core.profile import HostProfile
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.repository.profile_store import ProfileStore

logger = logging.getLogger(__name__)


class ProfileService:
    """Profile 管理服务。

    Args:
        store: Profile 存储（实现 ProfileStore 协议的仓库）
        host_repository: 可选的主机仓库，用于删除时的引用检查
    """

    def __init__(
        self,
        store: ProfileStore,
        host_repository: Optional[HostRepository] = None,
    ) -> None:
        self._store = store
        self._hosts = host_repository

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def add_profile(self, profile: HostProfile) -> HostProfile:
        """新增 profile。

        Raises:
            ValueError: 同名 profile 已存在
        """
        if self._store.contains_profile(profile.name):
            raise ValueError(f"profile '{profile.name}' already exists")
        self._store.save_profile(profile)
        self._flush()
        logger.info(f"profile added: {profile.name}")
        return profile

    def update_profile(self, profile_name: str, **kwargs: Any) -> HostProfile:
        """更新 profile 字段（不存在时 KeyError）。

        仅更新 HostProfile 已有的字段；``name`` 本身不可改名
        （通过 ``name=...`` 传入会抛 ValueError）。
        """
        profile = self._store.get_profile(profile_name)
        data = profile.to_dict()
        for key, value in kwargs.items():
            if key == "name":
                raise ValueError("profile name cannot be changed; remove and re-add instead")
            if key in data:
                data[key] = value
        # 先构造校验副本（非法值抛 ValidationError，不污染存储中的对象）
        validated = HostProfile.from_dict(data)
        self._store.save_profile(validated)
        self._flush()
        logger.info(f"profile updated: {profile_name}")
        return validated

    def remove_profile(self, name: str, force: bool = False) -> None:
        """删除 profile。

        Args:
            name: profile 名称
            force: 为 False（默认）且提供了 host_repository 时，
                若仍有主机引用该 profile 则抛出 ValueError

        Raises:
            KeyError: profile 不存在
            ValueError: 仍被主机引用且未 force
        """
        self._store.get_profile(name)  # 不存在 -> KeyError
        if not force and self._hosts is not None:
            referencing = [h.name for h in self._hosts.list() if h.profile == name]
            if referencing:
                names = ", ".join(sorted(referencing))
                raise ValueError(
                    f"profile '{name}' is still referenced by hosts: {names}; "
                    "pass force=True (or remove the references first)"
                )
        self._store.delete_profile(name)
        self._flush()
        logger.info(f"profile removed: {name}")

    def get_profile(self, name: str) -> HostProfile:
        return self._store.get_profile(name)

    def list_profiles(self) -> list[HostProfile]:
        return self._store.list_profiles()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _flush(self) -> None:
        """仓库支持 flush（JSON）时落盘；SQLite 写入即时生效则跳过。"""
        flush = getattr(self._store, "flush", None)
        if callable(flush):
            flush()


__all__ = ["ProfileService"]
