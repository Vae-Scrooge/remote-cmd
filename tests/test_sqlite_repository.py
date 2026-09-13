"""SqliteHostRepository 主机存储测试"""

import os
import sqlite3
import subprocess
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from remote_cmd.core.host import Host
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.exceptions import CredentialError, PlaintextCredentialWarning

# 多进程写测试的 worker 源码：启动后先写 ready 文件，等待 go 文件存在
# 再开始写入，确保各进程在 go 出现时同时争用数据库（确定性启动同步）。
_MP_WORKER = r"""
import os
import sys
import time

from remote_cmd.core.host import Host
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository

db, rank, n, ready, go = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
with open(ready, "w", encoding="utf-8"):
    pass
while not os.path.exists(go):
    time.sleep(0.005)
repo = SqliteHostRepository(db)
for i in range(n):
    repo.save(Host(name=f"p{rank}-h{i}", hostname=f"10.{rank}.{i}.1", username="u"))
"""


class TestSqliteHostRepository:
    """SqliteHostRepository 集成测试"""

    # --- 加密 ---

    def test_encryption_roundtrip(self, temp_db_path):
        """测试：配置 encryption 后密码加密落库并可解密读取"""
        encryption = CredentialEncryption()
        repo = SqliteHostRepository(temp_db_path, encryption=encryption)
        repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin", password="plain_secret"))

        # 读取返回解密后的明文
        retrieved = repo.get("srv1")
        assert retrieved.password == "plain_secret"

        # 落库内容是加密 token（非明文）
        conn = sqlite3.connect(temp_db_path)
        try:
            row = conn.execute("SELECT password FROM hosts WHERE name = ?", ("srv1",)).fetchone()
            assert row is not None
            assert row[0] != "plain_secret"
            assert encryption.is_encrypted(row[0])
        finally:
            conn.close()

    def test_without_encryption_stores_plaintext(self, temp_db_path):
        """测试：未配置 encryption 时明文直接落库（兼容旧行为），
        并发出 PlaintextCredentialWarning（v2.5 默认策略）。"""
        repo = SqliteHostRepository(temp_db_path)
        with pytest.warns(PlaintextCredentialWarning, match="Plaintext credential"):
            repo.save(
                Host(name="srv1", hostname="10.0.0.1", username="admin", password="plain_secret")
            )

        conn = sqlite3.connect(temp_db_path)
        try:
            row = conn.execute("SELECT password FROM hosts WHERE name = ?", ("srv1",)).fetchone()
            assert row[0] == "plain_secret"
        finally:
            conn.close()

    def test_plaintext_policy_opt_in_silent(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path, allow_plaintext_credentials=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PlaintextCredentialWarning)
            repo.save(
                Host(name="srv1", hostname="10.0.0.1", username="admin", password="plain_secret")
            )
        assert repo.get("srv1").password == "plain_secret"

    def test_plaintext_policy_reject_raises(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path, allow_plaintext_credentials=False)
        with pytest.raises(CredentialError, match="plaintext"):
            repo.save(
                Host(name="srv1", hostname="10.0.0.1", username="admin", password="plain_secret")
            )
        assert repo.count() == 0

    def test_plaintext_policy_no_password_silent(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path, allow_plaintext_credentials=False)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PlaintextCredentialWarning)
            repo.save(Host(name="srv1", hostname="10.0.0.1", username="admin"))
        assert repo.count() == 1

    def test_plaintext_policy_encrypted_token_not_flagged(self, temp_db_path):
        """$encrypted$ token 即使仓库未配置 encryption 也不应被误报。"""
        repo = SqliteHostRepository(temp_db_path, allow_plaintext_credentials=False)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PlaintextCredentialWarning)
            repo.save(
                Host(
                    name="srv1",
                    hostname="10.0.0.1",
                    username="admin",
                    password="$encrypted$gAAAAABfake-token",
                )
            )
        assert repo.count() == 1

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
        conn.execute(
            "INSERT INTO hosts (name, hostname, username, tags) VALUES (?, ?, ?, ?)",
            ("bad", "2", "u", "{not json}"),
        )
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
        conn.execute(
            "INSERT INTO hosts (name, hostname, username, tags) VALUES (?, ?, ?, ?)",
            ("bad", "2", "u", "{not json}"),
        )
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


