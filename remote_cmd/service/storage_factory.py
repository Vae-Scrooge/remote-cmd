"""
存储引擎工厂

根据 hosts 文件路径的扩展名或显式配置的 `storage_backend` 自动选择
可用的 HostRepository 实现：
- ``.json``                -> JsonHostRepository
- ``.db`` / ``.sqlite``    -> SqliteHostRepository

当配置中的 ``storage_backend`` 显式指定时，优先采用显式值，忽略扩展名推断。

用法:
    >>> from remote_cmd.service.storage_factory import build_repository
    >>> repo = build_repository("hosts.json")
    >>> repo = build_repository("hosts.db")
    >>> repo = build_repository("hosts.json", storage_backend="sqlite")
    >>> repo = build_repository("hosts.db", encryption=CredentialEncryption())
"""

from pathlib import Path
from typing import Callable, Optional

from remote_cmd.repository.host_repository import HostRepository
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.repository.sqlite_host_repository import SqliteHostRepository
from remote_cmd.utils.crypto import CredentialEncryption

# 显式 storage_backend 取值 -> 仓库工厂
BACKEND_FACTORIES: dict[str, Callable[[str, Optional[CredentialEncryption]], HostRepository]] = {
    "json": lambda path, encryption: JsonHostRepository(
        filepath=path, auto_load=True, encryption=encryption
    ),
    "sqlite": lambda path, encryption: SqliteHostRepository(db_path=path, encryption=encryption),
    "sqlite3": lambda path, encryption: SqliteHostRepository(db_path=path, encryption=encryption),
}

# 扩展名 -> 仓库工厂
EXTENSION_FACTORIES: dict[str, Callable[[str, Optional[CredentialEncryption]], HostRepository]] = {
    ".json": lambda path, encryption: JsonHostRepository(
        filepath=path, auto_load=True, encryption=encryption
    ),
    ".db": lambda path, encryption: SqliteHostRepository(db_path=path, encryption=encryption),
    ".sqlite": lambda path, encryption: SqliteHostRepository(db_path=path, encryption=encryption),
}


def resolve_storage_backend(filepath: str, storage_backend: Optional[str] = None) -> str:
    """
    解析存储后端名称。

    显式 ``storage_backend`` 优先；否则根据文件扩展名推断。

    Args:
        filepath: hosts 文件路径
        storage_backend: 显式指定的存储后端（可选）

    Returns:
        str: 解析出的存储后端名称（"json" 或 "sqlite"）

    Raises:
        ValueError: 无法推断存储后端（未知扩展名且未显式指定）
    """
    if storage_backend:
        normalized = storage_backend.strip().lower()
        if normalized in BACKEND_FACTORIES:
            # 归一化别名（如 sqlite3 -> sqlite）
            return "sqlite" if normalized in ("sqlite", "sqlite3") else "json"
        raise ValueError(
            f"unsupported storage backend: {storage_backend!r} "
            f"(expected one of: {', '.join(sorted(BACKEND_FACTORIES))})"
        )

    suffix = Path(filepath).suffix.lower()
    if suffix in EXTENSION_FACTORIES:
        return "sqlite" if suffix in (".db", ".sqlite") else "json"
    raise ValueError(
        f"cannot infer storage backend from file extension: {suffix!r}; "
        f"supported extensions: {', '.join(sorted(EXTENSION_FACTORIES))}, "
        "or set 'storage_backend' explicitly in config"
    )


def build_repository(
    filepath: str,
    storage_backend: Optional[str] = None,
    encryption: Optional[CredentialEncryption] = None,
) -> HostRepository:
    """
    根据扩展名或显式存储后端构建 HostRepository。

    Args:
        filepath: hosts 文件路径
        storage_backend: 显式指定的存储后端（可选，优先于扩展名推断）
        encryption: 凭据加密器（可选）。传入后仓库在写入时自动加密密码、
            读取时自动解密，作为防御深度（即使调用方绕过 HostService
            直接 save() 明文密码，落盘仍是密文）。

    Returns:
        HostRepository: 匹配的仓库实例

    Raises:
        ValueError: 无法推断或指定了不支持的存储后端
    """
    backend = resolve_storage_backend(filepath, storage_backend)
    factory = BACKEND_FACTORIES[backend]
    return factory(filepath, encryption)
