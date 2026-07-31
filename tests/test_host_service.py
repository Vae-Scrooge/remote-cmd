"""主机业务逻辑服务测试"""

from unittest.mock import MagicMock

import pytest

from remote_cmd.core.host import Host
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService
from remote_cmd.utils.crypto import CredentialEncryption


@pytest.fixture
def repo(tmp_path):
    return JsonHostRepository(filepath=str(tmp_path / "hosts.json"))


@pytest.fixture
def service(repo):
    return HostService(repository=repo)


class TestHostService:
    """HostService 业务逻辑测试"""

    def test_add_host(self, service):
        """测试：添加主机"""
        host = Host(name="srv1", hostname="10.0.0.1", username="root", tags=["prod"])
        result = service.add_host(host)
        assert result.name == "srv1"

        loaded = service.get_host("srv1")
        assert loaded.hostname == "10.0.0.1"

    def test_add_duplicate_raises(self, service):
        """测试：添加同名主机应抛出 ValueError"""
        service.add_host(Host(name="dup", hostname="1", username="u"))
        with pytest.raises(ValueError, match="已存在"):
            service.add_host(Host(name="dup", hostname="2", username="u"))

    def test_get_host(self, service):
        """测试：获取主机"""
        service.add_host(Host(name="srv", hostname="10.0.0.1", username="root"))
        host = service.get_host("srv")
        assert host.name == "srv"
        assert host.hostname == "10.0.0.1"

    def test_get_nonexistent_raises(self, service):
        """测试：获取不存在的主机应抛出 KeyError"""
        with pytest.raises(KeyError):
            service.get_host("phantom")

    def test_update_host(self, service):
        """测试：更新主机"""
        service.add_host(Host(name="srv", hostname="10.0.0.1", username="root", port=22))
        service.update_host("srv", port=2222, description="updated")

        updated = service.get_host("srv")
        assert updated.port == 2222
        assert updated.description == "updated"

    def test_remove_host(self, service):
        """测试：删除主机"""
        service.add_host(Host(name="srv", hostname="1", username="u"))
        service.remove_host("srv")
        assert len(service.list_hosts()) == 0

    def test_remove_nonexistent_raises(self, service):
        """测试：删除不存在的主机应抛出 KeyError"""
        with pytest.raises(KeyError):
            service.remove_host("phantom")

    def test_list_hosts(self, service):
        """测试：列出主机"""
        service.add_host(Host(name="a", hostname="1", username="u"))
        service.add_host(Host(name="b", hostname="2", username="u"))
        hosts = service.list_hosts()
        assert len(hosts) == 2

    def test_list_hosts_by_tag(self, service):
        """测试：按标签列出主机"""
        service.add_host(Host(name="web", hostname="1", username="u", tags=["web"]))
        service.add_host(Host(name="db", hostname="2", username="u", tags=["db"]))

        web_hosts = service.list_hosts(tag="web")
        assert len(web_hosts) == 1
        assert web_hosts[0].name == "web"

    def test_list_tags(self, service):
        """测试：列出所有标签"""
        service.add_host(Host(name="a", hostname="1", username="u", tags=["web", "prod"]))
        service.add_host(Host(name="b", hostname="2", username="u", tags=["db"]))
        tags = service.list_tags()
        assert set(tags) == {"web", "prod", "db"}

    def test_add_encrypts_password(self, tmp_path):
        """测试：添加主机时密码被自动加密"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"), encryption=crypto)
        svc = HostService(repository=repo, encryption=crypto)

        svc.add_host(Host(name="secure", hostname="10.0.0.1", username="root", password="secret"))

        # 从文件读取，验证密码已加密
        import json

        with open(tmp_path / "hosts.json") as f:
            data = json.load(f)
        stored_pw = data["hosts"]["secure"]["password"]
        assert stored_pw.startswith("$encrypted$")

        # 通过 service 读取，密码自动解密
        loaded = svc.get_host("secure")
        assert loaded.password == "secret"

    # ========================================================================
    # 连接管理测试
    # ========================================================================

    def test_connect_to_host(self, service):
        """测试：建立 SSH 连接"""
        from unittest.mock import patch

        with patch.object(service._ssh, "create_client") as m:
            service.add_host(Host(name="srv", hostname="10.0.0.1", username="admin"))
            service.connect_to_host("srv")
            m.assert_called_once_with(
                hostname="10.0.0.1",
                username="admin",
                port=22,
                password=None,
                key_filename=None,
            )

    def test_test_connection_success(self, service):
        """测试：连接测试成功"""
        from unittest.mock import patch

        with patch.object(service._ssh, "test_connection", return_value=True) as m:
            service.add_host(Host(name="srv", hostname="10.0.0.1", username="admin"))
            result = service.test_connection("srv")
            assert result is True
            m.assert_called_once()

    def test_test_connection_failure(self, service):
        """测试：连接测试失败"""
        from unittest.mock import patch

        with patch.object(service._ssh, "test_connection", return_value=False):
            service.add_host(Host(name="srv", hostname="10.0.0.1", username="admin"))
            result = service.test_connection("srv")
            assert result is False

    def test_test_all_connections(self, service):
        """测试：并行连接测试"""
        from unittest.mock import patch

        with patch.object(service, "test_connection") as m:
            m.side_effect = lambda name: {"a": True, "b": False, "c": True}.get(name, False)
            service.add_host(Host(name="a", hostname="1", username="u"))
            service.add_host(Host(name="b", hostname="2", username="u"))
            service.add_host(Host(name="c", hostname="3", username="u"))
            results = service.test_all_connections()
            assert results == {"a": True, "b": False, "c": True}

    def test_update_host_with_password_encrypts(self, tmp_path):
        """测试：update_host 时密码被自动加密"""
        key_path = tmp_path / ".key"
        crypto = CredentialEncryption(key_path=key_path)
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"), encryption=crypto)
        svc = HostService(repository=repo, encryption=crypto)

        svc.add_host(Host(name="srv", hostname="10.0.0.1", username="admin"))
        svc.update_host("srv", password="newsecret")

        updated = svc.get_host("srv")
        assert updated.password == "newsecret"

        # 磁盘上应存为加密形式
        import json

        with open(tmp_path / "hosts.json") as f:
            data = json.load(f)
        stored = data["hosts"]["srv"]["password"]
        assert stored.startswith("$encrypted$")

    def test_decrypt_host_failure_logs_warning(self, service):
        """测试：密码解密失败时安静返回原始主机"""
        from unittest.mock import patch

        # patch 需在 add_host 之前生效，否则真实的 is_encrypted 会将
        # 伪装的 "$encrypted$bad" 视为明文并二次加密
        patch_enc = patch.object(service._encryption, "is_encrypted", return_value=True)
        patch_dec = patch.object(
            service._encryption, "decrypt", side_effect=Exception("decrypt fail")
        )
        patch_enc.start()
        patch_dec.start()
        try:
            host = Host(name="srv", hostname="1", username="u", password="$encrypted$bad")
            service.add_host(host)
            result = service.get_host("srv")
            assert result.password == "$encrypted$bad"
        finally:
            patch_enc.stop()
            patch_dec.stop()

    def test_connect_to_host_resolves_key_path(self, service, tmp_path):
        """测试：resolve_host 展开密钥路径"""
        key_file = tmp_path / ".ssh" / "id_aws"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("key")
        host = Host(name="srv", hostname="1", username="u", key_filename=str(key_file))
        service.add_host(host)
        resolved = service.resolve_host("srv")
        assert resolved.key_filename == str(key_file)

    def test_test_all_connections_with_exception(self, service):
        """测试：并行连接测试中个别主机抛异常"""
        from unittest.mock import patch

        def mock_test(name):
            if name == "a":
                return True
            raise Exception("boom")

        with patch.object(service, "test_connection") as m:
            m.side_effect = mock_test
            service.add_host(Host(name="a", hostname="1", username="u"))
            service.add_host(Host(name="b", hostname="2", username="u"))
            results = service.test_all_connections()
            assert results == {"a": True, "b": False}

    def test_custom_credential_provider(self, tmp_path):
        """测试：传入自定义凭证提供者"""

        from remote_cmd.service.credential_provider import CredentialProvider

        provider = MagicMock(spec=CredentialProvider)
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        svc = HostService(repository=repo, credential_provider=provider)
        assert svc._cred_provider is provider


# ============================================================================
# P0-A 回归测试：加密存储的主机连接时解密链路
# ============================================================================


class TestEncryptedPasswordConnectRoundtrip:
    """回归：add_host 加密密码 → connect_to_host 应能拿到明文密码传给 SSHClient

    历史问题：cli _build_service 的凭据链仅含 EnvCredentialProvider，
    而 resolve_host 没有 _encryption.decrypt 兜底，导致加密存储的主机
    连接时把 $encrypted$... token 当作密码送给 SSH，必然认证失败。
    """

    def test_connect_to_host_decrypts_password_for_ssh(self, repo):
        """resolve_host 在凭据链未命中时回退 _encryption.decrypt，
        传给 SSHService.create_client 的 password 应是明文。"""
        from remote_cmd.service.credential_provider import (
            ChainCredentialProvider,
            EnvCredentialProvider,
        )

        # 与 CLI 的 _build_service 等价：链上只有 EnvCredentialProvider
        # 不含 EncryptedFileCredentialProvider，迫使 resolve_host 走兜底
        cred_chain = ChainCredentialProvider([EnvCredentialProvider()])
        service = HostService(repository=repo, credential_provider=cred_chain)

        # add_host 会用内置 CredentialEncryption 加密保存密码
        plain = "super-secret-123"
        service.add_host(Host(name="srv", hostname="10.0.0.1", username="root", password=plain))

        # 验证落盘的密码确实加密了（非明文）
        stored = repo.get("srv")
        assert stored.password != plain, "add_host 应加密保存密码"

        # mock SSHService.create_client，断言传入的是明文密码
        ssh_mock = MagicMock()
        service._ssh = ssh_mock
        service.connect_to_host("srv")

        ssh_mock.create_client.assert_called_once()
        kwargs = ssh_mock.create_client.call_args.kwargs
        assert kwargs["password"] == plain, (
            f"传给 SSH 的应是明文密码 {plain!r}, 实际是 {kwargs['password']!r}"
            " —— resolve_host 兜底解密失效"
        )
        assert kwargs["hostname"] == "10.0.0.1"
        assert kwargs["username"] == "root"

    def test_env_provider_takes_precedence_over_encrypted(self, repo):
        """环境变量命中时优先使用环境变量密码，不调 _encryption.decrypt。"""
        import os
        from unittest.mock import patch

        from remote_cmd.service.credential_provider import (
            ChainCredentialProvider,
            EnvCredentialProvider,
        )

        cred_chain = ChainCredentialProvider([EnvCredentialProvider()])
        service = HostService(repository=repo, credential_provider=cred_chain)

        service.add_host(
            Host(name="srv", hostname="10.0.0.1", username="root", password="disk_plain")
        )

        # 设置环境变量覆盖
        with patch.dict(os.environ, {"REMOTE_CMD_PASSWORD": "env_plain"}):
            ssh_mock = MagicMock()
            service._ssh = ssh_mock
            service.connect_to_host("srv")

        kwargs = ssh_mock.create_client.call_args.kwargs
        assert kwargs["password"] == "env_plain"

    def test_encrypted_file_provider_in_chain_avoids_fallback(self, tmp_path):
        """若凭据链含 EncryptedFileCredentialProvider，它应直接命中，
        无需依赖 resolve_host 的 _encryption.decrypt 兜底分支。"""
        from remote_cmd.service.credential_provider import (
            ChainCredentialProvider,
            EncryptedFileCredentialProvider,
            EnvCredentialProvider,
        )

        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        cred_chain = ChainCredentialProvider(
            [
                EnvCredentialProvider(),
                EncryptedFileCredentialProvider(repo),
            ]
        )
        service = HostService(repository=repo, credential_provider=cred_chain)

        plain = "stored-via-add"
        service.add_host(Host(name="srv", hostname="10.0.0.1", username="root", password=plain))

        # 直接调 cred_chain，应能解密（无需靠 resolve_host 兜底）
        fetched = repo.get("srv")
        resolved = cred_chain.get_password(fetched)
        assert resolved == plain

        # connect_to_host 也应成功（无论由链解密还是兜底解密）
        ssh_mock = MagicMock()
        service._ssh = ssh_mock
        service.connect_to_host("srv")
        kwargs = ssh_mock.create_client.call_args.kwargs
        assert kwargs["password"] == plain
