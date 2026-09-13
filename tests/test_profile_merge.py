"""引用式 Profile 合并语义测试（v2.8）。

覆盖 core/profile.py 文档化的合并规则，以及 HostService 有效视图、
未知 profile 错误映射、无 ProfileStore 仓库的降级错误。
"""

from __future__ import annotations

from typing import Optional

import pytest

from remote_cmd.core.host import Host
from remote_cmd.core.profile import HostProfile
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.batch_executor import BatchExecutor
from remote_cmd.service.host_service import HostService
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.exceptions import ConfigError


@pytest.fixture
def setup(tmp_path):
    repo = JsonHostRepository(filepath=str(tmp_path / "hosts.json"))
    service = HostService(repository=repo, encryption=CredentialEncryption(key_path=tmp_path / ".key"))
    return repo, service


def _add_host(repo, name="web1", **kwargs) -> Host:
    defaults = {"hostname": "10.0.0.1", "username": "admin"}
    defaults.update(kwargs)
    host = Host(name=name, **defaults)
    repo.save(host)
    return host


class TestMergeRules:
    def test_port_merge_when_default(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", port=2222))
        _add_host(repo, profile="aws")  # port 默认 22
        assert service.resolve_host("web1").port == 2222

    def test_explicit_non_default_port_wins(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", port=2222))
        _add_host(repo, profile="aws", port=2020)
        assert service.resolve_host("web1").port == 2020

    def test_key_filename_merge_and_host_priority(self, setup):
        """相对文件名在 POSIX/Windows 上语义一致（expanduser 为 no-op），
        避免硬编码 POSIX 根路径导致 Windows 下被 Path 归一化为反斜杠。"""
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", key_filename="profile.pem"))
        _add_host(repo, name="a", profile="aws")
        _add_host(repo, name="b", profile="aws", key_filename="host.pem")
        assert service.resolve_host("a").key_filename == "profile.pem"
        assert service.resolve_host("b").key_filename == "host.pem"

    def test_tags_union_host_first(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", tags=["cloud", "prod"]))
        _add_host(repo, profile="aws", tags=["prod", "web"])
        assert service.resolve_host("web1").tags == ["prod", "web", "cloud"]

    def test_description_fallback(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", description="profile desc"))
        _add_host(repo, name="a", profile="aws")
        _add_host(repo, name="b", profile="aws", description="host desc")
        assert service.resolve_host("a").description == "profile desc"
        assert service.resolve_host("b").description == "host desc"

    def test_username_fallback_when_empty(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", username="ec2-user"))
        _add_host(repo, profile="aws", username="")
        assert service.resolve_host("web1").username == "ec2-user"

    def test_username_live_default_propagates_after_update(self, setup):
        """审计 P1：存空 username 的引用主机随 profile.username 更新而变化。"""
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", username="ec2-user"))
        _add_host(repo, profile="aws", username="")
        assert service.resolve_host("web1").username == "ec2-user"

        profile = repo.get_profile("aws")
        profile.username = "ubuntu"
        repo.save_profile(profile)
        assert service.resolve_host("web1").username == "ubuntu"

    def test_existing_stored_username_not_changed_by_profile_update(self, setup):
        """已有主机（存储了 username）保持显式覆盖语义，不随 profile 更新。"""
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", username="ec2-user"))
        _add_host(repo, profile="aws", username="legacy")

        profile = repo.get_profile("aws")
        profile.username = "ubuntu"
        repo.save_profile(profile)
        assert service.resolve_host("web1").username == "legacy"

    def test_username_host_wins(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", username="ec2-user"))
        _add_host(repo, profile="aws", username="deploy")
        assert service.resolve_host("web1").username == "deploy"

    def test_no_profile_is_noop(self, setup):
        repo, service = setup
        _add_host(repo)
        resolved = service.resolve_host("web1")
        assert resolved.profile is None
        assert resolved.port == 22

    def test_reference_model_repo_object_unchanged(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", port=2222, key_filename="/k.pem"))
        _add_host(repo, profile="aws")
        service.resolve_host("web1")
        raw = repo.get("web1")
        assert raw.profile == "aws"
        assert raw.port == 22
        assert raw.key_filename is None

    def test_profile_update_propagates(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", port=2222))
        _add_host(repo, profile="aws")
        assert service.resolve_host("web1").port == 2222
        profile = repo.get_profile("aws")
        profile.port = 2200
        repo.save_profile(profile)
        assert service.resolve_host("web1").port == 2200

    def test_get_host_returns_effective_view(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", key_filename="/k.pem"))
        _add_host(repo, profile="aws")
        assert service.get_host("web1").key_filename == "/k.pem"

    def test_list_hosts_filter_by_profile_tag(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", tags=["cloud"]))
        _add_host(repo, name="a", profile="aws")
        _add_host(repo, name="b", tags=["local"])
        assert [h.name for h in service.list_hosts(tag="cloud")] == ["a"]
        assert [h.name for h in service.list_hosts(tag="local")] == ["b"]

    def test_list_tags_includes_profile_tags(self, setup):
        repo, service = setup
        repo.save_profile(HostProfile(name="aws", tags=["cloud"]))
        _add_host(repo, tags=["web"])
        assert service.list_tags() == ["cloud", "web"]


class TestProfileErrors:
    def test_unknown_profile_raises_config_error(self, setup):
        repo, service = setup
        _add_host(repo, profile="ghost")
        with pytest.raises(ConfigError, match="unknown profile 'ghost'"):
            service.resolve_host("web1")

    def test_repository_without_profile_store(self):
        class BareRepo(HostRepository):
            def __init__(self, host: Host) -> None:
                self._host = host

            def save(self, host: Host) -> None: ...
            def get(self, name: str) -> Host:
                return self._host

            def delete(self, name: str) -> None: ...
            def list(self, tag: Optional[str] = None) -> list[Host]:
                return [self._host]

            def list_tags(self) -> list[str]:
                return []

            def contains(self, name: str) -> bool:
                return True

            def count(self) -> int:
                return 1

            def flush(self) -> None: ...

        service = HostService(repository=BareRepo(Host(name="web1", hostname="1", username="u", profile="aws")))
        with pytest.raises(ConfigError, match="does not support profiles"):
            service.resolve_host("web1")

    def test_batch_executor_maps_profile_error_to_host_result(self, setup):
        repo, service = setup
        _add_host(repo, profile="ghost")
        executor = BatchExecutor(host_service=service, max_concurrency=1)
        result = executor.execute(["web1"], "uptime")
        assert result.failed == 1
        assert "profile resolution failed" in (result.results["web1"].error or "")
        assert "unknown profile" in (result.results["web1"].error or "")
