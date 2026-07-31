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
from typing import Optional

from remote_cmd.core.host import Host
from remote_cmd.repository.host_repository import HostRepository

logger = logging.getLogger(__name__)

# SQLite 数据库版本（用于未来迁移）
DB_VERSION = 1

# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hosts (
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

# 索引 SQL
CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_hosts_tags ON hosts(tags);",
    "CREATE INDEX IF NOT EXISTS idx_hosts_hostname ON hosts(hostname);",
    "CREATE INDEX IF NOT EXISTS idx_hosts_name ON hosts(name);",
]

# 元数据表（用于版本管理）
CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SqliteHostRepository(HostRepository):
    """
    SQLite 主机仓库

    Args:
        db_path: SQLite 数据库文件路径
        migrate_from: JSON 文件路径，用于自动迁移（仅首次使用）
        auto_create: 是否自动创建表和数据库，默认 True
    """

    def __init__(
        self,
        db_path: str,
        migrate_from: Optional[str] = None,
        auto_create: bool = True,
    ):
        self._db_path = db_path
        self._lock = threading.Lock()

        if auto_create:
            self._init_db()

        if migrate_from:
            self._maybe_migrate_from_json(migrate_from)

    # ========================================================================
    # 数据库初始化
    # ========================================================================

    def _init_db(self) -> None:
        """初始化数据库：创建表和索引"""
        with self._txn() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_META_SQL)
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
            # 设置数据库版本
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("db_version", str(DB_VERSION)),
            )
            conn.commit()
        logger.debug(f"SQLite 数据库已初始化: {self._db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（线程安全）"""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextlib.contextmanager
    def _txn(self):
        """
        事务 + 连接生命周期上下文

        包装 ``with conn:`` 与 ``conn.close()`` 为单一上下文：
        - 进入时打开新连接并执行 PRAGMA
        - 退出时先 ``conn.__exit__`` 提交/回滚，再 ``conn.close()`` 释放 fd

        解决 ``with self._get_conn() as conn:`` 不自动 close 导致的 fd 累积泄漏
        （sqlite3.Connection.__exit__ 仅管理事务边界，不释放连接句柄）。

        所有读写操作都应通过 ``with self._lock, self._txn() as conn:`` 使用，
        保证 ``self._lock`` 串行化的同时每次操作后释放 fd。
        """
        conn = self._get_conn()
        try:
            with conn:  # 事务：commit 或 rollback
                yield conn
        finally:
            conn.close()

    # ========================================================================
    # JSON 迁移
    # ========================================================================

    def _maybe_migrate_from_json(self, json_path: str) -> None:
        """
        如果数据库为空且 JSON 文件存在，执行迁移

        Args:
            json_path: JSON 文件路径
        """
        with self._lock, self._txn() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM hosts").fetchone()["cnt"]
            if count > 0:
                logger.info("数据库非空，跳过 JSON 迁移")
                return

        # 尝试加载 JSON 文件
        try:
            from pathlib import Path

            path = Path(json_path)
            if not path.exists():
                logger.info(f"JSON 文件不存在，跳过迁移: {json_path}")
                return

            with open(path, encoding="utf-8") as f:
                raw_data = json.load(f)

            # 解析版本格式
            version = raw_data.get("version", 1)
            hosts_data = raw_data.get("hosts", raw_data if version == 1 else {})

            if not isinstance(hosts_data, dict):
                logger.warning(f"无法识别的 JSON 格式: {json_path}")
                return

            imported = 0
            for name, host_dict in hosts_data.items():
                try:
                    host = Host.from_dict(host_dict)
                    self.save(host)
                    imported += 1
                except (ValueError, TypeError, KeyError) as e:
                    logger.warning(f"跳过无效主机 '{name}': {e}")

            if imported > 0:
                logger.info(f"从 JSON 迁移了 {imported} 台主机到 SQLite")

        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON 迁移失败: {e}")

    # ========================================================================
    # Repository 接口实现
    # ========================================================================

    def save(self, host: Host) -> None:
        """保存或更新主机"""
        with self._lock, self._txn() as conn:
            tags_json = json.dumps(host.tags or [], ensure_ascii=False)
            conn.execute(
                """
                    INSERT INTO hosts (name, hostname, username, port, password,
                                       key_filename, tags, description, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(name) DO UPDATE SET
                        hostname = excluded.hostname,
                        username = excluded.username,
                        port = excluded.port,
                        password = excluded.password,
                        key_filename = excluded.key_filename,
                        tags = excluded.tags,
                        description = excluded.description,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                (
                    host.name,
                    host.hostname,
                    host.username,
                    host.port,
                    host.password,
                    host.key_filename,
                    tags_json,
                    host.description,
                ),
            )
            conn.commit()

    def get(self, name: str) -> Host:
        """按名称获取主机"""
        with self._lock, self._txn() as conn:
            row = conn.execute("SELECT * FROM hosts WHERE name = ?", (name,)).fetchone()

        if row is None:
            raise KeyError(f"主机 '{name}' 不存在")

        return self._row_to_host(row)

    def delete(self, name: str) -> None:
        """按名称删除主机"""
        with self._lock, self._txn() as conn:
            cursor = conn.execute("DELETE FROM hosts WHERE name = ?", (name,))
            conn.commit()

        if cursor.rowcount == 0:
            raise KeyError(f"主机 '{name}' 不存在")

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

    def flush(self) -> None:
        """
        SQLite 写入即时生效，flush 为空操作
        此处仅触发一个检查点以压缩 WAL 日志
        """
        with self._lock, self._txn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

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

    @staticmethod
    def _row_to_host(row: sqlite3.Row) -> Host:
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

        return Host(
            name=row["name"],
            hostname=row["hostname"],
            username=row["username"],
            port=row["port"],
            password=row["password"],
            key_filename=row["key_filename"],
            tags=tags,
            description=row["description"] or "",
        )


__all__ = ["SqliteHostRepository"]
