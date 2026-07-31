"""JSON 主机仓库测试"""

import pytest

from remote_cmd.core.host import Host
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.utils.crypto import CredentialEncryption


class TestJsonHostRepository:
    """JsonHostRepository 单元测试"""

    def test_add_and_get(self, tmp_path):
        """测试：添加并获取主机"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        host = Host(name="test-server", hostname="10.0.0.1", username="root")
        repo.save(host)
        repo.flush()

        repo2 = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        loaded = repo2.get("test-server")
        assert loaded.name == "test-server"
        assert loaded.hostname == "10.0.0.1"

    def test_get_nonexistent_raises(self, tmp_path):
        """测试：获取不存在的主机应抛出 KeyError"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        with pytest.raises(KeyError):
            repo.get("nonexistent")

    def test_delete(self, tmp_path):
        """测试：删除主机"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="root"))
        repo.delete("srv1")
        assert repo.contains("srv1") is False

    def test_delete_nonexistent_raises(self, tmp_path):
        """测试：删除不存在的主机应抛出 KeyError"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        with pytest.raises(KeyError):
            repo.delete("nonexistent")

    def test_list_with_tag_filter(self, tmp_path):
        """测试：按标签筛选主机"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        repo.save(Host(name="web", hostname="10.0.0.1", username="root", tags=["web", "prod"]))
        repo.save(Host(name="db", hostname="10.0.0.2", username="root", tags=["db", "prod"]))
        repo.save(Host(name="dev", hostname="10.0.0.3", username="root", tags=["dev"]))

        web_hosts = repo.list(tag="web")
        assert len(web_hosts) == 1
        assert web_hosts[0].name == "web"

        prod_hosts = repo.list(tag="prod")
        assert len(prod_hosts) == 2

    def test_list_tags(self, tmp_path):
        """测试：列出所有标签"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        repo.save(Host(name="a", hostname="1", username="u", tags=["web", "prod"]))
        repo.save(Host(name="b", hostname="2", username="u", tags=["db"]))

        tags = repo.list_tags()
        assert tags == ["db", "prod", "web"]

    def test_empty_repo(self, tmp_path):
        """测试：空仓库"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        assert repo.count() == 0
        assert repo.list() == []
        assert repo.list_tags() == []

    def test_contains_and_count(self, tmp_path):
        """测试：contains 和 count"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        assert repo.contains("x") is False
        assert repo.count() == 0

        repo.save(Host(name="x", hostname="1", username="u"))
        assert repo.contains("x") is True
        assert repo.count() == 1

    def test_versioned_json_format(self, tmp_path):
        """测试：JSON 输出包含版本信息"""
        path = tmp_path / "hosts.json"
        repo = JsonHostRepository(filepath=str(path))
        repo.save(Host(name="srv", hostname="10.0.0.1", username="root"))
        repo.flush()

        import json

        with open(path) as f:
            data = json.load(f)
        assert "version" in data
        assert "hosts" in data
        assert data["version"] == 2
        assert "srv" in data["hosts"]

    def test_load_from_empty_file(self, tmp_path):
        """测试：从空文件加载应安全处理"""
        path = tmp_path / "empty.json"
        path.write_text("{}")
        repo = JsonHostRepository(filepath=str(path))
        assert repo.count() == 0

    def test_load_from_missing_file(self, tmp_path):
        """测试：从缺失文件加载应安全处理"""
        repo = JsonHostRepository(filepath=str(tmp_path / "missing.json"))
        assert repo.count() == 0


