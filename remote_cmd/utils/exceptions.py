"""
自定义异常模块

定义了 remote_cmd 包使用的完整异常层次结构。
所有自定义异常都继承自 RemoteCmdError 基类。

异常层次结构：
    RemoteCmdError (基类)
    ├── SSHError (SSH 相关错误基类)
    │   ├── SSHConnectionError (连接错误)
    │   │   ├── SSHAuthenticationError (认证失败 - 永久性，不可重试)
    │   │   └── SSHTimeoutError (连接超时 - 瞬态，可重试)
    │   ├── SSHCommandError (命令执行错误)
    │   │   └── SSHCommandTimeoutError (命令执行超时 - 瞬态，可重试)
    │   └── SSHFileTransferError (文件传输错误)
    ├── ConfigError (配置错误，别名 ConfigurationError)
    ├── CredentialError (凭据解析/解密失败 - 永久性，不可重试)
    └── ValidationError (验证错误 - 永久性，不可重试)

重试分类约定（详见 service/retry_policy.py）：
    - 瞬态（可重试）：SSHTimeoutError、SSHCommandTimeoutError、
      非认证类的 SSHConnectionError / SSHCommandError、网络 OSError
    - 永久性（绝不重试）：SSHAuthenticationError、CredentialError、
      ConfigError、ValidationError

兼容性说明（v2.1）：
    - 既有异常名称与导入路径保持不变；新增 SSH 异常仅作为**新增子类**插入，
      `except SSHConnectionError` / `except SSHCommandError` 等既有捕获
      行为不受影响。
    - `CredentialEncryptionError` 现在额外继承 `CredentialError`，原有捕获
      行为仍不受影响。
    - `ConfigurationError` 是 `ConfigError` 的别名（同一对象），便于按
      常见命名习惯捕获。

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
    - 认证失败（细分类型见 SSHAuthenticationError）
    - 网络超时（细分类型见 SSHTimeoutError）
    - 主机无法解析
    - 密钥文件不存在

    Example:
        >>> raise SSHConnectionError("could not connect to host: connection refused")
    """

    pass


class SSHAuthenticationError(SSHConnectionError):
    """
    SSH 认证失败（永久性错误，绝不可重试）

    密码/密钥被拒绝、账号被锁定等。重试同一凭据只会加剧锁定，
    批量执行器遇到此异常会立即放弃重试（见 service/retry_policy.py）。

    继承自 SSHConnectionError：既有 ``except SSHConnectionError``
    捕获行为完全兼容。

    Example:
        >>> raise SSHAuthenticationError("authentication failed: permission denied")
    """

    pass


class SSHTimeoutError(SSHConnectionError):
    """
    SSH 连接建立超时（瞬态错误，可重试）

    TCP 握手 / banner / 密钥协商阶段超时。区别于命令执行阶段的
    超时（SSHCommandTimeoutError）。

    继承自 SSHConnectionError：既有 ``except SSHConnectionError``
    捕获行为完全兼容。

    Example:
        >>> raise SSHTimeoutError("connection timeout: 192.168.1.100")
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


class SSHCommandTimeoutError(SSHCommandError):
    """
    远程命令执行超时（瞬态错误，可重试）

    命令在给定 wall-clock 超时内未产生任何输出/未退出。
    继承自 SSHCommandError：既有 ``except SSHCommandError``
    捕获行为完全兼容。

    Example:
        >>> raise SSHCommandTimeoutError("command timed out after 30 seconds")
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


# 别名：ConfigurationError 与 ConfigError 是同一个类，
# 便于按常见命名习惯捕获（见模块 docstring 兼容性说明）
ConfigurationError = ConfigError


class CredentialError(RemoteCmdError):
    """
    凭据错误（永久性错误，绝不可重试）

    凭据解析 / 解密 / 存取失败：解密密钥损坏、keyring 不可用、
    加密 token 格式非法等。与 SSHAuthenticationError 的区别：
    本异常发生在"取到凭据之前"（本地凭据链路），
    后者发生在"用凭据连服务器被拒"（远端认证）。

    Example:
        >>> raise CredentialError("failed to decrypt stored password: invalid token")
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
