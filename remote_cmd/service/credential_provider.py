"""
凭据提供链模块

定义凭据获取的抽象接口和多种实现：
1. EnvCredentialProvider: 从环境变量获取密码
2. EncryptedFileCredentialProvider: 从加密存储获取密码
3. ChainCredentialProvider: 按优先级链式尝试

使用策略:
    >>> from remote_cmd.service.credential_provider import (
    ...     EnvCredentialProvider,
    ...     EncryptedFileCredentialProvider,
    ...     ChainCredentialProvider,
    ... )
    >>> from remote_cmd.repository.json_host_repository import JsonHostRepository
    >>> provider = ChainCredentialProvider([
    ...     EnvCredentialProvider("REMOTE_CMD_PASSWORD"),
    ...     EncryptedFileCredentialProvider(repo),  # 从加密的 hosts.json 读取
    ... ])
    >>> password = provider.get_password(host)
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

from remote_cmd.core.host import Host
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.utils.crypto import CredentialEncryption

logger = logging.getLogger(__name__)


class CredentialProvider(ABC):
    """凭据提供者抽象基类"""

    @abstractmethod
    def get_password(self, host: Host) -> Optional[str]:
        """get a host by name, or None if not found"""
        ...


class EnvCredentialProvider(CredentialProvider):
    """
    从环境变量获取密码

    适用于 CI/CD 或容器环境。
    优先级低于交互式输入但高于默认值。

    支持两种查找方式（按优先级）：
    1. 主机专属变量 ``<env_var>_<HOST>``（主机名大写，非字母数字替换为 ``_``）
    2. 全局变量 ``<env_var>``

    示例：
        - 全局：``REMOTE_CMD_PASSWORD=secret`` 对所有主机生效
        - 专属：``REMOTE_CMD_PASSWORD_WEB1=secret`` 仅对名为 ``web1`` 的主机生效

    Args:
        env_var: 环境变量名（默认 REMOTE_CMD_PASSWORD）
    """

    def __init__(self, env_var: str = "REMOTE_CMD_PASSWORD"):
        self._env_var = env_var

    @staticmethod
    def _host_env_suffix(host_name: str) -> str:
        """
        将主机名转换为环境变量后缀：web1 -> WEB1，my-host -> MY_HOST

        注意: 非字母数字字符统一归一化为下划线，因此 ``web-1`` 与 ``web_1``
        会映射到同一个变量 ``..._WEB_1``。若同舰队同时存在这两种命名，
        需避免依赖同名变量（如需区分请先统一主机命名规范）。
        """
        normalized = "".join(c if c.isalnum() else "_" for c in host_name).upper()
        return normalized

    def get_password(self, host: Host) -> Optional[str]:
        # 优先主机专属变量，避免全局变量被应用到所有主机
        if host and host.name:
            host_var = f"{self._env_var}_{self._host_env_suffix(host.name)}"
            host_password = os.environ.get(host_var)
            if host_password is not None:
                return host_password
        return os.environ.get(self._env_var)


class EncryptedFileCredentialProvider(CredentialProvider):
    """
    从加密的 hosts.json 获取密码

    通过 CredentialEncryption 解密已存储的加密密码。
    适用于持久化保存密码的场景。
    """

    def __init__(
        self,
        repo: HostRepository,
        encryption: Optional[CredentialEncryption] = None,
    ):
        self._repo = repo
        self._encryption = encryption or CredentialEncryption()

    def get_password(self, host: Host) -> Optional[str]:
        try:
            stored_host = self._repo.get(host.name)
            pw = stored_host.password
            if pw and self._encryption.is_encrypted(pw):
                return self._encryption.decrypt(pw)
            return pw
        except (KeyError, ValueError, TypeError):
            return None


class ChainCredentialProvider(CredentialProvider):
    """
    链式凭据提供者

    按顺序尝试每个提供者，返回第一个非空结果。
    适用于 "环境变量 → 加密文件 → 交互式输入" 的优先级链。

    Args:
        providers: 凭据提供者列表，按优先级降序排列
    """

    def __init__(self, providers: list[CredentialProvider]):
        self._providers = list(providers)

    def get_password(self, host: Host) -> Optional[str]:
        for provider in self._providers:
            password = provider.get_password(host)
            if password is not None:
                return password
        return None

    def add_provider(self, provider: CredentialProvider) -> None:
        """在链尾添加一个提供者"""
        self._providers.append(provider)


class KeyringCredentialProvider(CredentialProvider):
    """
    Keyring 凭据提供者

    使用系统 Keyring 服务（Windows Credential Manager / macOS Keychain / Linux Secret Service）
    获取密码。需要安装 keyring 库（pip install keyring）。

    在凭据链中的位置：EnvCredentialProvider 之后，EncryptedFileCredentialProvider 之前。

    Args:
        service_name: Keyring 服务名称，默认 "remote-cmd"
    """

    def __init__(self, service_name: str = "remote-cmd"):
        self._service_name = service_name

    def get_password(self, host: Host) -> Optional[str]:
        """
        从系统 Keyring 获取密码

        使用 keyring.get_password(service_name, host.name) 获取。
        keyring 库为可选依赖，未安装时静默返回 None。

        Args:
            host: 主机配置对象

        Returns:
            Optional[str]: 密码，未找到或不可用时返回 None
        """
        try:
            import keyring

            password = keyring.get_password(self._service_name, host.name)
            if password:
                logger.debug(f"retrieved password for {host.name} ")
            return password
        except ImportError:
            logger.debug("keyring not installed, skipping KeyringCredentialProvider")
            return None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"keyring access failed: {e}")
            return None

    def set_password(self, host: Host, password: str) -> bool:
        """
        向 Keyring 存储密码

        Args:
            host: 主机配置对象
            password: to store

        Returns:
            bool: True if the store succeeded
        """
        try:
            import keyring

            keyring.set_password(self._service_name, host.name, password)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"keyring store failed: {e}")
            return False

    def delete_password(self, host: Host) -> bool:
        """
        从 Keyring 删除密码

        Args:
            host: 主机配置对象

        Returns:
            bool: True if the delete succeeded
        """
        try:
            import keyring

            keyring.delete_password(self._service_name, host.name)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug(f"keyring delete failed: {e}")
            return False
