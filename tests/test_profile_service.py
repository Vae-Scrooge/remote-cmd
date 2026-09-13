"""ProfileService 与仓库 ProfileStore 能力测试（v2.8）。"""

import json

import pytest

from remote_cmd.core.host import Host
from remote_cmd.core.profile import HostProfile
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.repository.profile_store import ProfileStore
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.service.profile_service import ProfileService
from remote_cmd.utils.exceptions import ValidationError


class TestProfileStoreCapability:
    def test_builtin_repos_implement_protocol(self, tmp_path):
        assert isinstance(JsonHostRepository(str(tmp_path / "h.json")), ProfileStore)
        assert isinstance(SqliteHostRepository(str(tmp_path / "h.db")), ProfileStore)

    def test_plain_repository_without_protocol(self):
        class Bare:
            pass

        assert not isinstance(Bare(), ProfileStore)


class TestProfileServiceJson:
    def _build(self, tmp_path, with_hosts: bool = True):
        repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        service = ProfileService(store=repo, host_repository=repo if with_hosts else None)
        return repo, service

    def test_add_get_list(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws", username="ec2-user", port=2222))
        assert service.get_profile("aws").username == "ec2-user"
        assert [p.name for p in service.list_profiles()] == ["aws"]

    def test_add_persists_to_disk(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws", key_filename="/k.pem"))
        # JSON 仓库：写操作后 flush 落盘
        raw = json.loads((tmp_path / "hosts.json").read_text(encoding="utf-8"))
        assert raw["profiles"]["aws"]["key_filename"] == "/k.pem"
        # 新实例可加载
        repo2 = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
        assert repo2.get_profile("aws").key_filename == "/k.pem"

    def test_duplicate_add_raises(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws"))
        with pytest.raises(ValueError, match="already exists"):
            service.add_profile(HostProfile(name="aws"))

    def test_update_validates_and_persists(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws"))
        updated = service.update_profile("aws", port=2200, tags=["x"])
        assert updated.port == 2200
        with pytest.raises(ValidationError):
            service.update_profile("aws", port=99999)
        # 失败更新不落盘
        assert service.get_profile("aws").port == 2200

    def test_update_name_rejected(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws"))
        with pytest.raises(ValueError, match="cannot be changed"):
            service.update_profile("aws", name="gcp")

    def test_remove_missing_raises(self, tmp_path):
        repo, service = self._build(tmp_path)
        with pytest.raises(KeyError):
            service.remove_profile("ghost")

    def test_remove_with_reference_requires_force(self, tmp_path):
        repo, service = self._build(tmp_path)
        service.add_profile(HostProfile(name="aws"))
        repo.save(Host(name="web1", hostname="1", username="u", profile="aws"))
        with pytest.raises(ValueError, match="still referenced"):
            service.remove_profile("aws")
        service.remove_profile("aws", force=True)
        assert not repo.contains_profile("aws")

    def test_remove_without_host_repository(self, tmp_path):
        repo, service = self._build(tmp_path, with_hosts=False)
        service.add_profile(HostProfile(name="aws"))
        service.remove_profile("aws")  # 无引用检查来源，直接删除
        assert not repo.contains_profile("aws")


class TestProfileServiceSqlite:
    def test_crud_and_persistence(self, tmp_path):
        db = str(tmp_path / "hosts.db")
        repo = SqliteHostRepository(db)
        service = ProfileService(store=repo, host_repository=repo)

        service.add_profile(
            HostProfile(name="gcp", username="ubuntu", port=22, key_filename="/g.pem", tags=["cloud"])
        )
        service.add_profile(HostProfile(name="aws", username="ec2-user"))

        # SQLite 即时落库：新实例可读
        repo2 = SqliteHostRepository(db)
        names = [p.name for p in repo2.list_profiles()]
        assert names == ["aws", "gcp"]  # 按名称排序
        assert repo2.get_profile("gcp").tags == ["cloud"]

        service.update_profile("gcp", port=2200)
        assert SqliteHostRepository(db).get_profile("gcp").port == 2200

        service.remove_profile("aws")
        assert not repo2.contains_profile("aws")


class TestJsonProfileLoadCompat:
    def test_old_file_without_profiles_section(self, tmp_path):
        path = tmp_path / "hosts.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "hosts": {"web1": {"name": "web1", "hostname": "1", "username": "u"}},
                }
            ),
            encoding="utf-8",
        )
        repo = JsonHostRepository(filepath=str(path))
        assert repo.list_profiles() == []
        assert repo.count() == 1

    def test_invalid_profile_entry_skipped(self, tmp_path):
        path = tmp_path / "hosts.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "hosts": {},
                    "profiles": {
                        "good": {"name": "good", "port": 22},
                        "bad": {"name": "", "port": 22},
                    },
                }
            ),
            encoding="utf-8",
        )
        repo = JsonHostRepository(filepath=str(path))
        assert [p.name for p in repo.list_profiles()] == ["good"]


class TestHostProfileBackendParity:
    """v2.8.1：Host.profile 引用在 JSON / SQLite 双后端往返一致。"""

    def test_host_profile_roundtrip_parity(self, tmp_path):
        from remote_cmd.core.host import Host

        json_repo = JsonHostRepository(filepath=str(tmp_path / "h.json"))
        sqlite_repo = SqliteHostRepository(str(tmp_path / "h.db"))
        host = Host(name="web1", hostname="10.0.0.1", username="", profile="aws")

        for repo in (json_repo, sqlite_repo):
            repo.save_profile(HostProfile(name="aws", username="ec2-user", port=2222))
            repo.save(host)
        json_repo.flush()  # JSON 内存模型需落盘；SQLite 即时生效

        json_reloaded = JsonHostRepository(filepath=str(tmp_path / "h.json")).get("web1")
        sqlite_reloaded = SqliteHostRepository(str(tmp_path / "h.db")).get("web1")
        assert json_reloaded.profile == sqlite_reloaded.profile == "aws"
        assert json_reloaded.username == sqlite_reloaded.username == ""
