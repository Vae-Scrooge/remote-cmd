"""SqliteHostRepository 主机存储测试"""

from remote_cmd.core.host import Host
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository


class TestSqliteHostRepository:
    """SqliteHostRepository 集成测试"""

    # --- CRUD ---

    def test_save_and_get(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        host = Host(name="srv1", hostname="10.0.0.1", username="admin", port=22)
        repo.save(host)

        retrieved = repo.get("srv1")
        assert retrieved.name == "srv1"
        assert retrieved.hostname == "10.0.0.1"
        assert retrieved.username == "admin"
        assert retrieved.port == 22

    def test_get_not_found(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        try:
            repo.get("ghost")
            raise AssertionError("应抛出 KeyError")
        except KeyError:
            pass

    def test_save_duplicate(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        host1 = Host(name="srv1", hostname="10.0.0.1", username="admin")
        host2 = Host(name="srv1", hostname="10.0.0.2", username="root")

        repo.save(host1)
        repo.save(host2)

        retrieved = repo.get("srv1")
        assert retrieved.hostname == "10.0.0.2"
        assert retrieved.username == "root"

    def test_delete(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        repo.delete("srv1")
        assert repo.contains("srv1") is False

    def test_delete_not_found(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        try:
            repo.delete("ghost")
            raise AssertionError("应抛出 KeyError")
        except KeyError:
            pass

    def test_contains(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        assert repo.contains("srv1") is True
        assert repo.contains("ghost") is False

    def test_count(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        assert repo.count() == 0
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        repo.save(Host(name="srv2", hostname="10.0.0.2", username="admin"))
        assert repo.count() == 2

    # --- List ---

    def test_list(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        hosts = [
            Host(name="srv1", hostname="10.0.0.1", username="admin"),
            Host(name="srv2", hostname="10.0.0.2", username="root"),
        ]
        for h in hosts:
            repo.save(h)

        result = repo.list()
        assert len(result) == 2
        assert {h.name for h in result} == {"srv1", "srv2"}

    def test_list_empty(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        assert repo.list() == []

    # --- Pagination ---

    def test_list_paginated(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        for i in range(10):
            repo.save(Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin"))

        page1, total1 = repo.list_paginated(offset=0, limit=3)
        assert len(page1) == 3
        assert total1 == 10

        page2, total2 = repo.list_paginated(offset=3, limit=3)
        assert len(page2) == 3
        assert total2 == 10
        names1 = {h.name for h in page1}
        names2 = {h.name for h in page2}
        assert names1.isdisjoint(names2)

    def test_list_paginated_defaults(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        for i in range(5):
            repo.save(Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="admin"))
        hosts, total = repo.list_paginated()
        assert len(hosts) == 5
        assert total == 5

    # --- Tags ---

    def test_list_tags(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web1", hostname="10.0.0.1", username="admin", tags=["web", "prod"]))
        repo.save(Host(name="db1", hostname="10.0.0.2", username="admin", tags=["db", "prod"]))

        tags = repo.list_tags()
        assert sorted(tags) == sorted(["web", "prod", "db"])

    def test_list_tags_empty(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        assert repo.list_tags() == []

    def test_list_by_tag(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web1", hostname="10.0.0.1", username="admin", tags=["web"]))
        repo.save(Host(name="db1", hostname="10.0.0.2", username="admin", tags=["db"]))

        web_hosts = repo.list(tag="web")
        assert len(web_hosts) == 1
        assert web_hosts[0].name == "web1"

    def test_list_by_tag_nonexistent(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web1", hostname="10.0.0.1", username="admin", tags=["web"]))
        result = repo.list(tag="nonexistent")
        assert result == []

    # --- Search ---

    def test_search_by_name(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web-server-01", hostname="10.0.0.1", username="admin"))
        repo.save(Host(name="db-server-01", hostname="10.0.0.2", username="admin"))

        result = repo.search("web")
        assert len(result) == 1
        assert result[0].name == "web-server-01"

    def test_search_by_hostname(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="api.example.com", username="admin"))

        result = repo.search("example")
        assert len(result) == 1
        assert result[0].name == "srv1"

    def test_search_empty(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        assert repo.search("nonexistent") == []

    def test_search_empty_query(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        result = repo.search("")
        assert len(result) == 1

    # --- Flush ---

    def test_flush(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        repo.flush()
        assert repo.contains("srv1") is True

    # --- Reopen ---

    def test_reopen_persistence(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        repo = SqliteHostRepository(db_path)
        repo.save(Host(name="persistent", hostname="10.0.0.1", username="admin"))

        repo2 = SqliteHostRepository(db_path)
        retrieved = repo2.get("persistent")
        assert retrieved.hostname == "10.0.0.1"

    # --- All Fields ---

    def test_host_with_all_fields(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        host = Host(
            name="full-srv",
            hostname="full.example.com",
            username="admin",
            port=2222,
            tags=["web", "prod", "us-east"],
            key_filename="/path/to/key",
        )
        repo.save(host)

        retrieved = repo.get("full-srv")
        assert retrieved.port == 2222
        assert retrieved.tags == ["web", "prod", "us-east"]
        assert retrieved.key_filename == "/path/to/key"

    def test_host_with_empty_tags(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="no-tags", hostname="10.0.0.1", username="admin"))
        retrieved = repo.get("no-tags")
        assert retrieved.tags == []

    # --- 迁移 ---

    def test_migrate_from_json(self, tmp_path, temp_db_path):
        """测试：从 JSON 文件迁移到 SQLite"""
        import json

        json_path = tmp_path / "hosts.json"
        hosts_data = {
            "version": 2,
            "hosts": {
                "srv1": {"name": "srv1", "hostname": "10.0.0.1", "username": "admin", "port": 22},
                "srv2": {"name": "srv2", "hostname": "10.0.0.2", "username": "root", "port": 2222},
            },
        }
        with open(json_path, "w") as f:
            json.dump(hosts_data, f)

        repo = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo.count() == 2
        assert repo.get("srv1").hostname == "10.0.0.1"

    def test_migrate_from_json_nonempty_skips(self, tmp_path, temp_db_path):
        """测试：数据库非空时跳过迁移"""
        import json

        json_path = tmp_path / "hosts.json"
        data = {"version": 2, "hosts": {"x": {"name": "x", "hostname": "1", "username": "u"}}}
        with open(json_path, "w") as f:
            json.dump(data, f)

        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="existing", hostname="1", username="u"))
        repo2 = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo2.count() == 1
        assert repo2.contains("existing")

    def test_migrate_from_json_missing_file(self, tmp_path, temp_db_path):
        """测试：JSON 文件缺失时静默跳过"""
        repo = SqliteHostRepository(temp_db_path, migrate_from=str(tmp_path / "nope.json"))
        assert repo.count() == 0

    def test_migrate_from_json_invalid_data(self, tmp_path, temp_db_path):
        """测试：JSON 格式无效时静默跳过"""
        json_path = tmp_path / "hosts.json"
        json_path.write_text("{bad json}", encoding="utf-8")
        repo = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo.count() == 0

    def test_migrate_from_json_v1_format(self, tmp_path, temp_db_path):
        """测试：v1 格式兼容"""
        import json

        json_path = tmp_path / "hosts.json"
        with open(json_path, "w") as f:
            json.dump({"srv1": {"name": "srv1", "hostname": "10.0.0.1", "username": "admin"}}, f)

        repo = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo.count() == 1

    def test_migrate_from_json_bad_dict_format(self, tmp_path, temp_db_path):
        """测试：无法识别的 JSON 格式时静默跳过"""
        import json

        json_path = tmp_path / "hosts.json"
        with open(json_path, "w") as f:
            json.dump({"version": 2, "hosts": ["not", "a", "dict"]}, f)

        repo = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo.count() == 0

    # --- 边缘路径 ---

    def test_list_tags_corrupted_json(self, temp_db_path):
        """测试：标签 JSON 损坏时静默跳过"""
        import sqlite3

        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="good", hostname="1", username="u", tags=["web"]))
        # 直写坏数据
        conn = sqlite3.connect(temp_db_path)
        conn.execute("UPDATE hosts SET tags=? WHERE name=?", ("{bad json}", "good"))
        conn.commit()
        conn.close()

        repo = SqliteHostRepository(temp_db_path)
        tags = repo.list_tags()
        # 坏 JSON 被跳过，不抛异常
        assert isinstance(tags, list)

    def test_list_paginated_with_tag(self, temp_db_path):
        """测试：按标签分页"""
        repo = SqliteHostRepository(temp_db_path)
        for i in range(15):
            repo.save(Host(name=f"web{i}", hostname=f"10.0.0.{i}", username="admin", tags=["web"]))
        for i in range(5):
            repo.save(Host(name=f"db{i}", hostname=f"10.0.0.{i}", username="admin", tags=["db"]))

        page, total = repo.list_paginated(tag="web", offset=0, limit=5)
        assert len(page) == 5
        assert total == 15
        for h in page:
            assert "web" in h.tags

    def test_list_paginated_with_tag_no_match(self, temp_db_path):
        """测试：按标签分页无匹配"""
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv", hostname="1", username="u", tags=["web"]))
        page, total = repo.list_paginated(tag="nonexistent", offset=0, limit=10)
        assert page == []
        assert total == 0

    def test_list_with_tag_handles_corrupted_tags(self, temp_db_path):
        """测试：损坏的标签 JSON 在 list 时被跳过"""
        import sqlite3

        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="good", hostname="1", username="u", tags=["ok"]))
        conn = sqlite3.connect(temp_db_path)
        conn.execute("INSERT INTO hosts (name, hostname, username, tags) VALUES (?, ?, ?, ?)",
                     ("bad", "2", "u", "{not json}"))
        conn.commit()
        conn.close()

        repo2 = SqliteHostRepository(temp_db_path)
        result = repo2.list(tag="ok")
        assert len(result) >= 1

    def test_row_to_host_handles_bad_tags(self, temp_db_path):
        """测试：_row_to_host 处理损坏的标签 JSON"""
        import sqlite3

        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="good", hostname="1", username="u", tags=["ok"]))
        conn = sqlite3.connect(temp_db_path)
        conn.execute("INSERT INTO hosts (name, hostname, username, tags) VALUES (?, ?, ?, ?)",
                     ("bad", "2", "u", "{not json}"))
        conn.commit()
        conn.close()

        repo2 = SqliteHostRepository(temp_db_path)
        hosts = repo2.list()
        assert len(hosts) == 2

    def test_migrate_skips_invalid_host(self, tmp_path, temp_db_path):
        """测试：迁移时跳过无效主机"""
        import json

        json_path = tmp_path / "hosts.json"
        hosts_data = {
            "version": 2,
            "hosts": {
                "good": {"name": "good", "hostname": "10.0.0.1", "username": "admin"},
                "bad": {"name": "bad"},  # 缺少 hostname
            },
        }
        with open(json_path, "w") as f:
            json.dump(hosts_data, f)

        repo = SqliteHostRepository(temp_db_path, migrate_from=str(json_path))
        assert repo.count() == 1
        assert repo.contains("good")
        assert not repo.contains("bad")
