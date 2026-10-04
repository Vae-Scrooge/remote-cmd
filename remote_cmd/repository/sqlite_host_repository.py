"""
SQLite 主机仓库实现

使用 SQLite 数据库存储主机配置，支持：
- 索引优化查询
- 分页查询
- 模糊搜索
- 从 JSON 格式自动迁移
- 线程安全

用法:
    >>> from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
    >>> repo = SqliteHostRepository("hosts.db")
    >>> repo.save(host)
    >>> repo.list(tag="production")
"""

import builtins
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
import warnings
import weakref
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Optional, cast

from remote_cmd.core.host import Host
from remote_cmd.core.profile import HostProfile
from remote_cmd.core.recipe import Recipe
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.utils.credential_guard import PasswordGuard, is_plaintext_password
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.exceptions import (
    CredentialError,
    PlaintextCredentialWarning,
    ValidationError,
)

logger = logging.getLogger(__name__)

# SQLite 数据库版本（用于未来迁移；v2.9：hosts 表新增 profile 外键约束）
DB_VERSION = 4

# hosts 建表 SQL 模板（v2.9：profile 外键 ON DELETE RESTRICT；重建迁移复用）
CREATE_HOSTS_TABLE_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    name TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    username TEXT NOT NULL,
    port INTEGER DEFAULT 22,
    password TEXT,
    key_filename TEXT,
    tags TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    profile TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (profile) REFERENCES profiles(name) ON DELETE RESTRICT
);
"""
CREATE_TABLE_SQL = CREATE_HOSTS_TABLE_TEMPLATE.format(table="hosts")

# 索引 SQL
CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_hosts_hostname ON hosts(hostname);",
    "CREATE INDEX IF NOT EXISTS idx_hosts_name ON hosts(name);",
    "CREATE INDEX IF NOT EXISTS idx_host_tags_tag ON host_tags(tag, host_name);",
]

# 可索引的规范化标签映射。hosts.tags 继续保留 JSON 字段，兼容已有
# 文件/查询；此表仅为精确标签筛选提供可迁移、可索引的辅助结构。
CREATE_HOST_TAGS_SQL = """
CREATE TABLE IF NOT EXISTS host_tags (
    host_name TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (host_name, tag),
    FOREIGN KEY (host_name) REFERENCES hosts(name) ON DELETE CASCADE
);
"""

# Recipe 表（v2.9；command 模板 + variables JSON；无凭据字段）
CREATE_RECIPES_SQL = """
CREATE TABLE IF NOT EXISTS recipes (
    name TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    variables TEXT DEFAULT '{}',
    description TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 元数据表（用于版本管理）
CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Profile 表（v2.8；无凭据字段）
CREATE_PROFILES_SQL = """
CREATE TABLE IF NOT EXISTS profiles (
    name TEXT PRIMARY KEY,
    username TEXT,
    port INTEGER,
    key_filename TEXT,
    tags TEXT DEFAULT '[]',
    description TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 写锁等待上限（毫秒）：多进程并发写同一数据库时，等待对方短事务提交/回滚
DEFAULT_BUSY_TIMEOUT_MS = 5000

# 显式 checkpoint 允许的模式白名单（防 SQL 注入；SQLite wal_checkpoint 模式）
CHECKPOINT_MODES = frozenset({"PASSIVE", "FULL", "RESTART", "TRUNCATE"})


class _ProcessAwareLock:
    """A repository lock which fails fast if inherited across ``fork()``."""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._lock = threading.Lock()

    def __enter__(self) -> "_ProcessAwareLock":
        if os.getpid() != self._pid:
            raise RuntimeError(
                "SqliteHostRepository cannot be used after fork; construct it in the child process"
            )
        self._lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self._lock.release()


class _ThreadConnection:
    """One SQLite connection owned by a thread-local repository slot."""

    __slots__ = ("connection", "pid", "__weakref__")

    def __init__(self, connection: sqlite3.Connection, pid: int) -> None:
        self.connection: Optional[sqlite3.Connection] = connection
        self.pid = pid

    def close(self) -> None:
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()

    def __del__(self) -> None:
        # Thread-local state is normally finalized by its owning thread. This
        # fallback also closes cached handles when a short-lived Thread object
        # is reclaimed before repository.close() is called.
        with contextlib.suppress(Exception):
            self.close()


class SqliteHostRepository(HostRepository):
    """
    SQLite 主机仓库

    Args:
        db_path: SQLite 数据库文件路径
        migrate_from: JSON 文件路径，用于自动迁移（仅首次使用）
        auto_create: 是否自动创建表和数据库，默认 True
        encryption: 可选的凭据加密器（设置后 save() 自动加密 password）
        busy_timeout_ms: 写锁等待上限（毫秒），默认 5000
        allow_plaintext_credentials: 明文密码持久化策略（v2.5；默认 None
            为兼容模式）。三态：
            - None：允许，但 save() 将要持久化明文密码时发出
              PlaintextCredentialWarning（兼容 v2.4 及更早）
            - True：显式允许明文落盘（不再告警）
            - False：save() 遇到明文密码时抛出 CredentialError
            v3.0 起默认值计划切换为 False。

    注意: 密码的加密依赖传入 encryption。若直接以明文密码调用 save()
    且未提供 encryption，明文会被持久化到数据库。请勿绕过 HostService。

    并发：WAL + busy_timeout 处理多进程并发写，适合作为多写入者后端
    （与 JsonHostRepository 的 single-writer 语义不同）。

    连接生命周期：每个调用线程缓存一个独立连接；使用 ``close()`` 或
    context manager 确定性释放。一个 repository instance 不可跨 fork
    复用，应在子进程内新建。
    """

    def __init__(
        self,
        db_path: str,
        migrate_from: Optional[str] = None,
        auto_create: bool = True,
        encryption: Optional[CredentialEncryption] = None,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        allow_plaintext_credentials: Optional[bool] = None,
    ) -> None:
        self._db_path = db_path
        self._lock = _ProcessAwareLock()
        self._pid = os.getpid()
        self._connection_lock = threading.Lock()
        self._connection_local = threading.local()
        self._connections: weakref.WeakKeyDictionary[
            threading.Thread, weakref.ReferenceType[_ThreadConnection]
        ] = weakref.WeakKeyDictionary()
        self._closed = False
        self._encryption = encryption
        self._guard = PasswordGuard(encryption)
        self._busy_timeout_ms = busy_timeout_ms
        self._allow_plaintext = allow_plaintext_credentials
        self._wal_ready = False

        if auto_create:
            self._init_db()

        if migrate_from:
            self._maybe_migrate_from_json(migrate_from)

    # ========================================================================
    # 数据库初始化
    # ========================================================================

    @staticmethod
    def _parse_tag_list(raw_tags: Optional[str]) -> list[str]:
        """解析持久化标签，只保留有效字符串值。"""
        try:
            parsed = json.loads(raw_tags or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [tag for tag in parsed if isinstance(tag, str)]

    @staticmethod
    def _replace_host_tags(conn: sqlite3.Connection, host_name: str, tags: object) -> None:
        """在当前事务中同步 host_tags 派生索引。"""
        conn.execute("DELETE FROM host_tags WHERE host_name = ?", (host_name,))
        if not isinstance(tags, (list, tuple)):
            return
        seen: set[str] = set()
        for tag in tags:
            if isinstance(tag, str) and tag not in seen:
                conn.execute(
                    "INSERT INTO host_tags (host_name, tag) VALUES (?, ?)",
                    (host_name, tag),
                )
                seen.add(tag)

    def _init_db(self) -> None:
        """初始化数据库：创建表和索引"""
        with self._txn(write=True) as conn:
            # profiles 必须先于 hosts 建表（hosts 的 FK 引用 profiles）
            conn.execute(CREATE_META_SQL)
            conn.execute(CREATE_PROFILES_SQL)
            conn.execute(CREATE_RECIPES_SQL)
            conn.execute(CREATE_TABLE_SQL)
            self._ensure_hosts_profile_column(conn)
            host_tags_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='host_tags'"
            ).fetchone() is not None
            conn.execute(CREATE_HOST_TAGS_SQL)
            conn.execute("DROP INDEX IF EXISTS idx_hosts_tags;")
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
            if not host_tags_exists:
                rows = conn.execute("SELECT name, tags FROM hosts").fetchall()
                for row in rows:
                    self._replace_host_tags(conn, row["name"], self._parse_tag_list(row["tags"]))
            # 设置数据库版本
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("db_version", str(DB_VERSION)),
            )
            conn.commit()
        # FK 迁移必须在事务外：PRAGMA foreign_keys 在事务内是 no-op
        self._ensure_hosts_profile_fk()
        logger.debug(f"SQLite database initialized: {self._db_path}")

    def _ensure_hosts_profile_column(self, conn: sqlite3.Connection) -> None:
        """自动迁移：为 v2.8.0 及更早创建的 hosts 表补充 profile 列。

        v2.8.0 的 SQLite 后端在 save()/行映射中遗漏了 ``Host.profile``，
        导致 profile 引用被静默丢弃（v2.8.1 修复）。旧库在此通过
        ``ALTER TABLE ... ADD COLUMN`` 原地升级，已有数据保留。
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(hosts);").fetchall()}
        if "profile" not in columns:
            conn.execute("ALTER TABLE hosts ADD COLUMN profile TEXT;")
            logger.info("migrated hosts table: added 'profile' column")

    def _ensure_hosts_profile_fk(self) -> None:
        """自动迁移：为 hosts.profile 添加 ``REFERENCES profiles(name)
        ON DELETE RESTRICT`` 外键（v2.9）。

        SQLite 不支持对已有表 ADD CONSTRAINT，需按官方推荐的
        "新表 → 复制 → 删旧表 → 重命名" 流程重建。迁移在事务外先关闭
        ``foreign_keys``（该 PRAGMA 在事务内为 no-op），并在重建后恢复。
        """
        conn = self._get_conn()
        fks = conn.execute("PRAGMA foreign_key_list(hosts);").fetchall()
        if any(row["table"] == "profiles" and row["from"] == "profile" for row in fks):
            return  # 已是 v3 schema
        logger.info("migrating hosts table: adding profile foreign key")
        conn.execute("PRAGMA foreign_keys=OFF;")
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("DROP TABLE IF EXISTS hosts_new;")
            conn.execute(CREATE_HOSTS_TABLE_TEMPLATE.format(table="hosts_new"))
            conn.execute(
                """
                INSERT INTO hosts_new (name, hostname, username, port, password,
                                       key_filename, tags, description, profile,
                                       created_at, updated_at)
                SELECT name, hostname, username, port, password,
                       key_filename, tags, description, profile,
                       created_at, updated_at
                FROM hosts;
                """
            )
            conn.execute("DROP TABLE hosts;")
            conn.execute("ALTER TABLE hosts_new RENAME TO hosts;")
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
            conn.execute("COMMIT;")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK;")
            raise
        finally:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA foreign_keys=ON;")

    def _check_process(self) -> None:
        """Reject inherited repository instances; construct one in each child."""
        if os.getpid() != self._pid:
            raise RuntimeError(
                "SqliteHostRepository cannot be used after fork; construct it in the child process"
            )

    def _get_conn(self) -> sqlite3.Connection:
        """Return the calling thread's cached SQLite connection.

        Each thread owns its own connection; repository methods still serialize
        operations with ``self._lock``. ``check_same_thread=False`` is used only
        so ``close()`` can release all cached handles under that same lock.

        PRAGMA order remains connection-local: install ``busy_timeout`` before
        the once-per-repository WAL transition, then enable foreign keys on
        every thread-local connection.
        """
        self._check_process()
        if self._closed:
            raise RuntimeError("SqliteHostRepository is closed")

        state = getattr(self._connection_local, "state", None)
        if state is not None and state.pid == self._pid and state.connection is not None:
            return cast(sqlite3.Connection, state.connection)

        with self._connection_lock:
            if self._closed:
                raise RuntimeError("SqliteHostRepository is closed")
            state = getattr(self._connection_local, "state", None)
            if state is not None and state.pid == self._pid and state.connection is not None:
                return cast(sqlite3.Connection, state.connection)

            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms};")
                if not self._wal_ready:
                    self._enable_wal(conn)
                    self._wal_ready = True
                conn.execute("PRAGMA foreign_keys=ON;")
            except BaseException:
                conn.close()
                raise

            state = _ThreadConnection(conn, self._pid)
            self._connection_local.state = state
            self._connections[threading.current_thread()] = weakref.ref(state)
            return conn

    def _enable_wal(self, conn: sqlite3.Connection) -> None:
        """启用 WAL；对 journal_mode 切换的 SQLITE_BUSY 做有界重试。

        数据库已是 WAL 时该 PRAGMA 只读取模式、不取排他锁，直接返回；
        仅首次从其他模式切换到 WAL 的竞态需要重试。
        """
        deadline = time.monotonic() + self._busy_timeout_ms / 1000.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                return
            except sqlite3.OperationalError as e:
                message = str(e).lower()
                if "locked" not in message and "busy" not in message:
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    @contextlib.contextmanager
    def _txn(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """
        事务上下文（连接按线程缓存，生命周期归 repository 所有）。

        - 首次在一个线程使用时打开连接并执行 PRAGMA，后续操作复用；
        - ``write=True`` 时以 ``BEGIN IMMEDIATE`` 预先取得写锁（等待受
          ``busy_timeout`` 约束）；避免 deferred 事务在写升级时因快照过期
          直接返回 SQLITE_BUSY（busy handler 不适用该场景）
        - 退出时由 ``conn.__exit__`` 提交/回滚；句柄由 ``close()``、上下文
          管理器或 originating thread 退出时释放。

        所有读写操作都应通过 ``with self._lock, self._txn() as conn:`` 使用，
        保证 repository API 内不会并发使用 SQLite connection；写操作使用
        ``self._txn(write=True)``。
        """
        conn = self._get_conn()
        if write:
            conn.execute("BEGIN IMMEDIATE")
        with conn:  # 事务：commit 或 rollback
            yield conn

    def close(self) -> None:
        """Close every thread-local handle owned by this repository.

        API operations are serialized by ``self._lock``, so no connection is
        in use while handles are closed. The weak thread registry also lets a
        connection be reclaimed when its owning short-lived thread exits.
        """
        self._check_process()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            with self._connection_lock:
                states = [
                    state
                    for state_ref in self._connections.values()
                    if (state := state_ref()) is not None
                ]
                self._connections.clear()
            for state in states:
                try:
                    state.close()
                except sqlite3.Error:
                    logger.warning("error closing SQLite connection", exc_info=True)

    def __enter__(self) -> "SqliteHostRepository":
        self._check_process()
        if self._closed:
            raise RuntimeError("SqliteHostRepository is closed")
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def __del__(self) -> None:
        # Best-effort backward compat: per-operation close model never
        # required callers to close(). Suppress all errors: interpreter
        # shutdown may have torn down locks/logging already.
        with contextlib.suppress(Exception):
            self.close()

    # ========================================================================
    # JSON 迁移
    # ========================================================================

    def _maybe_migrate_from_json(self, json_path: str) -> None:
        """原子迁移 JSON 中的 hosts、profiles、recipes 到空数据库。"""
        with self._lock, self._txn() as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()["cnt"]
                for table in ("hosts", "profiles", "recipes")
            }
            if any(counts.values()):
                logger.info("database not empty, skipping JSON migration")
                return

        path = Path(json_path)
        if not path.exists():
            logger.info(f"JSON file not found, skipping migration: {json_path}")
            return

        # JsonHostRepository understands both the legacy v1 host-only shape and
        # the versioned format. Supplying the same encryption object preserves
        # encrypted passwords as plaintext-in-memory → encrypted-in-SQLite.
        try:
            source = JsonHostRepository(str(path), encryption=self._encryption)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON migration failed: {e}")
            return
        if source._load_error:
            logger.warning("JSON migration skipped because source store is malformed: %s", path)
            return

        hosts = source.list()
        profiles = source.list_profiles()
        recipes = source.list_recipes()
        with self._lock, self._txn(write=True) as conn:
            # Re-check inside BEGIN IMMEDIATE so two processes cannot both start
            # a migration after observing an empty database.
            counts = {
                table: conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()["cnt"]
                for table in ("hosts", "profiles", "recipes")
            }
            if any(counts.values()):
                logger.info("database not empty, skipping JSON migration")
                return

            for profile in profiles:
                conn.execute(
                    """
                    INSERT INTO profiles (name, username, port, key_filename, tags, description)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.name,
                        profile.username,
                        profile.port,
                        profile.key_filename,
                        json.dumps(profile.tags, ensure_ascii=False),
                        profile.description,
                    ),
                )

            for recipe in recipes:
                variables = {
                    name: variable.to_dict() for name, variable in recipe.variables.items()
                }
                conn.execute(
                    """
                    INSERT INTO recipes (name, command, variables, description, tags)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        recipe.name,
                        recipe.command,
                        json.dumps(variables, ensure_ascii=False),
                        recipe.description,
                        json.dumps(recipe.tags, ensure_ascii=False),
                    ),
                )

            for host in hosts:
                if not self._guard.enabled:
                    self._enforce_plaintext_policy(host.name, host.password)
                password = self._guard.encrypt(host.password)
                tags = list(host.tags or [])
                conn.execute(
                    """
                    INSERT INTO hosts (name, hostname, username, port, password,
                                       key_filename, tags, description, profile)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        host.name,
                        host.hostname,
                        host.username,
                        host.port,
                        password,
                        host.key_filename,
                        json.dumps(tags, ensure_ascii=False),
                        host.description,
                        host.profile,
                    ),
                )
                self._replace_host_tags(conn, host.name, tags)

            conn.commit()

        logger.info(
            "migrated JSON store to SQLite: %s hosts, %s profiles, %s recipes",
            len(hosts),
            len(profiles),
            len(recipes),
        )

    # ========================================================================
    # Repository 接口实现
    # ========================================================================

    def save(self, host: Host) -> None:
        """保存或更新主机

        Raises:
            CredentialError: allow_plaintext_credentials=False 且密码为明文
        """
        if not self._guard.enabled:
            self._enforce_plaintext_policy(host.name, host.password)
        with self._lock, self._txn(write=True) as conn:
            tags_json = json.dumps(host.tags or [], ensure_ascii=False)
            # 配置了加密器时，明文密码先加密再落库
            password = self._guard.encrypt(host.password)
            try:
                conn.execute(
                    """
                        INSERT INTO hosts (name, hostname, username, port, password,
                                           key_filename, tags, description, profile,
                                           updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(name) DO UPDATE SET
                            hostname = excluded.hostname,
                            username = excluded.username,
                            port = excluded.port,
                            password = excluded.password,
                            key_filename = excluded.key_filename,
                            tags = excluded.tags,
                            description = excluded.description,
                            profile = excluded.profile,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                    (
                        host.name,
                        host.hostname,
                        host.username,
                        host.port,
                        password,
                        host.key_filename,
                        tags_json,
                        host.description,
                        host.profile,
                    ),
                )
                self._replace_host_tags(conn, host.name, host.tags)
            except sqlite3.IntegrityError as e:
                # v2.9：FK ON DELETE RESTRICT 同时要求引用的 profile 存在。
                # SQLite 在写入时快速失败（JSON 后端仍在解析时报 ConfigError）。
                raise ValueError(
                    f"host '{host.name}' references unknown profile {host.profile!r}"
                ) from e
            conn.commit()

    def get(self, name: str) -> Host:
        """按名称获取主机"""
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT * FROM hosts WHERE name = ?", (name,)).fetchone()

        if row is None:
            raise KeyError(f"Host '{name}' not found")

        return self._row_to_host(row)

    def _enforce_plaintext_policy(self, name: str, password: Optional[str]) -> None:
        """执行明文密码持久化策略（未配置加密器时）。"""
        if self._allow_plaintext is True:
            return
        if not is_plaintext_password(password):
            return
        if self._allow_plaintext is False:
            raise CredentialError(
                f"refusing to persist plaintext credential for host '{name}'. "
                "Pass encryption=... to encrypt at rest, or set "
                "allow_plaintext_credentials=True to explicitly opt in."
            )
        warnings.warn(
            f"Plaintext credential will be persisted for host '{name}'. "
            "Pass encryption=... to encrypt at rest, or "
            "allow_plaintext_credentials=True to suppress this warning "
            "(v3.0 will reject plaintext persistence by default).",
            PlaintextCredentialWarning,
            stacklevel=4,
        )

    def delete(self, name: str) -> None:
        """按名称删除主机"""
        with self._lock, self._txn(write=True) as conn:
            cursor = conn.execute("DELETE FROM hosts WHERE name = ?", (name,))
            conn.commit()

        if cursor.rowcount == 0:
            raise KeyError(f"Host '{name}' not found")

    def list(self, tag: Optional[str] = None) -> list[Host]:
        """列出主机，可选按标签筛选"""
        with self._lock, self._txn() as conn:
            if tag:
                rows = conn.execute(
                    """
                    SELECT hosts.* FROM hosts
                    JOIN host_tags ON host_tags.host_name = hosts.name
                    WHERE host_tags.tag = ?
                    ORDER BY hosts.name
                    """,
                    (tag,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM hosts ORDER BY name").fetchall()

        return [self._row_to_host(row) for row in rows]

    def list_tags(self) -> builtins.list[str]:
        """列出所有标签"""
        with self._lock, self._txn() as conn:
            rows = conn.execute("SELECT DISTINCT tag FROM host_tags ORDER BY tag").fetchall()
        return [row["tag"] for row in rows]

    def contains(self, name: str) -> bool:
        """检查主机是否存在"""
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT 1 FROM hosts WHERE name = ?", (name,)).fetchone()

        return row is not None

    def count(self) -> int:
        """返回主机数量"""
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM hosts").fetchone()

        return row["cnt"] if row else 0

    # ========================================================================
    # ProfileStore 能力（v2.8）
    # ========================================================================

    def save_profile(self, profile: HostProfile) -> None:
        """保存或更新 Profile（即时落库；无凭据字段）。"""
        tags_json = json.dumps(list(profile.tags or []), ensure_ascii=False)
        with self._lock, self._txn(write=True) as conn:
            conn.execute(
                """
                    INSERT INTO profiles (name, username, port, key_filename,
                                          tags, description, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        username = excluded.username,
                        port = excluded.port,
                        key_filename = excluded.key_filename,
                        tags = excluded.tags,
                        description = excluded.description,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                (
                    profile.name,
                    profile.username,
                    profile.port,
                    profile.key_filename,
                    tags_json,
                    profile.description,
                ),
            )
            conn.commit()

    def get_profile(self, name: str) -> HostProfile:
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE name = ?", (name,)).fetchone()

        if row is None:
            raise KeyError(f"Profile '{name}' not found")
        return self._row_to_profile(row)

    def delete_profile(self, name: str) -> None:
        with self._lock, self._txn(write=True) as conn:
            try:
                cursor = conn.execute("DELETE FROM profiles WHERE name = ?", (name,))
            except sqlite3.IntegrityError as e:
                # v2.9：FK ON DELETE RESTRICT 阻止删除仍被引用的 profile。
                # 服务层的引用检查是快速路径；本约束是竞态下的最终保障。
                raise ValueError(
                    f"profile '{name}' is still referenced by hosts "
                    "(foreign key ON DELETE RESTRICT)"
                ) from e
            conn.commit()

        if cursor.rowcount == 0:
            raise KeyError(f"Profile '{name}' not found")

    def list_profiles(self) -> builtins.list[HostProfile]:
        with self._lock, self._txn() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY name").fetchall()

        return [self._row_to_profile(row) for row in rows]

    def contains_profile(self, name: str) -> bool:
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT 1 FROM profiles WHERE name = ?", (name,)).fetchone()

        return row is not None

    # ========================================================================
    # RecipeStore 能力（v2.9）
    # ========================================================================

    def save_recipe(self, recipe: Recipe) -> None:
        """保存或更新 Recipe（即时落库）。"""
        variables_json = json.dumps(
            {name: var.to_dict() for name, var in recipe.variables.items()},
            ensure_ascii=False,
        )
        tags_json = json.dumps(list(recipe.tags or []), ensure_ascii=False)
        with self._lock, self._txn(write=True) as conn:
            conn.execute(
                """
                    INSERT INTO recipes (name, command, variables, description,
                                         tags, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        command = excluded.command,
                        variables = excluded.variables,
                        description = excluded.description,
                        tags = excluded.tags,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                (
                    recipe.name,
                    recipe.command,
                    variables_json,
                    recipe.description,
                    tags_json,
                ),
            )
            conn.commit()

    def get_recipe(self, name: str) -> Recipe:
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT * FROM recipes WHERE name = ?", (name,)).fetchone()

        if row is None:
            raise KeyError(f"Recipe '{name}' not found")
        return self._row_to_recipe(row)

    def delete_recipe(self, name: str) -> None:
        with self._lock, self._txn(write=True) as conn:
            cursor = conn.execute("DELETE FROM recipes WHERE name = ?", (name,))
            conn.commit()

        if cursor.rowcount == 0:
            raise KeyError(f"Recipe '{name}' not found")

    def list_recipes(self) -> builtins.list[Recipe]:
        with self._lock, self._txn() as conn:
            rows = conn.execute("SELECT * FROM recipes ORDER BY name").fetchall()

        return [self._row_to_recipe(row) for row in rows]

    def contains_recipe(self, name: str) -> bool:
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT 1 FROM recipes WHERE name = ?", (name,)).fetchone()

        return row is not None

    def _row_to_recipe(self, row: sqlite3.Row) -> Recipe:
        from remote_cmd.core.recipe import RecipeVariable

        variables: dict[str, RecipeVariable] = {}
        try:
            parsed = json.loads(row["variables"] or "{}")
            if isinstance(parsed, dict):
                variables = {
                    name: RecipeVariable.from_dict(var)
                    for name, var in parsed.items()
                    if isinstance(var, dict)
                }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        tags: list[str] = []
        try:
            parsed_tags = json.loads(row["tags"] or "[]")
            if isinstance(parsed_tags, list):
                tags = [t for t in parsed_tags if isinstance(t, str)]
        except (json.JSONDecodeError, TypeError):
            pass

        return Recipe(
            name=row["name"],
            command=row["command"],
            variables=variables,
            description=row["description"] or "",
            tags=tags,
        )

    def _row_to_profile(self, row: sqlite3.Row) -> HostProfile:
        tags: list[str] = []
        try:
            parsed = json.loads(row["tags"] or "[]")
            if isinstance(parsed, list):
                tags = [t for t in parsed if isinstance(t, str)]
        except (json.JSONDecodeError, TypeError):
            pass

        return HostProfile(
            name=row["name"],
            username=row["username"],
            port=row["port"],
            key_filename=row["key_filename"],
            tags=tags,
            description=row["description"] or "",
        )

    def flush(self) -> None:
        """轻量刷新：执行一次 PASSIVE WAL checkpoint（v2.7 P2.3）。

        SQLite 写入即时生效，本方法是与 JsonHostRepository 对齐的接口。

        v2.7 起不再在每次 flush 执行 ``wal_checkpoint(TRUNCATE)``：
        HostService 的 add/update/remove 每次都会调用 flush，TRUNCATE 会
        等待所有读者并在 checkpoint 后截断 WAL 文件，造成写放大与读阻塞
        风险。PASSIVE 不等待、不截断，仅将可安全写回的帧写回数据库；
        WAL 增长由 SQLite 自动 checkpoint（默认 1000 页）控制。
        需要主动压缩 WAL 时请调用 :meth:`checkpoint`。
        """
        with self._lock, self._txn() as conn:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE);")

    def checkpoint(self, mode: str = "TRUNCATE") -> None:
        """执行显式 WAL checkpoint（v2.7 P2.3），用于按需压缩 WAL 文件。

        Args:
            mode: checkpoint 模式（大小写不敏感）：
                - ``PASSIVE``：不等待读者、不截断（与 flush 相同）
                - ``FULL``：等待所有读者完成
                - ``RESTART``：等待读者完成后重启 WAL 日志
                - ``TRUNCATE``（默认）：等待读者完成后截断 WAL 文件

        Raises:
            ValidationError: mode 不在白名单内（防 SQL 注入）
        """
        normalized = mode.strip().upper()
        if normalized not in CHECKPOINT_MODES:
            raise ValidationError(
                f"unsupported checkpoint mode: {mode!r} "
                f"(expected one of: {', '.join(sorted(CHECKPOINT_MODES))})"
            )
        with self._lock, self._txn() as conn:
            conn.execute(f"PRAGMA wal_checkpoint({normalized});")

    # ========================================================================
    # 扩展方法（非 ABC 接口）
    # ========================================================================

    def search(self, query: str) -> builtins.list[Host]:
        """
        模糊搜索主机

        按名称、主机名、用户名、描述进行模糊匹配。

        Args:
            query: 搜索关键词

        Returns:
            List[Host]: 匹配的主机列表
        """
        pattern = f"%{query}%"
        with self._lock, self._txn() as conn:
            rows = conn.execute(
                """
                    SELECT * FROM hosts
                    WHERE name LIKE ?
                       OR hostname LIKE ?
                       OR username LIKE ?
                       OR description LIKE ?
                    ORDER BY name
                    """,
                (pattern, pattern, pattern, pattern),
            ).fetchall()

        return [self._row_to_host(row) for row in rows]

    def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        tag: Optional[str] = None,
    ) -> tuple[builtins.list[Host], int]:
        """
        分页查询主机

        Args:
            offset: 偏移量
            limit: 每页数量
            tag: 可选标签筛选

        Returns:
            Tuple[List[Host], int]: (主机列表, 总数)
        """
        with self._lock, self._txn() as conn:
            if tag:
                count_row = conn.execute(
                    """
                    SELECT COUNT(*) as cnt FROM hosts
                    JOIN host_tags ON host_tags.host_name = hosts.name
                    WHERE host_tags.tag = ?
                    """,
                    (tag,),
                ).fetchone()
                total = count_row["cnt"] if count_row else 0
                rows = conn.execute(
                    """
                    SELECT hosts.* FROM hosts
                    JOIN host_tags ON host_tags.host_name = hosts.name
                    WHERE host_tags.tag = ?
                    ORDER BY hosts.name LIMIT ? OFFSET ?
                    """,
                    (tag, limit, offset),
                ).fetchall()
            else:
                count_row = conn.execute("SELECT COUNT(*) as cnt FROM hosts").fetchone()
                total = count_row["cnt"] if count_row else 0
                rows = conn.execute(
                    "SELECT * FROM hosts ORDER BY name LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()

        hosts = [self._row_to_host(row) for row in rows]
        return hosts, total

    # ========================================================================
    # 内部辅助
    # ========================================================================

    def _row_to_host(self, row: sqlite3.Row) -> Host:
        """
        将 SQLite 行转换为 Host 对象

        Args:
            row: SQLite 行对象

        Returns:
            Host: 主机配置对象
        """
        # 解析 tags JSON，丢弃非字符串损坏值以保持 JSON/SQLite parity。
        tags = self._parse_tag_list(row["tags"])

        # 配置了加密器时解密密码
        password = row["password"]
        if self._guard.is_encrypted(password):
            password = self._guard.decrypt(password)
            if password is None:
                logger.warning("failed to decrypt password for host '%s'", row["name"])

        return Host(
            name=row["name"],
            hostname=row["hostname"],
            username=row["username"],
            port=row["port"],
            password=password,
            key_filename=row["key_filename"],
            tags=tags,
            description=row["description"] or "",
            profile=row["profile"],
        )


__all__ = ["SqliteHostRepository"]
