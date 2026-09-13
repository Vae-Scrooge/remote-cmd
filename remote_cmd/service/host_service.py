"""
主机业务逻辑服务

协调 Repository 和 CredentialProvider 完成主机管理。
职责：
    - 主机 CRUD 委托给 Repository
    - 凭据解析委托给 CredentialProvider 链
    - 密码加密委托给 CredentialEncryption
    - SSH 连接测试委托给 SSHService

使用示例:
    >>> from remote_cmd.service.host_service import HostService
    >>> from remote_cmd.repository.json_host_repository import JsonHostRepository
    >>> from remote_cmd.service.credential_provider import (
    ...     EnvCredentialProvider, ChainCredentialProvider
    ... )
    >>>
    >>> repo = JsonHostRepository("hosts.json")
    >>> cred_provider = ChainCredentialProvider([EnvCredentialProvider()])
    >>> service = HostService(repo, credential_provider=cred_provider)
    >>>
    >>> service.add_host(host)
    >>> service.test_connection("web-server")
"""

import logging
from pathlib import Path
from typing import Any, Optional

from remote_cmd.core.host import Host
from remote_cmd.core.profile import DEFAULT_PORT
from remote_cmd.core.ssh_client import SSHClient
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.repository.profile_store import ProfileStore
from remote_cmd.service.credential_provider import (
    ChainCredentialProvider,
    CredentialProvider,
    EncryptedFileCredentialProvider,
    EnvCredentialProvider,
)
from remote_cmd.service.ssh_service import SSHService
from remote_cmd.utils.crypto import CredentialEncryption, CredentialEncryptionError
from remote_cmd.utils.exceptions import ConfigError

logger = logging.getLogger(__name__)


