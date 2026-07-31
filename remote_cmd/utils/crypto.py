"""
凭据加密模块

使用 Fernet (对称加密) 保护 SSH 密码等敏感信息。
密钥自动生成并存储在 ~/.remote_cmd/.key，权限设为 0600。

用法:
    >>> from remote_cmd.utils.crypto import CredentialEncryption
    >>> crypto = CredentialEncryption()
    >>> encrypted = crypto.encrypt("my_password")
    >>> crypto.decrypt(encrypted)
    'my_password'

安全说明:
    - 密钥文件权限为 0600（仅所有者可读写）
    - 加密密钥永不出现在日志或错误消息中
    - 首次使用时自动生成密钥
"""

import contextlib
import logging
import stat as stat_module
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CredentialEncryptionError(Exception):
    """凭据加解密错误"""

    pass


class CredentialEncryption:
    """
    Fernet 凭据加解密器

    使用 cryptography.fernet 实现对称加密。
    密钥自动管理：首次加密时生成，后续自动加载。

    Attributes:
        key_path: 密钥文件存储路径
    """

    _PREFIX = "$encrypted$"

    def __init__(self, key_path: Optional[Path] = None):
        """
        Args:
            key_path: 密钥文件路径，默认 ~/.remote_cmd/.key
        """
        from cryptography.fernet import Fernet

        self._key_path = key_path or (Path.home() / ".remote_cmd" / ".key")
        self._fernet: Optional[Fernet] = None

    @property
    def _cipher(self):
        """延迟初始化 Fernet 实例"""
        if self._fernet is None:
            key = self._load_or_create_key()
            from cryptography.fernet import Fernet

            self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        """
        加密明文密码

        Args:
            plaintext: 明文密码

        Returns:
            str: Base64 编码的密文字符串（格式: $encrypted$<token>）

        Raises:
            CredentialEncryptionError: 加密失败
        """
        try:
            token = self._cipher.encrypt(plaintext.encode("utf-8"))
            return self._PREFIX + token.decode("utf-8")
        except Exception as e:
            raise CredentialEncryptionError(f"加密失败: {e}") from e

    def decrypt(self, ciphertext: str) -> str:
        """
        解密密文

        Args:
            ciphertext: 加密后的密文字符串

        Returns:
            str: 明文密码

        Raises:
            CredentialEncryptionError: 解密失败或格式错误
        """
        if not ciphertext.startswith(self._PREFIX):
            raise CredentialEncryptionError("错误的密文格式")

        try:
            token = ciphertext[len(self._PREFIX) :].encode("utf-8")
            return self._cipher.decrypt(token).decode("utf-8")
        except Exception as e:
            raise CredentialEncryptionError(f"解密失败: {e}") from e

    def is_encrypted(self, value: str) -> bool:
        """
        检查字符串是否为加密格式

        采用双重校验降低格式碰撞风险：
        1. 前缀匹配 ``$encrypted$``
        2. token 部分必须是 Fernet 格式（version byte 0x80，base64 编码后以 ``g`` 开头，
           且解码后总长度至少 57 字节 = 1 + 8 + 16 + 32）

        这避免了与真实密码恰好以 ``$encrypted$`` 开头时被误判的场景：
        真实密码很少以 ``$encrypted$g`` 开头，且 base64 解码长度必须 >= 57 字节。

        Attention: 本函数不验证 HMAC/签名，仅为廉价标识符检测；
        完整合法性校验在 :meth:`decrypt` 中完成。
        """
        if not value.startswith(self._PREFIX):
            return False
        token = value[len(self._PREFIX) :]
        # Fernet token base64 解码后第一字节恒为 0x80（编码后开头为 "g"）
        if not token.startswith("g"):
            return False
        # 重新填充 base64 并解码长度校验，避免误判纯字符串
        import base64

        padding = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(token + padding)
        except ValueError:
            return False
        # 最小密文长度 = version(1) + timestamp(8) + IV(16) + HMAC(32)
        return len(decoded) >= 57

    def _load_or_create_key(self) -> bytes:
        """
        加载现有密钥或生成新密钥

        密钥文件权限设为 0600，防止其他用户读取。
        """
        from cryptography.fernet import Fernet

        if self._key_path.exists():
            # 确保现有密钥文件权限正确
            try:
                current_perms = stat_module.S_IMODE(self._key_path.stat().st_mode)
                if current_perms != 0o600:
                    self._key_path.chmod(0o600)
            except OSError:
                pass  # Windows 或权限不足时忽略
            return self._key_path.read_bytes()

        # 生成新密钥
        key = Fernet.generate_key()
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key_path.write_bytes(key)
        with contextlib.suppress(OSError):
            self._key_path.chmod(0o600)

        logger.info(f"已生成加密密钥: {self._key_path}")
        return key
