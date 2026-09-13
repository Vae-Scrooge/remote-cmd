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
import sqlite3
import threading
import time
import warnings
from typing import Optional

from remote_cmd.core.host import Host
from remote_cmd.core.profile import HostProfile
from remote_cmd.core.recipe import Recipe
from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.utils.credential_guard import PasswordGuard, is_plaintext_password
from remote_cmd.utils.crypto import CredentialEncryption
from remote_cmd.utils.exceptions import (
    CredentialError,
    PlaintextCredentialWarning,
    ValidationError,
)

logger = logging.getLogger(__name__)

# SQLite 数据库版本（用于未来迁移；v2.9：hosts 表新增 profile 外键约束）
DB_VERSION = 3

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
    "CREATE INDEX IF NOT EXISTS idx_hosts_tags ON hosts(tags);",
    "CREATE INDEX IF NOT EXISTS idx_hosts_hostname ON hosts(hostname);",
    "CREATE INDEX IF NOT EXISTS idx_hosts_name ON hosts(name);",
]

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
        self._lock = threading.Lock()
        self._encryption = encryption
        self._guard = PasswordGuard(encryption)
        self._busy_timeout_ms = busy_timeout_ms
        self._allow_plaintext = allow_plaintext_credentials

        if auto_create:
            self._init_db()

        if migrate_from:
            self._maybe_migrate_from_json(migrate_from)

    # ========================================================================
    # 数据库初始化
    # ========================================================================

    def _init_db(self) -> None:
        """初始化数据库：创建表和索引"""
        with self._txn(write=True) as conn:
            # profiles 必须先于 hosts 建表（hosts 的 FK 引用 profiles）
            conn.execute(CREATE_META_SQL)
            conn.execute(CREATE_PROFILES_SQL)
            conn.execute(CREATE_RECIPES_SQL)
            conn.execute(CREATE_TABLE_SQL)
            self._ensure_hosts_profile_column(conn)
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
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
        try:
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
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（线程安全）。

        PRAGMA 顺序约定：``busy_timeout`` 必须最先设置——首次并发打开同一
        数据库文件时，``journal_mode=WAL`` 本身需要短暂排他锁，若 busy
        handler 尚未生效会立即抛 ``database is locked``（v2.2 多进程回归）。

        注意：SQLite 的 journal_mode 切换不经过 busy handler，即使设置了
        ``busy_timeout`` 仍会直接返回 SQLITE_BUSY；因此在首次并发切换到
        WAL 时使用有界重试（上限即 busy_timeout）等待其他连接的短事务结束。
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # 多进程/多连接写入争用：等待对方事务结束而不是立即失败
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms};")
        self._enable_wal(conn)
        conn.execute("PRAGMA foreign_keys=ON;")
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
    def _txn(self, write: bool = False):
        """
        事务 + 连接生命周期上下文

        包装 ``with conn:`` 与 ``conn.close()`` 为单一上下文：
        - 进入时打开新连接并执行 PRAGMA
        - ``write=True`` 时以 ``BEGIN IMMEDIATE`` 预先取得写锁（等待受
          ``busy_timeout`` 约束）；避免 deferred 事务在写升级时因快照过期
          直接返回 SQLITE_BUSY（busy handler 不适用该场景）
        - 退出时先 ``conn.__exit__`` 提交/回滚，再 ``conn.close()`` 释放 fd

        解决 ``with self._get_conn() as conn:`` 不自动 close 导致的 fd 累积泄漏
        （sqlite3.Connection.__exit__ 仅管理事务边界，不释放连接句柄）。

        所有读写操作都应通过 ``with self._lock, self._txn() as conn:`` 使用，
        保证 ``self._lock`` 串行化的同时每次操作后释放 fd；写操作使用
        ``self._txn(write=True)``。
        """
        conn = self._get_conn()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            with conn:  # 事务：commit 或 rollback
                yield conn
        finally:
            conn.close()

    # ========================================================================
    # JSON 迁移
    # ========================================================================

    def _maybe_migrate_from_json(self, json_path: str) -> None:
        """
        if database is empty and JSON file exists, run migration

        Args:
            json_path: JSON 文件路径
        """
        with self._lock, self._txn() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM hosts").fetchone()["cnt"]
            if count > 0:
                logger.info("database not empty, skipping JSON migration")
                return

        # 尝试加载 JSON 文件
        try:
            from pathlib import Path

            path = Path(json_path)
            if not path.exists():
                logger.info(f"JSON file not found, skipping migration: {json_path}")
                return

            with open(path, encoding="utf-8") as f:
                raw_data = json.load(f)

            # 解析版本格式
            version = raw_data.get("version", 1)
            hosts_data = raw_data.get("hosts", raw_data if version == 1 else {})

            if not isinstance(hosts_data, dict):
                logger.warning(f"unrecognized JSON format: {json_path}")
                return

            imported = 0
            for name, host_dict in hosts_data.items():
                try:
                    host = Host.from_dict(host_dict)
                    self.save(host)
                    imported += 1
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(f"skipping invalid host '{name}': {e}")

            if imported > 0:
                logger.info(f"migrated {imported} hosts to SQLite")

        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON migration failed: {e}")

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
                # 使用 LIKE 匹配 tags JSON 中的标签
                rows = conn.execute(
                    "SELECT * FROM hosts WHERE tags LIKE ? ORDER BY name",
                    (f'%"{tag}"%',),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM hosts ORDER BY name").fetchall()

        return [self._row_to_host(row) for row in rows]

    def list_tags(self) -> builtins.list[str]:
        """列出所有标签"""
        with self._lock, self._txn() as conn:
            rows = conn.execute("SELECT DISTINCT tags FROM hosts WHERE tags IS NOT NULL").fetchall()

        tags_set: set = set()
        for row in rows:
            try:
                tags = json.loads(row["tags"] or "[]")
                if isinstance(tags, list):
                    tags_set.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass

        return sorted(tags_set)

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
                    "SELECT COUNT(*) as cnt FROM hosts WHERE tags LIKE ?",
                    (f'%"{tag}"%',),
                ).fetchone()
                total = count_row["cnt"] if count_row else 0
                rows = conn.execute(
                    "SELECT * FROM hosts WHERE tags LIKE ? ORDER BY name LIMIT ? OFFSET ?",
                    (f'%"{tag}"%', limit, offset),
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
        # 解析 tags JSON
        tags = None
        try:
            raw_tags = row["tags"]
            if raw_tags:
                parsed = json.loads(raw_tags)
                if isinstance(parsed, list):
                    tags = parsed
        except (json.JSONDecodeError, TypeError):
            pass

        # 配置了加密器时解密密码
        password = row["password"]
        if self._guard.is_encrypted(password):
            password = self._guard.decrypt(password)
            if password is None:
                logger.warning("failed to decrypt password for host '%s'", row["name"])

        # tags 解析失败时为 None，归一化为空列表（与 Host 构造器默认行为一致）
        if tags is None:
            tags = []

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