class HostService:
    """
    主机业务逻辑服务

    Args:
        repository: 主机配置仓库
        credential_provider: 凭据提供者（可选）
        encryption: 密码加密器（可选）
        ssh_service: SSH 连接服务（可选，自动创建）
        profile_store: Profile 存储（可选）。未提供时，若 repository 实现了
            :class:`ProfileStore` 能力协议则自动采用；均不满足时引用 profile
            的主机会在解析时报 ConfigError
    """

    def __init__(
        self,
        repository: HostRepository,
        credential_provider: Optional[CredentialProvider] = None,
        encryption: Optional[CredentialEncryption] = None,
        ssh_service: Optional[SSHService] = None,
        profile_store: Optional[ProfileStore] = None,
    ) -> None:
        self._repo = repository
        self._encryption = encryption or CredentialEncryption()
        self._ssh = ssh_service or SSHService()
        if profile_store is not None:
            self._profile_store: Optional[ProfileStore] = profile_store
        elif isinstance(repository, ProfileStore):
            self._profile_store = repository
        else:
            self._profile_store = None

        if credential_provider:
            self._cred_provider = credential_provider
        else:
            # 默认凭据链: 环境变量 → 加密文件
            self._cred_provider = ChainCredentialProvider(
                [
                    EnvCredentialProvider(),
                    EncryptedFileCredentialProvider(repository, self._encryption),
                ]
            )

    # ========================================================================
    # 主机管理
    # ========================================================================

    def add_host(self, host: Host) -> Host:
        """
        添加主机

        Args:
            host: 主机配置

        Returns:
            Host: 已添加的主机

        Raises:
            ValueError: 同名主机已存在
        """
        if self._repo.contains(host.name):
            raise ValueError(f"Host '{host.name}' already exists")

        # 加密密码（如果明文）
        if host.password and not self._encryption.is_encrypted(host.password):
            host.password = self._encryption.encrypt(host.password)

        self._repo.save(host)
        self._repo.flush()
        logger.info(f"host added: {host.name}")
        return host

    def get_host(self, name: str) -> Host:
        """获取主机配置（合并 profile 连接默认值，密码自动解密）"""
        host = self._repo.get(name)
        return self._decrypt_host(self._apply_profile(host))

    def update_host(self, name: str, **kwargs) -> Host:
        """
        更新主机配置

        Args:
            name: 主机名
            **kwargs: 要更新的字段

        Returns:
            Host: 更新后的主机
        """
        host = self._repo.get(name)
        for key, value in kwargs.items():
            if hasattr(host, key):
                setattr(host, key, value)

        # 如果密码被更新，重新加密
        if (
            "password" in kwargs
            and kwargs["password"] is not None
            and not self._encryption.is_encrypted(kwargs["password"])
        ):
            host.password = self._encryption.encrypt(kwargs["password"])

        self._repo.save(host)
        self._repo.flush()
        logger.info(f"host updated: {name}")
        return host

    def remove_host(self, name: str) -> None:
        """删除主机"""
        self._repo.delete(name)
        self._repo.flush()
        logger.info(f"host removed: {name}")

    def list_hosts(self, tag: Optional[str] = None) -> list[Host]:
        """列出主机（合并 profile 连接默认值，密码自动解密）。

        ``tag`` 过滤基于**合并后**的标签（主机标签 + profile 标签）。
        """
        hosts = [self._decrypt_host(self._apply_profile(h)) for h in self._repo.list()]
        if tag:
            hosts = [h for h in hosts if h.tags and tag in h.tags]
        return hosts

    def list_tags(self) -> list[str]:
        """列出所有标签（主机标签 + 全部 profile 标签的并集）"""
        tags = set(self._repo.list_tags())
        if self._profile_store is not None:
            for profile in self._profile_store.list_profiles():
                tags.update(profile.tags)
        return sorted(tags)

    # ========================================================================
    # 连接管理
    # ========================================================================

    def connect_to_host(self, name: str) -> SSHClient:
        """
        建立到主机的 SSH 连接

        Args:
            name: 主机名

        Returns:
            SSHClient: 已连接的客户端
        """
        host = self.resolve_host(name)
        return self._ssh.create_client(**self._to_ssh_args(host))

    def test_connection(self, name: str) -> bool:
        """
        测试主机连接

        Args:
            name: 主机名

        Returns:
            bool: True if connected
        """
        host = self.resolve_host(name)
        return self._ssh.test_connection(**self._to_ssh_args(host))

    def test_all_connections(self, max_workers: int = 10) -> dict[str, bool]:
        """并行测试所有主机连接"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        hosts = self._repo.list()
        results: dict[str, bool] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(self.test_connection, h.name): h.name for h in hosts}
            for future in as_completed(future_map):
                name = future_map[future]
                try:
                    results[name] = future.result()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"connection test error for host {name}: {e}")
                    results[name] = False

        return results

    def _build_resolved_host(
        self,
        host: Host,
        password: Optional[str],
        key_filename: Optional[str],
    ) -> Host:
        """构造带解析后凭据的 Host 副本，绝不修改仓库内存中的原对象。

        Args:
            host: 仓库中的原始主机对象
            password: 解析后的密码（明文或加密 token）
            key_filename: 解析后的私钥路径

        Returns:
            Host: 新副本
        """
        return Host(
            name=host.name,
            hostname=host.hostname,
            username=host.username,
            port=host.port,
            password=password,
            key_filename=key_filename,
            tags=host.tags,
            description=host.description,
            profile=host.profile,
        )

    def _apply_profile(self, host: Host) -> Host:
        """合并主机引用的 profile 连接默认值（引用式，v2.8）。

        语义（profile 为回退层，主机优先；详见 ``core/profile.py``）：
        - ``username``：主机为空时由 profile 提供
        - ``port``：主机为默认值 22 时由 profile 提供（显式非默认端口优先）
        - ``key_filename``：主机为 None 时由 profile 提供
        - ``tags``：主机标签 + profile 标签（并集去重，主机顺序在前）
        - ``description``：主机为空字符串时由 profile 提供

        Returns:
            Host: 合并后的新副本（绝不修改仓库内存中的原对象）

        Raises:
            ConfigError: 仓库不支持 profile 或引用了未知 profile
        """
        if not host.profile:
            return host
        if self._profile_store is None:
            raise ConfigError(
                f"host '{host.name}' references profile '{host.profile}' "
                "but the repository does not support profiles"
            )
        try:
            profile = self._profile_store.get_profile(host.profile)
        except KeyError:
            raise ConfigError(
                f"host '{host.name}' references unknown profile '{host.profile}'"
            ) from None

        username = host.username or profile.username or host.username
        port = (
            profile.port
            if profile.port is not None and host.port == DEFAULT_PORT
            else host.port
        )
        key_filename = host.key_filename if host.key_filename is not None else profile.key_filename
        tags = list(host.tags)
        for tag in profile.tags:
            if tag not in tags:
                tags.append(tag)
        description = host.description or profile.description

        return Host(
            name=host.name,
            hostname=host.hostname,
            username=username,
            port=port,
            password=host.password,
            key_filename=key_filename,
            tags=tags,
            description=description,
            profile=host.profile,
        )

    def _to_ssh_args(self, host: Host) -> dict[str, Any]:
        """从主机构造 SSH 服务的连接参数字典（create_client / test_connection 共用）。"""
        return {
            "hostname": host.hostname,
            "username": host.username,
            "port": host.port,
            "password": host.password,
            "key_filename": host.key_filename,
        }

    def _decrypt_host(self, host: Host) -> Host:
        """返回主机副本，密码字段自动解密（如已加密）"""
        if host.password and self._encryption.is_encrypted(host.password):
            try:
                decrypted = self._encryption.decrypt(host.password)
                return self._build_resolved_host(host, decrypted, host.key_filename)
            except CredentialEncryptionError as e:
                # 解密失败不应阻塞整批主机返回：保留加密 token，
                # 让 SSH 层在真正连接时报告认证失败
                logger.warning(f"failed to decrypt password for {host.name}: {e}")
        return host

    def resolve_host(self, name: str) -> Host:
        """
        获取主机并尝试解密密码

        解密优先级：
        1. 通过凭据提供链（环境变量 / keyring / 加密文件存储等）获取明文
        2. 若凭据链未命中，则回退到本地 CredentialEncryption 解密存储中的加密 token

        这一层兜底是必需的：CLI 的默认凭据链可能不包含
        EncryptedFileCredentialProvider，但主机密码已被 add_host 加密落盘，
        若不兜底解密则 connect_to_host 会拿到加密 token 当密码使用，必然认证失败。

        Note:
            本方法返回一个新的 Host 副本，绝不就地修改仓库内存中存储的对象。
            若直接修改 repo.get() 返回的原始引用，解密后的明文密码会污染内存
            中的加密 token，后续任意 add_host/update_host/remove_host 触发
            flush() 时明文密码会被写入磁盘，造成凭据泄露。

        Note (v2.8):
            返回的是**合并 profile 后**的有效配置（引用式解析）。
        """
        host = self._apply_profile(self._repo.get(name))

        # 解析密码：始终写入新变量，不修改 host 原对象
        resolved_password = host.password
        if host.password and self._encryption.is_encrypted(host.password):
            resolved = self._cred_provider.get_password(host)
            if resolved:
                resolved_password = resolved
            else:
                # 凭据链未命中，回退到本地加密器解密
                try:
                    resolved_password = self._encryption.decrypt(host.password)
                except CredentialEncryptionError as e:
                    logger.warning(f"failed to decrypt password for {host.name}: {e}")
                    # 保留加密 token，留给 SSH 层报认证失败

        # 解析密钥路径
        resolved_key_filename = host.key_filename
        if resolved_key_filename:
            resolved_key_filename = str(Path(resolved_key_filename).expanduser())

        # 返回新对象，保持仓库内存中的加密 token 不被污染
        return self._build_resolved_host(host, resolved_password, resolved_key_filename)
