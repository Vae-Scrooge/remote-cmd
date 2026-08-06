"""存储引擎自动切换测试

验证根据 hosts 文件扩展名或显式 `storage_backend` 自动选择存储引擎：
- .json 使用 JsonHostRepository
- .db / .sqlite 使用 SqliteHostRepository
- 显式 storage_backend 优先于扩展名推断
- 未知扩展名且无显式配置时抛出 ValueError
"""

import pytest

from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.service.storage_factory import (
    build_repository,
    resolve_storage_backend,
)


class TestResolveStorageBackend:
    """存储后端解析测试"""

    def test_json_extension(self):
        assert resolve_storage_backend("hosts.json") == "json"

    def test_db_extension(self):
        assert resolve_storage_backend("hosts.db") == "sqlite"

    def test_sqlite_extension(self):
        assert resolve_storage_backend("hosts.sqlite") == "sqlite"

    def test_explicit_backend_precedes_extension(self):
        assert resolve_storage_backend("hosts.json", "sqlite") == "sqlite"
        assert resolve_storage_backend("hosts.db", "json") == "json"

    def test_backend_case_insensitive(self):
        assert resolve_storage_backend("hosts.json", "SQLITE") == "sqlite"
        assert resolve_storage_backend("hosts.db", "JSON") == "json"

    def test_sqlite3_alias(self):
        assert resolve_storage_backend("hosts.json", "sqlite3") == "sqlite"

    def test_unknown_extension_raises(self):
        with pytest.raises(ValueError, match="cannot infer storage backend"):
            resolve_storage_backend("hosts.yaml")

    def test_unsupported_explicit_backend_raises(self):
        with pytest.raises(ValueError, match="unsupported storage backend"):
            resolve_storage_backend("hosts.db", "yaml")


class TestBuildRepository:
    """仓库构建测试"""

    def test_json_extension_builds_json(self, tmp_path):
        repo = build_repository(str(tmp_path / "hosts.json"))
        assert isinstance(repo, JsonHostRepository)

    def test_db_extension_builds_sqlite(self, tmp_path):
        repo = build_repository(str(tmp_path / "hosts.db"))
        assert isinstance(repo, SqliteHostRepository)

    def test_sqlite_extension_builds_sqlite(self, tmp_path):
        repo = build_repository(str(tmp_path / "hosts.sqlite"))
        assert isinstance(repo, SqliteHostRepository)

    def test_explicit_json_backend(self, tmp_path):
        repo = build_repository(str(tmp_path / "hosts.db"), "json")
        assert isinstance(repo, JsonHostRepository)

    def test_explicit_sqlite_backend(self, tmp_path):
        repo = build_repository(str(tmp_path / "hosts.json"), "sqlite")
        assert isinstance(repo, SqliteHostRepository)

    def test_unknown_extension_raises(self, tmp_path):
        with pytest.raises(ValueError):
            build_repository(str(tmp_path / "hosts.yaml"))

    def test_default_json_stays_json(self, tmp_path):
        """默认 hosts.json 行为保持为 JSON 存储，不破坏兼容性"""
        repo = build_repository(str(tmp_path / "hosts.json"))
        assert isinstance(repo, JsonHostRepository)