class TestConnectionLifecycle:
    """P0-B 回归测试：每次操作后连接 fd 应被释放

    历史问题：`with self._get_conn() as conn:` 中 sqlite3.Connection.__exit__
    只提交/回滚不 close，导致每次 save/get/list 都累积一个打开的连接句柄，
    long-running 场景下最终触发 EMFILE。
    """

    def test_no_fd_leak_over_many_operations(self, temp_db_path):
        """循环读写后进程打开的文件描述符数量不应增长"""
        import os

        repo = SqliteHostRepository(temp_db_path)
        host = Host(name="srv", hostname="10.0.0.1", username="admin")

        proc_fd = "/proc/self/fd"
        if not os.path.isdir(proc_fd):
            import pytest

            pytest.skip("需要 Linux /proc 支持")

        # 预热（确保 WAL、缓存等已建立，基线稳定）
        repo.save(host)
        repo.get("srv")
        repo.list()

        fds_before = len(os.listdir(proc_fd))

        for i in range(200):
            repo.save(Host(name=f"srv{i}", hostname="1", username="u"))
            repo.get(f"srv{i}")
            repo.list()
            repo.count()

        fds_after = len(os.listdir(proc_fd))
        assert fds_after <= fds_before + 2, (
            f"数据库连接 fd 泄漏：操作前 {fds_before}，操作后 {fds_after}"
        )


# ============================================================================
# v2.2：并发写加固（WAL + busy_timeout + BEGIN IMMEDIATE）
# ============================================================================


class TestSqliteConcurrentWriters:
    """多进程/多连接并发写入的健壮性回归

    背景：v2.1 及更早版本在多个 remote-cmd 进程同时打开同一 hosts.db 时，
    首次 WAL 切换会直接抛 ``database is locked``（journal_mode 切换不经过
    busy handler），且并发写事务无 busy_timeout，导致进程失败/丢失更新。
    """

    def _spawn_env(self) -> dict:
        repo_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def test_configured_busy_timeout_and_wal(self, temp_db_path):
        """连接级 PRAGMA：busy_timeout 生效且 journal_mode 为 WAL"""
        repo = SqliteHostRepository(temp_db_path, busy_timeout_ms=1234)
        conn = repo._get_conn()
        try:
            assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 1234
            assert str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower() == "wal"
        finally:
            conn.close()


    def test_multiprocess_concurrent_writers_no_lost_updates(self, tmp_path):
        """多个独立进程同时写同一数据库：全部成功且无丢失更新"""
        db_path = str(tmp_path / "multiproc.db")
        n_procs = 4
        n_writes = 20
        ready_files = [str(tmp_path / f"ready-{r}") for r in range(n_procs)]
        go_file = str(tmp_path / "go")

        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _MP_WORKER,
                    db_path,
                    str(rank),
                    str(n_writes),
                    ready_files[rank],
                    go_file,
                ],
                env=self._spawn_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for rank in range(n_procs)
        ]

        try:
            # 等待所有 worker 就绪（全部存活并即将争用数据库）
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not all(os.path.exists(p) for p in ready_files):
                time.sleep(0.01)
            assert all(os.path.exists(p) for p in ready_files), "worker 未全部就绪"

            with open(go_file, "w", encoding="utf-8") as f:
                f.write("go")

            failures = []
            for proc in procs:
                try:
                    _out, err = proc.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _out, err = proc.communicate()
                    failures.append("timeout")
                    continue
                if proc.returncode != 0:
                    failures.append(err.decode(errors="replace"))
            assert not failures, f"并发写进程失败: {failures}"
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

        repo = SqliteHostRepository(db_path)
        assert repo.count() == n_procs * n_writes
        names = {h.name for h in repo.list()}
        expected = {f"p{r}-h{i}" for r in range(n_procs) for i in range(n_writes)}
        # 无丢失更新：每个进程写入的每条记录都能读到
        assert names == expected

    def test_concurrent_threads_separate_instances(self, tmp_path):
        """同一进程内多实例（各自连接）并发写：全部成功且无丢失更新"""
        db_path = str(tmp_path / "threads.db")
        n_threads = 4
        n_writes = 15

        def worker(rank: int) -> None:
            repo = SqliteHostRepository(db_path)
            for i in range(n_writes):
                repo.save(Host(name=f"t{rank}-h{i}", hostname=f"10.{rank}.{i}.1", username="u"))

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            list(pool.map(worker, range(n_threads)))

        repo = SqliteHostRepository(db_path)
        assert repo.count() == n_threads * n_writes
        names = {h.name for h in repo.list()}
        expected = {f"t{r}-h{i}" for r in range(n_threads) for i in range(n_writes)}
        assert names == expected

    def test_schema_version_and_columns_preserved(self, temp_db_path):
        """schema 兼容性：v2.8.1 起 db_version=2，hosts 表含 profile 列且旧列不变"""
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="srv", hostname="10.0.0.1", username="u"))

        conn = sqlite3.connect(temp_db_path)
        try:
            version = conn.execute("SELECT value FROM meta WHERE key = 'db_version'").fetchone()[0]
            assert version == "2"
            cols = {row[1] for row in conn.execute("PRAGMA table_info(hosts)")}
            assert {
                "name",
                "hostname",
                "username",
                "port",
                "password",
                "key_filename",
                "tags",
                "description",
                "profile",
                "created_at",
                "updated_at",
            } <= cols
        finally:
            conn.close()

        # 重新打开：读取行为不变
        repo2 = SqliteHostRepository(temp_db_path)
        assert repo2.get("srv").hostname == "10.0.0.1"


