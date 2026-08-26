"""异常层次结构（utils/exceptions.py）兼容性与新增类型测试

v2.1 新增异常均为**既有异常的子类**（或别名），本文件锁定：
- 既有捕获行为不受影响（新异常可被旧父类捕获）
- 永久性 / 瞬态分类所需的类型区分可用
- CredentialEncryptionError 归入 RemoteCmdError 层级（v2.1 起继承 CredentialError）
"""

from __future__ import annotations

import pytest

from remote_cmd.utils.crypto import CredentialEncryptionError
from remote_cmd.utils.exceptions import (
    ConfigError,
    ConfigurationError,
    CredentialError,
    RemoteCmdError,
    SSHAuthenticationError,
    SSHCommandError,
    SSHCommandTimeoutError,
    SSHConnectionError,
    SSHError,
    SSHFileTransferError,
    SSHTimeoutError,
    ValidationError,
)


class TestHierarchyCompatibility:
    """新异常必须可被既有 except 子句捕获（API 兼容契约）"""

    def test_auth_error_catchable_as_connection_error(self):
        with pytest.raises(SSHConnectionError):
            raise SSHAuthenticationError("denied")

    def test_timeout_error_catchable_as_connection_error(self):
        with pytest.raises(SSHConnectionError):
            raise SSHTimeoutError("connect timeout")

    def test_command_timeout_catchable_as_command_error(self):
        with pytest.raises(SSHCommandError):
            raise SSHCommandTimeoutError("command timeout")

    def test_all_ssh_errors_catchable_as_ssh_error(self):
        for exc_cls in (
            SSHConnectionError,
            SSHAuthenticationError,
            SSHTimeoutError,
            SSHCommandError,
            SSHCommandTimeoutError,
            SSHFileTransferError,
        ):
            with pytest.raises(SSHError):
                raise exc_cls("boom")

    def test_all_errors_catchable_as_base(self):
        for exc_cls in (
            SSHError,
            ConfigError,
            CredentialError,
            ValidationError,
        ):
            with pytest.raises(RemoteCmdError):
                raise exc_cls("boom")

    def test_configuration_error_is_alias(self):
        assert ConfigurationError is ConfigError


class TestCredentialEncryptionErrorHierarchy:
    """CredentialEncryptionError 归入层级（v2.1 兼容性增强）"""

    def test_is_credential_error(self):
        with pytest.raises(CredentialError):
            raise CredentialEncryptionError("decrypt failed")

    def test_is_remotecmd_error(self):
        with pytest.raises(RemoteCmdError):
            raise CredentialEncryptionError("decrypt failed")

    def test_still_importable_from_crypto(self):
        # 既有导入路径不破坏
        import remote_cmd.utils.crypto as crypto_mod

        assert crypto_mod.CredentialEncryptionError is CredentialEncryptionError


class TestSiblingIsolation:
    """分类区分度：永久性类型不被瞬态父类之外的兄弟误伤"""

    def test_auth_is_not_command_error(self):
        assert not issubclass(SSHAuthenticationError, SSHCommandError)

    def test_command_timeout_is_not_connection_error(self):
        assert not issubclass(SSHCommandTimeoutError, SSHConnectionError)

    def test_credential_error_is_not_ssh_error(self):
        assert not issubclass(CredentialError, SSHError)
