"""凭据加密模块测试"""

import pytest

from remote_cmd.utils.crypto import CredentialEncryption, CredentialEncryptionError


class TestCredentialEncryption:
    """CredentialEncryption 加解密测试"""

    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        """测试：加密解密往返正常"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)

        password = "my_secret_password_123!"
        encrypted = crypto.encrypt(password)

        assert encrypted != password
        assert encrypted.startswith("$encrypted$")

        decrypted = crypto.decrypt(encrypted)
        assert decrypted == password

    def test_decrypt_wrong_format(self, tmp_path):
        """测试：解密错误格式的密文应抛出异常"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")

        with pytest.raises(CredentialEncryptionError, match="invalid ciphertext format"):
            crypto.decrypt("not_encrypted")

    def test_decrypt_tampered_ciphertext(self, tmp_path):
        """测试：解密被篡改的密文应抛出异常"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")
        encrypted = crypto.encrypt("password")

        tampered = encrypted[:-1] + "X"

        with pytest.raises(CredentialEncryptionError, match="decryption failed"):
            crypto.decrypt(tampered)

    def test_is_encrypted(self, tmp_path):
        """测试：is_encrypted 判断正确"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")

        encrypted = crypto.encrypt("test")
        assert crypto.is_encrypted(encrypted) is True
        assert crypto.is_encrypted("plaintext") is False
        assert crypto.is_encrypted("") is False

    def test_key_file_created_with_correct_permissions(self, tmp_path):
        """测试：密钥文件自动创建且权限正确"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)

        crypto.encrypt("test")

        assert key_path.exists()
        perms = key_path.stat().st_mode & 0o777
        assert perms == 0o600, f"预期 0600，实际 {oct(perms)}"

    def test_reuses_existing_key(self, tmp_path):
        """测试：复用已有密钥文件"""
        key_path = tmp_path / ".key"

        crypto1 = CredentialEncryption(key_path=key_path)
        encrypted = crypto1.encrypt("password")

        crypto2 = CredentialEncryption(key_path=key_path)
        decrypted = crypto2.decrypt(encrypted)
        assert decrypted == "password"

    def test_encrypt_empty_string(self, tmp_path):
        """测试：加密空字符串"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")
        encrypted = crypto.encrypt("")
        assert encrypted.startswith("$encrypted$")
        assert crypto.decrypt(encrypted) == ""

    def test_encrypt_unicode(self, tmp_path):
        """测试：加密包含 Unicode 的密码"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")
        password = "密码123!@#"
        encrypted = crypto.encrypt(password)
        assert crypto.decrypt(encrypted) == password

    def test_is_encrypted_false_on_prefix_collision(self, tmp_path):
        """测试：以 $encrypted$ 开头但非合法 Fernet token 的字符串不应被误判"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")

        # 真实密码恰好以 $encrypted$ 开头但不是 Fernet token
        assert crypto.is_encrypted("$encrypted$plain_password") is False
        # 没有后续 token 部分
        assert crypto.is_encrypted("$encrypted$") is False
        # token 不以 Fernet version byte 'g' 开头
        assert crypto.is_encrypted("$encrypted$hAAAAAB0test") is False
        # token 是合法 base64 但解码后长度不足 57 字节
        assert crypto.is_encrypted("$encrypted$gAAAAAB") is False
        # token 形式上像 Fernet 但内容是随便的 base64（解码后 < 57B）
        assert crypto.is_encrypted("$encrypted$gA") is False

    def test_is_encrypted_true_for_genuine_token(self, tmp_path):
        """测试：真实加密 token 正确识别为已加密"""
        crypto = CredentialEncryption(key_path=tmp_path / ".key")
        encrypted = crypto.encrypt("real_password")
        assert crypto.is_encrypted(encrypted) is True
        # token 部分单独也能通过校验链
        token_part = encrypted[len("$encrypted$") :]
        assert crypto.is_encrypted("$encrypted$" + token_part) is True