class TestJsonHostRepositoryEncryption:
    """带加密功能的仓库测试"""

    def test_encrypted_password_storage(self, tmp_path):
        """测试：密码在 JSON 文件中被加密存储"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)

        path = tmp_path / "hosts.json"
        repo = JsonHostRepository(filepath=str(path), encryption=crypto)

        repo.save(Host(name="secure", hostname="10.0.0.1", username="admin", password="secret"))
        repo.flush()

        import json

        with open(path) as f:
            data = json.load(f)
        stored_pw = data["hosts"]["secure"]["password"]
        assert stored_pw.startswith("$encrypted$")

        # 解密后验证
        decrypted = crypto.decrypt(stored_pw)
        assert decrypted == "secret"

    def test_encrypted_password_loaded_and_decrypted(self, tmp_path):
        """测试：从加密文件加载后密码自动解密"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)

        path = str(tmp_path / "hosts.json")
        repo = JsonHostRepository(filepath=path, encryption=crypto)
        repo.save(Host(name="secure", hostname="10.0.0.1", username="admin", password="secret"))
        repo.flush()

        repo2 = JsonHostRepository(filepath=path, encryption=crypto)
        loaded = repo2.get("secure")
        assert loaded.password == "secret"

    def test_mixed_encrypted_and_plain(self, tmp_path):
        """测试：加密仓库中已加密的字段解密，未加密的保持原样"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)

        path = str(tmp_path / "hosts.json")
        repo = JsonHostRepository(filepath=path, encryption=crypto)

        repo.save(
            Host(
                name="plain",
                hostname="1",
                username="u",
                password=None,
                key_filename="~/.ssh/id_rsa",
            )
        )
        repo.flush()

        repo2 = JsonHostRepository(filepath=path, encryption=crypto)
        loaded = repo2.get("plain")
        assert loaded.password is None
        assert loaded.key_filename == "~/.ssh/id_rsa"

    def test_decryption_failure_returns_none_password(self, tmp_path):
        """测试：解密失败时密码设为 None"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)
        path = str(tmp_path / "hosts.json")
        repo = JsonHostRepository(filepath=path, encryption=crypto)
        repo.save(Host(name="srv", hostname="1", username="u", password="secret"))
        repo.flush()

        # 篡改密码字段：使用另一把密钥加密生成的合法 Fernet token，
        # 以保证 is_encrypted() 通过校验（格式合法），但解密失败（HMAC 不匹配），
        # 触发 "解密失败" 分支 → password = None
        other_key_path = tmp_path / ".other_key"
        other_crypto = CredentialEncryption(key_path=other_key_path)
        tampered_token = other_crypto.encrypt("will_not_decrypt_correctly")

        import json

        with open(path) as f:
            data = json.load(f)
        data["hosts"]["srv"]["password"] = tampered_token
        with open(path, "w") as f:
            json.dump(data, f)

        repo2 = JsonHostRepository(filepath=path, encryption=crypto)
        loaded = repo2.get("srv")
        assert loaded.password is None

    def test_skip_invalid_host_data(self, tmp_path):
        """测试：跳过无效主机数据"""
        import json

        path = tmp_path / "hosts.json"
        repo = JsonHostRepository(filepath=str(path))
        repo.save(Host(name="good", hostname="1", username="u"))
        repo.flush()

        # 写入无效主机
        with open(path) as f:
            data = json.load(f)
        data["hosts"]["bad"] = {"hostname": None}  # 缺少必要字段
        with open(path, "w") as f:
            json.dump(data, f)

        repo2 = JsonHostRepository(filepath=str(path))
        # 应安全跳过 bad，只加载 good
        assert repo2.count() == 1
        assert repo2.contains("good")

    def test_load_from_dict(self, tmp_path):
        """测试：load_from_dict / to_dict"""
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        hosts = {
            "a": Host(name="a", hostname="1", username="u"),
            "b": Host(name="b", hostname="2", username="u"),
        }
        repo.load_from_dict(hosts)
        assert repo.count() == 2
        d = repo.to_dict()
        assert set(d.keys()) == {"a", "b"}

    def test_flush_persists(self, tmp_path):
        """测试：flush 后数据持久化到磁盘"""
        path = tmp_path / "hosts.json"
        repo = JsonHostRepository(filepath=str(path))
        repo.save(Host(name="p", hostname="1", username="u"))
        repo.flush()
        assert path.exists()
        assert path.stat().st_size > 10
