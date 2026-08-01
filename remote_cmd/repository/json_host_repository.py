"""
JSON 文件主机仓库实现

使用 JSON 文件存储主机配置，支持：
- 原子写入（先写临时文件再重命名，防止崩溃导致数据丢失）
- 可选加密（通过 CredentialEncryption 加密 password 字段）
- config version management
- 自动从旧版本迁移
"""

import builtins
import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from remote_cmd.core.host import Host
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.utils.crypto import CredentialEncryption, CredentialEncryptionError

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
    """

    def __init__(
        self,
        filepath: str,
        encryption: Optional[CredentialEncryption] = None,
        auto_load: bool = True,
    ):
        self._filepath = Path(filepath)
        self._encryption = encryption
        self._hosts: dict[str, Host] = {}

        if auto_load and self._filepath.exists():
            self._load()

    # ========================================================================
    # Repository 接口实现
    # ========================================================================

    def save(self, host: Host) -> None:
        """保存主机到内存，随后需要调用 flush() 写入文件"""
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
    # 持久化
    # ========================================================================

    def flush(self) -> None:
        """原子写入 JSON 文件"""
        data = self._serialize_hosts()
        self._atomic_write(data)

    def _serialize_hosts(self) -> dict:
        """序列化主机列表到字典，包含版本信息"""
        hosts_dict = {name: host.to_dict() for name, host in self._hosts.items()}

        # 加密密码
        if self._encryption:
            for host_data in hosts_dict.values():
                pw = host_data.get("password")
                if pw and not self._encryption.is_encrypted(pw):
                    host_data["password"] = self._encryption.encrypt(pw)

        return {
            "version": CONFIG_VERSION,
            "hosts": hosts_dict,
        }

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
            if pw and self._encryption and self._encryption.is_encrypted(pw):
                try:
                    host_data["password"] = self._encryption.decrypt(pw)
                except (ValueError, TypeError, KeyError, CredentialEncryptionError) as e:
                    logger.error(f"decrypting password for host '{name}' failed: {e}")
                    host_data["password"] = None

            try:
                host = Host.from_dict(host_data)
                self._hosts[name] = host
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"skipping invalid host '{name}': {e}")

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
