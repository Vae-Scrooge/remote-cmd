"""
自定义异常模块

定义了 remote_cmd 包使用的完整异常层次结构。
所有自定义异常都继承自 RemoteCmdError 基类。

异常层次结构：
    RemoteCmdError (基类)
    ├── SSHError (SSH 相关错误基类)
    │   ├── SSHConnectionError (连接错误)
    │   ├── SSHCommandError (命令执行错误)
    │   └── SSHFileTransferError (文件传输错误)
    ├── ConfigError (配置错误)
    └── ValidationError (验证错误)

Author: Vae-Scrooge
"""

# ============================================================================
# 基础异常
# ============================================================================


class RemoteCmdError(Exception):
    """
    remote_cmd 包的基础异常类

    所有自定义异常都继承此类，便于统一捕获和处理。

    Example:
        >>> try:
        ...     # 某些操作
        ...     pass
        ... except RemoteCmdError as e:
        ...     print(f"操作失败: {e}")
    """

    pass


# ============================================================================
# SSH 相关异常
# ============================================================================


class SSHError(RemoteCmdError):
    """
    SSH 相关错误的基类

    所有 SSH 操作相关的异常都继承此类。
    包括连接、命令执行、文件传输等错误。
    """

    pass


class SSHConnectionError(SSHError):
    """
    SSH 连接错误

    当 SSH 连接建立失败时抛出，包括：
    - 认证失败
    - 网络超时
    - 主机无法解析
    - 密钥文件不存在

    Example:
        >>> raise SSHConnectionError("could not connect to host: connection refused")
    """

    pass


class SSHCommandError(SSHError):
    """
    SSH 命令执行错误

    当远程命令执行失败时抛出。
    注意：命令返回非零退出码不一定会抛出此异常，
    此异常主要用于命令执行本身出现问题的情况（如网络中断）。

    Example:
        >>> raise SSHCommandError("command execution timed out: timeout after 30 seconds")
    """

    pass


class SSHFileTransferError(SSHError):
    """
    SSH 文件传输错误

    当文件上传或下载失败时抛出，包括：
    - 本地文件不存在
    - 远程目录权限不足
    - 传输中断

    Example:
        >>> raise SSHFileTransferError("file upload failed: disk full")
    """

    pass


# ============================================================================
# 配置和验证异常
# ============================================================================


class ConfigError(RemoteCmdError):
    """
    配置错误

    当配置文件无效或配置项缺失时抛出。

    Example:
        >>> raise ConfigError("invalid config file format: invalid YAML")
    """

    pass


class ValidationError(RemoteCmdError):
    """
    输入验证错误

    当用户输入或参数验证失败时抛出。

    Example:
        >>> raise ValidationError("port must be between 1 and 65535")
    """

    pass
