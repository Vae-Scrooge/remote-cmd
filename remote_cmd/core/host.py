"""
主机数据模型模块

定义 Host 数据类，与仓储/服务层解耦，职责单一。

包括：
- Host: 远程主机配置数据类
- 序列化/反序列化方法
- 与连接配置的转换
"""

from dataclasses import asdict, dataclass
from typing import Any, Optional

from remote_cmd.core.ssh_client import ConnectionConfig


@dataclass
class Host:
    """
    远程主机配置类

    存储单个远程主机的所有连接信息和元数据。
    支持通过标签进行分类管理。

    Attributes:
        name: 主机名称（唯一标识符）
        hostname: 主机地址（IP 或域名）
        username: SSH 登录用户名
        port: SSH 端口号，默认为 22
        password: 登录密码（可选，可能被加密）
        key_filename: SSH 私钥文件路径（可选）
        tags: 主机标签列表，用于分类和筛选
        description: 主机描述信息

    Example:
        >>> host = Host(
        ...     name="web-server",
        ...     hostname="192.168.1.100",
        ...     username="admin",
        ...     key_filename="~/.ssh/id_rsa",
        ...     tags=["production", "web"]
        ... )
    """

    name: str
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    tags: Optional[list[str]] = None
    description: str = ""

    def __post_init__(self):
        """初始化后处理：设置默认标签列表"""
        if self.tags is None:
            self.tags = []

    def to_connection_config(self) -> ConnectionConfig:
        """
        将主机配置转换为 SSH 连接配置

        Returns:
            ConnectionConfig: 可直接用于 SSHClient 的连接配置对象
        """
        return ConnectionConfig(
            hostname=self.hostname,
            username=self.username,
            port=self.port,
            password=self.password,
            key_filename=self.key_filename,
        )

    def to_dict(self) -> dict:
        """
        将主机配置转换为字典

        Returns:
            Dict: 包含所有主机属性的字典
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Host":
        """
        从字典创建主机配置对象

        Args:
            data: 包含主机属性的字典

        Returns:
            Host: 主机配置对象
        """
        # 只提取 Host 已知的字段，忽略未知字段
        known_fields = {
            "name",
            "hostname",
            "username",
            "port",
            "password",
            "key_filename",
            "tags",
            "description",
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def sanitized_dict(self) -> dict[str, Any]:
        """
        返回脱敏后的主机字典，用于日志、显示、API 响应等场景。

        敏感字段（password、key_filename）被替换为安全标识。

        Returns:
            Dict: 不包含敏感明文的主机信息字典
        """
        data = asdict(self)
        # 脱敏密码：显示加密状态而非明文
        pw = data.get("password")
        if pw:
            data["password"] = "***encrypted***" if isinstance(pw, str) and pw.startswith("$encrypted$") else "***"
        # 脱敏密钥路径：仅显示文件名
        if data.get("key_filename"):
            from pathlib import Path
            data["key_filename"] = Path(data["key_filename"]).name
        return data

    def __repr__(self) -> str:
        """安全的字符串表示：自动脱敏敏感字段"""
        safe = self.sanitized_dict()
        fields = ", ".join(f"{k}={v!r}" for k, v in safe.items())
        return f"Host({fields})"