class TestSqliteCheckpointPolicy:
    """v2.7（P2.3）：flush 只做 PASSIVE checkpoint，压缩 WAL 走显式 checkpoint()。"""

    def _wal_size(self, db_path: str) -> int:
        wal = Path(f"{db_path}-wal")
        return wal.stat().st_size if wal.exists() else 0

    def test_flush_is_passive_and_checkpoint_truncates(self, tmp_path):
        db_path = str(tmp_path / "hosts.db")
        repo = SqliteHostRepository(db_path)
        # observer 连接保持打开，避免最后一个连接关闭时 SQLite 自动删除 WAL 文件
        observer = sqlite3.connect(db_path)
        try:
            observer.execute("PRAGMA journal_mode=WAL;")
            for i in range(200):
                repo.save(Host(name=f"srv{i}", hostname=f"10.0.0.{i}", username="u"))

            wal_before = self._wal_size(db_path)
            assert wal_before > 0

            repo.flush()  # PASSIVE：不截断
            assert self._wal_size(db_path) >= wal_before

            repo.checkpoint("TRUNCATE")
            assert self._wal_size(db_path) == 0
        finally:
            observer.close()

    def test_checkpoint_modes_case_insensitive(self, tmp_path):
        repo = SqliteHostRepository(str(tmp_path / "hosts.db"))
        for mode in ("passive", "Full", " restart ", "truncate"):
            repo.checkpoint(mode)  # 不抛错即可

    def test_checkpoint_invalid_mode_raises(self, tmp_path):
        from remote_cmd.utils.exceptions import ValidationError

        repo = SqliteHostRepository(str(tmp_path / "hosts.db"))
        with pytest.raises(ValidationError, match="checkpoint mode"):
            repo.checkpoint("DROP TABLE hosts")


# ============================================================================
# v2.8.1：Host.profile 引用持久化 + 旧库自动迁移
# ============================================================================


class TestSqliteHostProfilePersistence:
    """v2.8.0 曾在 SQLite 后端丢失 Host.profile（save/行映射缺列）。"""

    def test_profile_roundtrip(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web1", hostname="10.0.0.1", username="", profile="aws"))

        again = SqliteHostRepository(temp_db_path)
        assert again.get("web1").profile == "aws"

    def test_profile_survives_upsert(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo.save(Host(name="web1", hostname="10.0.0.1", username="", profile="aws"))
        host = repo.get("web1")
        host.description = "updated"
        repo.save(host)

        assert SqliteHostRepository(temp_db_path).get("web1").profile == "aws"

    def test_profile_persisted_end_to_end_with_service(self, temp_db_path):
        from remote_cmd.core.profile import HostProfile
        from remote_cmd.service.host_service import HostService

        repo = SqliteHostRepository(temp_db_path)
        repo.save_profile(HostProfile(name="aws", username="ec2-user", port=2222))
        repo.save(Host(name="web1", hostname="10.0.0.1", username="", profile="aws"))

        resolved = HostService(repository=repo).resolve_host("web1")
        assert resolved.username == "ec2-user"
        assert resolved.port == 2222

    def test_legacy_database_auto_migration(self, tmp_path):
        """v2.8.0 旧库（hosts 表无 profile 列）打开时自动 ALTER TABLE，数据保留。"""
        db_path = str(tmp_path / "legacy.db")
        legacy_sql = """
        CREATE TABLE hosts (
            name TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            username TEXT NOT NULL,
            port INTEGER DEFAULT 22,
            password TEXT,
            key_filename TEXT,
            tags TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(legacy_sql)
            conn.execute(
                "INSERT INTO hosts (name, hostname, username, port, tags, description) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy1", "10.0.0.9", "root", 22, "[]", "old row"),
            )
            conn.commit()
        finally:
            conn.close()

        repo = SqliteHostRepository(db_path)  # __init__ 触发迁移
        assert repo.get("legacy1").description == "old row"
        assert repo.get("legacy1").profile is None

        columns = {
            row[1] for row in repo._get_conn().execute("PRAGMA table_info(hosts);").fetchall()
        }
        assert "profile" in columns

        # 迁移后可正常写入并读取 profile 引用
        repo.save(Host(name="new1", hostname="10.0.0.10", username="u", profile="aws"))
        assert SqliteHostRepository(db_path).get("new1").profile == "aws"

    def test_migration_is_idempotent(self, temp_db_path):
        repo = SqliteHostRepository(temp_db_path)
        repo._init_db()
        repo._init_db()  # 重复初始化不得报错
        columns = [
            row[1] for row in repo._get_conn().execute("PRAGMA table_info(hosts);").fetchall()
        ]
        assert columns.count("profile") == 1
