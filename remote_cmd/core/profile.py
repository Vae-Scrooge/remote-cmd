"""
主机连接 Profile 模型（v2.8）

Profile 表示一组**连接默认值**（用户名/端口/私钥路径/标签/描述），
供多台主机共享，避免逐台重复配置：

    profile aws: username=ec2-user, key_filename=~/.ssh/aws.pem
    host web-01: profile=aws, hostname=10.0.0.10

安全约定（硬性）：
- Profile **不得包含密码等凭据**。``HostProfile`` 没有 password 字段，
  凭据仍由主机自身 + 凭据提供链（环境变量/keyring/加密文件）解析。
- Profile 是引用式的：主机只存 profile 名，连接解析时合并
  （见 ``HostService.resolve_host``），修改 profile 对引用它的主机生效。

合并语义（defaults → profile → host；v2.8 仅支持扁平的 profile）：
- ``username``: 主机为空时由 profile 提供
- ``port``: 主机为默认值 22 时由 profile 提供（显式非默认端口优先）
- ``key_filename``: 主机为 None 时由 profile 提供
- ``tags``: 主机标签 + profile 标签（并集去重，主机顺序在前）
- ``description``: 主机为空字符串时由 profile 提供

用法:
    >>> from remote_cmd.core.profile import HostProfile
    >>> profile = HostProfile(
    ...     name="aws",
    ...     username="ec2-user",
    ...     key_filename="~/.ssh/aws.pem",
    ...     tags=["cloud"],
    ... )
    >>> profile.to_dict()["name"]
    'aws'
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from remote_cmd.utils.exceptions import ValidationError

#: 合并时视为“未设置”的默认端口（Host/ConnectionConfig 的默认值）
DEFAULT_PORT = 22


@dataclass
class HostProfile:
    """
    主机连接 Profile（共享连接默认值；绝不含凭据）

    Attributes:
        name: Profile 名称（唯一标识；非空）
        username: 默认 SSH 用户名（可选）
        port: 默认 SSH 端口（可选；1-65535）
        key_filename: 默认私钥路径（可选）
        tags: 附加标签（与主机标签并集）
        description: 默认描述（主机描述为空时使用）

    Raises:
        ValidationError: 构造时校验失败（空名称/端口越界/非字符串标签）
    """

    name: str
    username: Optional[str] = None
    port: Optional[int] = None
    key_filename: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError(f"profile name must be a non-empty string, got: {self.name!r}")
        if self.port is not None:
            if isinstance(self.port, bool) or not isinstance(self.port, int):
                raise ValidationError(f"profile port must be an integer, got: {self.port!r}")
            if not (1 <= self.port <= 65535):
                raise ValidationError(f"profile port must be in 1..65535, got: {self.port}")
        if self.tags is None:
            self.tags = []
        if not isinstance(self.tags, list) or any(not isinstance(t, str) for t in self.tags):
            raise ValidationError(f"profile tags must be a list of strings, got: {self.tags!r}")

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    _KNOWN_FIELDS = frozenset(
        {"name", "username", "port", "key_filename", "tags", "description"}
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典（字段固定，便于 JSON/SQLite 存取）。"""
        return {
            "name": self.name,
            "username": self.username,
            "port": self.port,
            "key_filename": self.key_filename,
            "tags": list(self.tags),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostProfile":
        """从字典构造（忽略未知字段，兼容未来扩展）。"""
        filtered = {k: v for k, v in data.items() if k in cls._KNOWN_FIELDS}
        return cls(**filtered)

    def __repr__(self) -> str:
        """安全表示：Profile 不含凭据，直接展示字段。"""
        return (
            f"HostProfile(name={self.name!r}, username={self.username!r}, "
            f"port={self.port!r}, key_filename={self.key_filename!r}, "
            f"tags={self.tags!r}, description={self.description!r})"
        )


__all__ = ["DEFAULT_PORT", "HostProfile"]
