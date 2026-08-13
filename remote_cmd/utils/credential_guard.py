"""
凭据加解密守卫模块

统一 JSON / SQLite 仓库层的密码加解密策略，消除两处仓库中重复的
"已加密判定 + 加密 / 解密" 分支：

- ``is_encrypted``: 判定密码是否为加密格式（空值 / 未配置加密器时为 False）
- ``encrypt``: 写入时加密明文密码（已加密 / 未配置加密器则原样返回）
- ``decrypt``: 读取时解密密文密码（未配置加密器则原样返回；解密失败返回 None）

设计约定：
- 本模块不做日志。解密失败时 ``decrypt`` 返回 None，由各仓库在调用点
  按原有级别输出日志（JSON 用 error、SQLite 用 warning），保持行为不变。
- 未配置加密器（``encryption=None``）时守卫不做任何加解密，恒原样返回，
  与仓库"未配置加密则明文落盘"的既有行为一致。
- 解密失败捕获 ``(ValueError, TypeError, KeyError, CredentialEncryptionError)``，
  与最严格的 JSON 仓库实现一致；``CredentialEncryption.decrypt`` 实际只抛
  ``CredentialEncryptionError``，其余为防御性兜底。

用法:
    >>> from remote_cmd.utils.credential_guard import PasswordGuard
    >>> from remote_cmd.utils.crypto import CredentialEncryption
    >>> guard = PasswordGuard(CredentialEncryption())
    >>> guard.is_encrypted("secret")                       # False
    >>> cipher = guard.encrypt("secret")                   # '$encrypted$...'
    >>> guard.is_encrypted(cipher)                         # True
    >>> guard.decrypt(cipher)                              # 'secret'
    >>> guard.encrypt(None)                                # None（不加密空值）
    >>> PasswordGuard().encrypt("secret")                  # 'secret'（未配置加密器）
"""

import logging
from typing import Optional

from remote_cmd.utils.crypto import CredentialEncryption, CredentialEncryptionError

logger = logging.getLogger(__name__)

# 解密失败时的异常集合：与最严格的 JSON 仓库实现保持一致
_DECRYPT_ERRORS = (ValueError, TypeError, KeyError, CredentialEncryptionError)


class PasswordGuard:
    """
    密码加解密守卫

    包装可选的 :class:`CredentialEncryption`，为仓库提供统一的
    写入加密 / 读取解密策略，避免各仓库重复实现相同分支。

    Args:
        encryption: 凭据加密器。为 None 时守卫不做任何加解密，
            ``encrypt`` / ``decrypt`` 均原样返回。
    """

    def __init__(self, encryption: Optional[CredentialEncryption] = None) -> None:
        self._encryption = encryption

    @property
    def enabled(self) -> bool:
        """是否已配置加密器（决定是否执行加解密）。"""
        return self._encryption is not None

    def is_encrypted(self, value: Optional[str]) -> bool:
        """
        判定密码是否为加密格式。

        未配置加密器或值为空时返回 False。

        Args:
            value: 待判定的密码值

        Returns:
            bool: 已加密为 True，否则 False
        """
        if not value or not self._encryption:
            return False
        return self._encryption.is_encrypted(value)

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        """
        写入时加密明文密码。

        语义：
        - ``value`` 为空或未配置加密器 → 原样返回
        - 已加密（``$encrypted$`` 前缀）→ 原样返回，避免二次加密
        - 明文密码 → 加密后返回

        Args:
            value: 待落盘的密码（明文或已加密）

        Returns:
            Optional[str]: 适合落盘的密码值
        """
        if not value or not self._encryption:
            return value
        if self._encryption.is_encrypted(value):
            return value
        return self._encryption.encrypt(value)

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        """
        读取时解密密文密码。

        语义：
        - ``value`` 为空或未配置加密器 → 原样返回
        - 明文密码（非 ``$encrypted$``）→ 原样返回
        - 密文密码 → 解密返回明文；解密失败返回 None（不抛出）

        失败原因在 debug 级记录；调用方需在检测到返回 None 时按原有日志级别
        输出带主机名的提示（JSON 仓库 error、SQLite 仓库 warning）。

        Args:
            value: 存储中的密码值

        Returns:
            Optional[str]: 解密后的明文密码，解密失败时返回 None
        """
        if not value or not self._encryption:
            return value
        if not self._encryption.is_encrypted(value):
            return value
        try:
            return self._encryption.decrypt(value)
        except _DECRYPT_ERRORS as e:
            logger.debug("password decrypt failed: %s", e)
            return None
