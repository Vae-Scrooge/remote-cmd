"""
JSON 文件主机仓库实现

使用 JSON 文件存储主机配置，支持：
- 原子写入（先写临时文件再重命名，防止崩溃导致数据丢失）
- 可选加密（通过 CredentialEncryption 加密 password 字段）
- 明文持久化策略（v2.5）
- config version management
- 自动从旧版本迁移

并发语义（single-writer）：
- 本实现面向**单进程/单写入者**"; 多进程并发写同一 JSON 文件
  可能丢失更新（读-改-写无跨进程锁）。需要多进程/多写入者时，
  请使用 SqliteHostRepository（WAL + busy_timeout 处理并发写）。
"""

import builtins
import contextlib
import json
import logging
import os
import tempfile
import warnings
from pathlib import Path
from typing import Optional

from remote_cmd.core.host import Host
from remote_cmd.core.profile import HostProfile
from remote_cmd.core.recipe import Recipe
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.utils.credential_guard import PasswordGuard, is_plaintext_password
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.exceptions import (
    CredentialError,
    PlaintextCredentialWarning,
    ValidationError,
)

logger = logging.getLogger(__name__)

# current config version
CONFIG_VERSION = 2


class JsonHostRepository(HostRepository):
    """
    JSON 文件主机仓库

    Args:
        filepath: JSON 文件路径
        encryption: 可选的凭据加密器（设置后自动加密 password）
        auto_load: 初始化时是否自动加载已有文件（默认 True）
        allow_plaintext_credentials: 明文密码持久化策略（v2.5；默认 None
            为兼容模式）。三态：
            - None：允许，但在 flush 会持久化明文密码时发出
              PlaintextCredentialWarning（兼容 v2.4 及更早）
            - True：显式允许明文落盘（不再告警）
            - False：flush 遇到明文密码时抛出 CredentialError
            v3.0 起默认值计划切换为 False。

    并发语义：单进程/单写入者（见模块 docstring）；多进程场景请使用
    SqliteHostRepository。

    Profile 支持（v2.8）：实现 :class:`ProfileStore` 能力协议，
    同一文件内以 ``profiles`` 段持久化（无凭据字段）。
    """

    def __init__(
        self,
        filepath: str,
        encryption: Optional[CredentialEncryption] = None,
        auto_load: bool = True,
        allow_plaintext_credentials: Optional[bool] = None,
    ) -> None:
        self._filepath = Path(filepath)
        self._encryption = encryption
        self._guard = PasswordGuard(encryption)
        self._allow_plaintext = allow_plaintext_credentials
        self._hosts: dict[str, Host] = {}
        self._profiles: dict[str, HostProfile] = {}
        self._recipes: dict[str, Recipe] = {}

        if auto_load and self._filepath.exists():
            self._load()

    # ========================================================================
    # Repository 接口实现
    # ========================================================================

    def save(self, host: Host) -> None:
        """
        保存主机到内存，随后需要调用 flush() 写入文件

        注意: 本方法不会加密密码。password 的加密发生在 flush() 序列化阶段
        （仅当构造时传入了 encryption）。请勿绕过 HostService 直接以明文
        密码调用 save() 后再 flush() 落盘——确保传入了 encryption。
        """
        self._hosts[host.name] = host

    def get(self, name: str) -> Host:
        if name not in self._hosts:
            raise KeyError(f"Host '{name}' not found")
        return self._hosts[name]

    def delete(self, name: str) -> None:
        if name not in self._hosts:
            raise KeyError(f"Host '{name}' not found")
        del self._hosts[name]

    def list(self, tag: Optional[str] = None) -> list[Host]:
        hosts = list(self._hosts.values())
        if tag:
            hosts = [h for h in hosts if h.tags and tag in h.tags]
        return hosts

    def list_tags(self) -> builtins.list[str]:
        tags: set = set()
        for host in self._hosts.values():
            if host.tags:
                tags.update(host.tags)
        return sorted(tags)

    def contains(self, name: str) -> bool:
        return name in self._hosts

    def count(self) -> int:
        return len(self._hosts)

    # ========================================================================
    # ProfileStore 能力（v2.8）
    # ========================================================================

    def save_profile(self, profile: HostProfile) -> None:
        """保存 Profile 到内存，随后需要调用 flush() 写入文件。"""
        self._profiles[profile.name] = profile

    def get_profile(self, name: str) -> HostProfile:
        if name not in self._profiles:
            raise KeyError(f"Profile '{name}' not found")
        return self._profiles[name]

    def delete_profile(self, name: str) -> None:
        if name not in self._profiles:
            raise KeyError(f"Profile '{name}' not found")
        del self._profiles[name]

    def list_profiles(self) -> builtins.list[HostProfile]:
        return [self._profiles[name] for name in sorted(self._profiles)]

    def contains_profile(self, name: str) -> bool:
        return name in self._profiles

    # ========================================================================
    # RecipeStore 能力（v2.9）
    # ========================================================================

    def save_recipe(self, recipe: Recipe) -> None:
        """保存 Recipe 到内存，随后需要调用 flush() 写入文件。"""
        self._recipes[recipe.name] = recipe

    def get_recipe(self, name: str) -> Recipe:
        if name not in self._recipes:
            raise KeyError(f"Recipe '{name}' not found")
        return self._recipes[name]

    def delete_recipe(self, name: str) -> None:
        if name not in self._recipes:
            raise KeyError(f"Recipe '{name}' not found")
        del self._recipes[name]

    def list_recipes(self) -> builtins.list[Recipe]:
        return [self._recipes[name] for name in sorted(self._recipes)]

    def contains_recipe(self, name: str) -> bool:
        return name in self._recipes

    # ========================================================================
    # 持久化
    # ========================================================================

    def flush(self) -> None:
        """原子写入 JSON 文件

        注意：JSON 持久化是单进程/单写入者语义，多进程并发写同一文件
        可能丢失更新（无跨进程锁）；多进程场景请使用 SqliteHostRepository。

        Raises:
            CredentialError: allow_plaintext_credentials=False 且存在明文密码
        """
        data = self._serialize()
        self._atomic_write(data)

    def _serialize(self) -> dict:
        """序列化主机 + Profile 到字典，包含版本信息"""
        hosts_dict = {name: host.to_dict() for name, host in self._hosts.items()}

        # 加密密码
        if self._guard.enabled:
            for host_data in hosts_dict.values():
                host_data["password"] = self._guard.encrypt(host_data.get("password"))
        else:
            self._enforce_plaintext_policy(hosts_dict)

        return {
            "version": CONFIG_VERSION,
            "hosts": hosts_dict,
            "profiles": {name: profile.to_dict() for name, profile in self._profiles.items()},
            "recipes": {name: recipe.to_dict() for name, recipe in self._recipes.items()},
        }

    def _enforce_plaintext_policy(self, hosts_dict: dict) -> None:
        """执行明文密码持久化策略（未配置加密器时）。

        集合所有违规主机，一次 flush 最多发出一次警告或抛出一次异常。
        """
        if self._allow_plaintext is True:
            return
        offenders = [
            name
            for name, data in hosts_dict.items()
            if is_plaintext_password(data.get("password"))
        ]
        if not offenders:
            return
        if self._allow_plaintext is False:
            raise CredentialError(
                "refusing to persist plaintext credentials for hosts: "
                f"{', '.join(sorted(offenders))}. "
                "Pass encryption=... to encrypt at rest, or set "
                "allow_plaintext_credentials=True to explicitly opt in."
            )
        warnings.warn(
            f"Plaintext credentials will be persisted for {len(offenders)} host(s): "
            f"{', '.join(sorted(offenders))}. Pass encryption=... to encrypt at rest, or "
            "allow_plaintext_credentials=True to suppress this warning "
            "(v3.0 will reject plaintext persistence by default).",
            PlaintextCredentialWarning,
            stacklevel=4,
        )

    def _load(self) -> None:
        """从 JSON 文件加载主机配置"""
        try:
            with open(self._filepath, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"failed to load config file: {e}")
            return

        # 检查版本并迁移
        version = raw.get("version", 1)
        if version < CONFIG_VERSION:
            logger.info(f"config version {version} -> {CONFIG_VERSION}，running migration")

        hosts_data = raw.get("hosts", raw if version == 1 else {})
        # 兼容 v1 格式（hosts 直接在最外层）
        if version == 1 and isinstance(hosts_data, dict):
            pass  # hosts_data 已经是正确的格式

        self._hosts = {}
        for name, host_data in hosts_data.items():
            pw = host_data.get("password")
            if self._guard.is_encrypted(pw):
                resolved = self._guard.decrypt(pw)
                if resolved is None:
                    logger.error(f"decrypting password for host '{name}' failed")
                host_data["password"] = resolved

            try:
                host = Host.from_dict(host_data)
                self._hosts[name] = host
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"skipping invalid host '{name}': {e}")

        # Profile（v2.8；旧文件无该段时为空）
        self._profiles = {}
        profiles_data = raw.get("profiles", {})
        if isinstance(profiles_data, dict):
            for name, profile_data in profiles_data.items():
                try:
                    self._profiles[name] = HostProfile.from_dict(profile_data)
                except (ValueError, TypeError, KeyError, ValidationError) as e:
                    logger.warning(f"skipping invalid profile '{name}': {e}")

        # Recipe（v2.9；旧文件无该段时为空）
        self._recipes = {}
        recipes_data = raw.get("recipes", {})
        if isinstance(recipes_data, dict):
            for name, recipe_data in recipes_data.items():
                try:
                    self._recipes[name] = Recipe.from_dict(recipe_data)
                except (ValueError, TypeError, KeyError, ValidationError) as e:
                    logger.warning(f"skipping invalid recipe '{name}': {e}")

    def _atomic_write(self, data: dict) -> None:
        """原子写入：写临时文件 → rename 覆盖原文件"""
        self._filepath.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f"{self._filepath.name}.",
            dir=self._filepath.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(self._filepath))
        except Exception:
            # 清理临时文件
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        logger.debug(f"saved {self.count()} host configs to {self._filepath}")

    # ========================================================================
    # 批量操作
    # ========================================================================

    def load_from_dict(self, data: dict[str, Host]) -> None:
        """从字典批量加载主机（替换当前所有）"""
        self._hosts = dict(data)

    def to_dict(self) -> dict[str, Host]:
        """导出所有主机的字典"""
        return dict(self._hosts)
