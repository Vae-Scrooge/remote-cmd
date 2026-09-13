"""KeyringCredentialProvider 凭据提供者测试"""

from unittest.mock import patch

import keyring

from remote_cmd.core.host import Host
from remote_cmd.service.credential_provider import KeyringCredentialProvider


class TestKeyringCredentialProvider:
    """KeyringCredentialProvider 测试

    实际方法签名：
    - get_password(host: Host) -> Optional[str]
    - set_password(host: Host, password: str) -> bool
    - delete_password(host: Host) -> bool
    """

    def make_host(self, name="myhost"):
        return Host(name=name, hostname="10.0.0.1", username="admin")

    def test_get_password_returns_none_when_no_keyring(self):
        provider = KeyringCredentialProvider()
        # 模拟 keyring 未安装
        import sys

        orig = sys.modules.get("keyring")
        sys.modules["keyring"] = None
        try:
            result = provider.get_password(self.make_host())
            assert result is None
        finally:
            if orig:
                sys.modules["keyring"] = orig
            else:
                del sys.modules["keyring"]

    def test_get_password_found(self):
        provider = KeyringCredentialProvider()
        host = self.make_host("srv1")
        with patch("keyring.get_password", return_value="secret123"):
            password = provider.get_password(host)
            assert password == "secret123"

    def test_get_password_not_found(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.get_password", return_value=None):
            result = provider.get_password(self.make_host("ghost"))
            assert result is None

    def test_get_password_called_with_service_name(self):
        provider = KeyringCredentialProvider(service_name="my-tool")
        host = self.make_host("srv1")
        with patch("keyring.get_password") as mock_get:
            mock_get.return_value = "pass"
            provider.get_password(host)
            mock_get.assert_called_once_with("my-tool", "srv1")

    def test_set_password_success(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.set_password") as mock_set:
            result = provider.set_password(self.make_host("srv1"), "newpass")
            assert result is True
            mock_set.assert_called_once_with("remote-cmd", "srv1", "newpass")

    def test_set_password_failure(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.set_password", side_effect=keyring.errors.KeyringError("denied")):
            result = provider.set_password(self.make_host("srv1"), "newpass")
            assert result is False

    def test_delete_password_success(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.delete_password") as mock_del:
            result = provider.delete_password(self.make_host("srv1"))
            assert result is True
            mock_del.assert_called_once_with("remote-cmd", "srv1")

    def test_delete_password_failure(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.delete_password", side_effect=keyring.errors.KeyringError("not found")):
            result = provider.delete_password(self.make_host("srv1"))
            assert result is False

    def test_service_name_configurable(self):
        provider = KeyringCredentialProvider(service_name="my-app")
        with patch("keyring.set_password") as mock_set:
            provider.set_password(self.make_host("h1"), "pass")
            mock_set.assert_called_once_with("my-app", "h1", "pass")

    def test_get_password_keyring_exception(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.get_password", side_effect=keyring.errors.KeyringError("keyring locked")):
            result = provider.get_password(self.make_host("srv1"))
            assert result is None

    def test_set_password_uses_host_name(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.set_password") as mock_set:
            provider.set_password(self.make_host("web-01"), "secret")
            mock_set.assert_called_once_with("remote-cmd", "web-01", "secret")

    def test_delete_password_uses_host_name(self):
        provider = KeyringCredentialProvider()
        with patch("keyring.delete_password") as mock_del:
            provider.delete_password(self.make_host("db-01"))
            mock_del.assert_called_once_with("remote-cmd", "db-01")
