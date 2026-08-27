"""
SSH 客户端模块

提供高级别的 SSH 连接和操作接口，包括：
- 远程命令执行
- 文件上传/下载
- 远程目录管理
- sudo 权限命令执行

依赖：paramiko 库

Author: Vae-Scrooge
"""

import contextlib
import logging
import re
import shlex
import socket
import stat
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import paramiko

from remote_cmd.utils.exceptions import (
    SSHAuthenticationError,
    SSHCommandError,
    SSHCommandTimeoutError,
    SSHConnectionError,
    SSHFileTransferError,
    SSHTimeoutError,
    ValidationError,
)

# 模块日志记录器
logger = logging.getLogger(__name__)

# 安全警告常量
_SECURITY_WARNING_AUTOADD = (
    "SECURITY WARNING: AutoAddPolicy automatically accepts unknown host keys, "
    "making connections vulnerable to MITM attacks. "
    "Use RejectPolicy (default) or pre-load known_hosts in production."
)

# 环境变量键的合法 shell 标识符模式（值已 shlex.quote 转义，键直接拼入
# export 命令，必须在拼接前校验防止命令注入）
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# stderr 排空线程的有界 join 时限（秒）。正常路径下通道关闭会使阻塞读取
# 立即返回（毫秒级）；此上限仅防御极端场景——超时回调中 channel.close()
# 失败（异常被抑制），或主线程读取异常退出而通道仍打开——避免 join 无限
# 等待。超时后线程仍为 daemon，不会阻止进程退出
_READER_JOIN_TIMEOUT = 5.0


def validate_environment(environment: Optional[dict[str, str]]) -> None:
    """校验环境变量字典的键均为合法 shell 标识符。

    安全：值会经 ``shlex.quote`` 转义，但键直接拼入
    ``export {k}=...`` 命令前缀——含 shell 元字符的键（如
    ``A; malicious``）会造成命令注入，必须在拼接前拒绝。

    Args:
        environment: 环境变量字典（可为 None / 空）

    Raises:
        ValidationError: 存在非法键
    """
    if not environment:
        return
    for key in environment:
        if not _ENV_KEY_PATTERN.match(key):
            raise ValidationError(
                f"invalid environment variable name: {key!r} (must match [A-Za-z_][A-Za-z0-9_]*)"
            )


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class ConnectionConfig:
    """
    SSH 连接配置类

    用于存储和管理 SSH 连接所需的所有参数。
    支持密码认证、SSH 密钥认证和 SSH Agent 三种方式。

    Attributes:
        hostname: 目标主机地址（IP 或域名）
        username: SSH 登录用户名
        port: SSH 端口号，默认为 22
        password: 登录密码（可选）
        key_filename: SSH 私钥文件路径（可选）
        timeout: 连接超时时间（秒），默认 30 秒
        compress: 是否启用压缩，默认启用
        host_key_policy: 主机密钥验证策略，默认 None（即 RejectPolicy，
                        拒绝未知主机密钥）。
                        可设为 paramiko.AutoAddPolicy() 自动接受新主机密钥，
                        或 paramiko.RejectPolicy() 严格验证。
                         警告: AutoAddPolicy 容易受到 MITM 攻击！

    Note:
        - password 和 key_filename 可以同时为 None，此时使用 SSH Agent 认证
        - 生产环境建议使用 SSH 密钥或 Agent 认证，避免明文密码
    """

    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    timeout: int = 30
    compress: bool = True
    host_key_policy: Optional[Any] = None
    known_hosts_file: Optional[str] = None

    def __post_init__(self):
        """初始化后验证：校验端口、主机名等"""
        # 验证端口号
        if not (1 <= self.port <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got: {self.port}")

        # 验证主机名/IP 不为空
        if not self.hostname or not self.hostname.strip():
            raise ValueError("hostname must not be empty")
        if not self.username or not self.username.strip():
            raise ValueError("username must not be empty")

        # 安全提示：AutoAddPolicy 存在 MITM 风险
        if isinstance(self.host_key_policy, paramiko.AutoAddPolicy):
            logger.warning(_SECURITY_WARNING_AUTOADD)


@dataclass
class CommandResult:
    """
    命令执行结果类

    封装远程命令执行的返回结果，包括标准输出、标准错误和退出码。

    Attributes:
        command: 执行的命令字符串
        stdout: 标准输出内容
        stderr: 标准错误内容
        exit_code: command exit code (0 means success)
    """

    command: str
    stdout: str
    stderr: str
    exit_code: int

    @property
    def success(self) -> bool:
        """
        whether the command executed successfully

        Returns:
            bool: 退出码为 0 时返回 True，否则返回 False
        """
        return self.exit_code == 0

    def __str__(self) -> str:
        """
        生成命令结果的可读字符串表示

        Returns:
            str: 格式为 "状态符号 [退出码] 命令"
        """
        status = "✓" if self.success else "✗"
        return f"{status} [{self.exit_code}] {self.command}"


@dataclass
class RemoteFileEntry:
    """
    远程文件/目录条目信息

    描述远程目录中的单个条目（文件或目录），替代弱类型的字典返回。

    Attributes:
        name: 文件/目录名
        size: 文件大小（字节）
        mode: 权限模式（八进制字符串）
        mtime: 修改时间戳
        is_dir: 是否为目录
    """

    name: str
    size: int
    mode: str
    mtime: Any
    is_dir: bool


# ============================================================================
# SSH 客户端类
# ============================================================================


class SSHClient:
    """
    高级 SSH 客户端类

    提供完整的 SSH 连接管理功能，支持上下文管理器模式，
    可以使用 `with` 语句自动管理连接的生命周期。

    主要功能：
    - 建立/断开 SSH 连接
    - 执行远程命令（普通命令和 sudo 命令）
    - 文件上传/下载
    - 远程目录浏览

    使用示例：
        >>> config = ConnectionConfig(
        ...     hostname="example.com",
        ...     username="admin",
        ...     key_filename="~/.ssh/id_rsa"
        ... )
        >>> with SSHClient(config) as client:
        ...     result = client.execute("ls -la")
        ...     print(result.stdout)
    """

    def __init__(self, config: ConnectionConfig) -> None:
        """
        初始化 SSH 客户端

        Args:
            config: ConnectionConfig 对象，包含连接参数

        Note:
            初始化时不会建立连接，需要调用 connect() 方法或使用上下文管理器
        """
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # ========================================================================
    # 连接管理方法
    # ========================================================================

    def connect(self) -> "SSHClient":
        """
        建立 SSH 连接

        根据配置信息建立到远程服务器的 SSH 连接。
        支持密码认证和密钥认证两种方式。

        Returns:
            SSHClient: 返回自身，支持链式调用

        Raises:
            SSHConnectionError: 连接失败时抛出，包括：
                - 认证失败
                - 连接超时
                - 主机无法解析
                - 其他网络错误

        Example:
            >>> client = SSHClient(config)
            >>> client.connect()  # 建立连接
            >>> # 或链式调用
            >>> client.connect().execute("ls")
        """
        try:
            # 创建 SSH 客户端实例
            self._client = paramiko.SSHClient()

            # 设置主机密钥策略
            policy = self.config.host_key_policy or paramiko.RejectPolicy()
            if isinstance(policy, paramiko.AutoAddPolicy):
                logger.warning(_SECURITY_WARNING_AUTOADD)
            self._client.set_missing_host_key_policy(policy)

            # 加载 known_hosts 文件（可选）
            known_hosts = self.config.known_hosts_file
            if known_hosts:
                known_hosts_path = Path(known_hosts).expanduser()
                if known_hosts_path.exists():
                    self._client.load_host_keys(str(known_hosts_path))
                    logger.debug(f"loaded known_hosts: {known_hosts_path}")
                else:
                    logger.warning(f"known_hosts file not found: {known_hosts_path}")

            # 构建连接参数字典
            connect_kwargs = {
                "hostname": self.config.hostname,
                "port": self.config.port,
                "username": self.config.username,
                "timeout": self.config.timeout,
                "compress": self.config.compress,
            }

            # 根据认证方式添加相应参数
            if self.config.password:
                # 密码认证
                connect_kwargs["password"] = self.config.password
            elif self.config.key_filename:
                # 密钥认证：展开 ~ 并验证文件存在
                key_path = Path(self.config.key_filename).expanduser()
                if not key_path.exists():
                    raise SSHConnectionError(f"SSH key file not found: {key_path}")
                connect_kwargs["key_filename"] = str(key_path)

            # 记录连接日志
            logger.info(f"connecting to {self.config.hostname}:{self.config.port}")

            # 建立连接
            self._client.connect(**connect_kwargs)
            logger.info(f"connected to {self.config.hostname}")

            return self

        except paramiko.AuthenticationException as e:
            # 永久性错误：重试同一凭据只会加剧账号锁定（见 service/retry_policy.py）
            raise SSHAuthenticationError(f"authentication failed: {e}") from e
        except socket.timeout as e:
            raise SSHTimeoutError(f"connection timeout: {self.config.hostname}") from e
        except socket.gaierror as e:
            raise SSHConnectionError(f"could not resolve hostname: {self.config.hostname}") from e
        except (OSError, paramiko.SSHException) as e:
            raise SSHConnectionError(f"connection error: {e}") from e

    def disconnect(self) -> None:
        """
        断开 SSH 连接并清理资源

        关闭 SFTP 和 SSH 连接，释放所有相关资源。
        即使连接已断开或出现错误，此方法也能安全执行。
        """
        # 关闭 SFTP 连接
        if self._sftp:
            try:
                self._sftp.close()
                logger.debug("SFTP connection closed")
            except (OSError, paramiko.SSHException) as e:
                logger.warning(f"error closing SFTP connection: {e}")
            finally:
                self._sftp = None

        # 关闭 SSH 连接
        if self._client:
            try:
                self._client.close()
                logger.debug("SSH connection closed")
            except (OSError, paramiko.SSHException) as e:
                logger.warning(f"error closing SSH connection: {e}")
            finally:
                self._client = None

    def is_connected(self) -> bool:
        """
        检查 SSH 连接是否处于活动状态

        Returns:
            bool: 连接活动返回 True，否则返回 False
        """
        if not self._client:
            return False

        try:
            transport = self._client.get_transport()
            return transport is not None and transport.is_active()
        except (AttributeError, OSError):
            return False

    def _read_output(
        self,
        stdout: Any,
        stderr: Any,
        timeout: Optional[int],
    ) -> tuple[int, str, str]:
        """并发排空命令输出流并返回 (exit_code, stdout, stderr)。

        大输出死锁防护（paramiko 官方文档对 ``recv_exit_status`` 的警告
        场景）：SSH 通道窗口（默认 2MB）限制远端可发送的未确认数据量。
        若在排空输出流之前等待退出状态、或只阻塞读取其中一流，远端写满
        窗口后会阻塞，命令永不退出 → 死锁。因此：

        - stderr 由后台线程排空、stdout 在当前线程读取，两流并发消费，
          窗口持续调整，远端永远不会因窗口耗尽而卡死；
        - 两个流都读到 EOF（命令已退出）后再取退出状态，此时立即返回。

        超时语义（wall-clock，与 AsyncSSHClient 的 ``conn.run(timeout=...)``
        对齐）：不使用通道 ``settimeout``（其 per-recv 语义会误杀"长时间
        静默于单一流但整体健康"的命令），改由定时器在超时后关闭通道——
        关闭使两个阻塞读取解除（返回已缓冲数据），且 ``_set_closed`` 会
        置位 status_event 使 ``recv_exit_status`` 立即返回，不会二次挂起。

        Args:
            stdout: exec_command 返回的 stdout 文件对象
            stderr: exec_command 返回的 stderr 文件对象
            timeout: wall-clock 超时（秒），None 表示不限时

        Returns:
            tuple[int, str, str]: (exit_code, stdout_text, stderr_text)

        Raises:
            SSHCommandTimeoutError: 命令在 timeout 内未完成
        """
        channel = stdout.channel
        stderr_bytes = b""
        stderr_error: Optional[BaseException] = None

        def _drain_stderr() -> None:
            nonlocal stderr_bytes, stderr_error
            try:
                stderr_bytes = stderr.read()
            except BaseException as e:  # noqa: BLE001 - 线程内异常回传主线程
                stderr_error = e

        reader = threading.Thread(target=_drain_stderr, name="ssh-stderr-drain", daemon=True)

        timed_out = threading.Event()

        def _on_timeout() -> None:
            timed_out.set()
            # 关闭通道以终止远端命令，并解除两个读取的阻塞
            with contextlib.suppress(Exception):
                channel.close()

        timer: Optional[threading.Timer] = None
        if timeout is not None:
            timer = threading.Timer(timeout, _on_timeout)
            timer.daemon = True
            timer.start()

        try:
            reader.start()
            stdout_bytes = stdout.read()
        finally:
            if timer is not None:
                timer.cancel()
            # 有界 join：正常路径通道关闭后 reader 立即返回；极端场景下
            # （close 失败 / 主线程读取异常而通道未关闭）reader 可能仍
            # 阻塞在远端输出上——放弃等待其自然退出，避免调用方永久挂起
            reader.join(timeout=_READER_JOIN_TIMEOUT)
            if reader.is_alive():
                logger.debug(
                    "stderr drain thread did not finish within %.1fs", _READER_JOIN_TIMEOUT
                )

        if timed_out.is_set():
            raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")
        if stderr_error is not None:
            raise stderr_error

        exit_code = channel.recv_exit_status()
        return (
            exit_code,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    def _get_sftp(self) -> paramiko.SFTPClient:
        """获取 SFTP 客户端（延迟初始化）"""
        if not self._client:
            raise SSHConnectionError("not connected, call connect() first")
        if not self._sftp:
            self._sftp = self._client.open_sftp()
        return self._sftp

    # ========================================================================
    # 上下文管理器支持
    # ========================================================================

    def __enter__(self) -> "SSHClient":
        """
        上下文管理器入口：自动建立连接

        Returns:
            SSHClient: 已连接的客户端实例
        """
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """
        上下文管理器出口：自动断开连接

        Args:
            exc_type: 异常类型
            exc_val: 异常值
            exc_tb: 异常追踪信息
        """
        self.disconnect()

    # ========================================================================
    # 命令执行方法
    # ========================================================================

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        """
        在远程服务器上执行命令

        Args:
            command: 要执行的命令字符串
            timeout: 命令执行 wall-clock 超时时间（秒），None 表示不限时。
                超时后关闭通道终止远端命令并抛出 SSHCommandTimeoutError。
                输出流在内部并发排空，大输出（超过 SSH 通道窗口）不会死锁
            environment: 环境变量字典，将在命令执行前设置

        Returns:
            CommandResult: 包含命令执行结果的对象

        Raises:
            SSHCommandError: 命令执行失败时抛出
            SSHConnectionError: 未连接时抛出

        Example:
            >>> result = client.execute("ls -la")
            >>> if result.success:
            ...     print(result.stdout)
        """
        # 检查连接状态
        if not self._client:
            raise SSHConnectionError("not connected, call connect() first")

        # 安全：键必须为合法 shell 标识符（值虽已转义，键直接拼入命令）
        validate_environment(environment)

        try:
            # 安全：不记录命令全文（可能含敏感参数），仅记录执行事件
            logger.debug("executing remote command")

            # 构建环境变量设置命令
            # 安全：对 value 做 shlex.quote 转义，防止包含 shell 元字符
            # （如 ;、$()、反引号）的值触发命令注入或带空格的值静默失败
            env_str = ""
            if environment:
                env_vars = [f"export {k}={shlex.quote(str(v))}" for k, v in environment.items()]
                env_str = "; ".join(env_vars) + "; "

            # 组合完整命令（切换到用户主目录执行）
            full_command = f"{env_str}cd ~ && {command}"

            # 执行命令（timeout 为 wall-clock 语义，由 _read_output 实施：
            # 并发排空两流防大输出死锁，超时关闭通道终止远端命令）
            stdin, stdout, stderr = self._client.exec_command(full_command)

            # 获取命令执行结果（先排空输出流，再取退出状态）
            exit_code, stdout_data, stderr_data = self._read_output(stdout, stderr, timeout)

            # 构建结果对象
            result = CommandResult(
                command=command,
                stdout=stdout_data,
                stderr=stderr_data,
                exit_code=exit_code,
            )

            logger.debug(f"command finished, exit code: {exit_code}")
            return result

        except (paramiko.SSHException, OSError) as e:
            raise SSHCommandError(f"command execution failed: {e}") from e

    def execute_sudo(
        self,
        command: str,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """
        以 sudo 权限执行命令（安全实现）

        Args:
            command: 要执行的命令字符串（不需要包含 sudo 前缀）
            password: sudo 密码（如果需要），None 表示使用无密码 sudo
            timeout: 命令执行超时时间（秒）

        Returns:
            CommandResult: 包含命令执行结果的对象

        Note:
            - 如果提供了 password，使用 exec_command + -S 从 stdin 传入密码
            - 密码不会出现在进程列表或日志中
            - stdout 和 stderr 保持独立分离

        Example:
            >>> result = client.execute_sudo("systemctl restart nginx", password="mypass")
        """
        if not self._client:
            raise SSHConnectionError("not connected, call connect() first")

        if password is None:
            full_command = f"sudo {command}"
            return self.execute(full_command, timeout)

        # 使用 exec_command + sudo -S 从 stdin 传入密码，保持 stdout/stderr 分离
        try:
            full_command = f"sudo -S {command}"
            # get_pty=False：避免 PTY 合并 stdout/stderr（与文档"独立分离"一致），
            # 同时关闭 PTY echo 防止 sudo 密码被回显到 stdout 造成凭据泄露
            stdin, stdout, stderr = self._client.exec_command(full_command, get_pty=False)
            stdin.write(password + "\n")
            stdin.flush()

            # 与 execute 一致：先并发排空两流（防大输出死锁），再取退出状态；
            # timeout 为 wall-clock 语义
            exit_code, stdout_data, stderr_data = self._read_output(stdout, stderr, timeout)

            return CommandResult(
                command=command,
                stdout=stdout_data,
                stderr=stderr_data,
                exit_code=exit_code,
            )
        except (paramiko.SSHException, OSError) as e:
            raise SSHCommandError(f"sudo command execution failed: {e}") from e

    # ========================================================================
    # 文件传输方法
    # ========================================================================

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """
        上传本地文件到远程服务器

        Args:
            local_path: 本地文件路径
            remote_path: 远程目标路径（绝对路径）

        Raises:
            SSHFileTransferError: 文件传输失败时抛出
            SSHConnectionError: 未连接时抛出

        Example:
            >>> client.upload_file("./script.sh", "/home/user/script.sh")
        """
        sftp = self._get_sftp()

        # 验证本地文件存在
        local_file = Path(local_path)
        if not local_file.exists():
            raise SSHFileTransferError(f"Local file not found: {local_path}")

        # 执行上传
        try:
            logger.info(f"uploading file: {local_path} -> {remote_path}")
            sftp.put(str(local_file), remote_path)
            logger.info("file upload finished")
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"file upload failed: {e}") from e

    def download_file(self, remote_path: str, local_path: str) -> None:
        """
        从远程服务器下载文件到本地

        Args:
            remote_path: 远程文件路径（绝对路径）
            local_path: 本地目标路径

        Raises:
            SSHFileTransferError: 文件传输失败时抛出
            SSHConnectionError: 未连接时抛出

        Note:
            如果本地目录不存在，将自动创建

        Example:
            >>> client.download_file("/var/log/syslog", "./logs/syslog")
        """
        sftp = self._get_sftp()

        # 确保本地目录存在
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)

        # 执行下载
        try:
            logger.info(f"downloading file: {remote_path} -> {local_path}")
            sftp.get(remote_path, str(local_file))
            logger.info("file download finished")
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"file download failed: {e}") from e

    def list_remote_directory(self, remote_path: str = ".") -> list[RemoteFileEntry]:
        """
        列出远程目录内容

        Args:
            remote_path: 远程目录路径，默认为当前目录

        Returns:
            List[RemoteFileEntry]: 目录项信息列表

        Raises:
            SSHFileTransferError: 列出目录失败时抛出
            SSHConnectionError: 未连接时抛出

        Example:
            >>> entries = client.list_remote_directory("/home/user")
            >>> for entry in entries:
            ...     print(f"{entry.name}: {entry.size} bytes")
        """
        sftp = self._get_sftp()

        try:
            entries: list[RemoteFileEntry] = []
            for entry in sftp.listdir_attr(remote_path):
                mode = entry.st_mode if entry.st_mode is not None else 0
                entries.append(
                    RemoteFileEntry(
                        name=entry.filename,
                        size=entry.st_size,
                        mode=oct(mode)[-3:] if mode else "000",
                        mtime=entry.st_mtime,
                        is_dir=bool(mode & stat.S_IFDIR) if mode else False,
                    )
                )
            return entries
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to list remote directory: {e}") from e

    def create_remote_directory(self, path: str) -> None:
        """创建远程目录（支持递归创建）"""
        sftp = self._get_sftp()

        def _makedirs(sftp_client: paramiko.SFTPClient, remote_path: str) -> None:
            # 远端路径始终是 POSIX 语义，必须用 PurePosixPath 处理，
            # 不能用本地 Path（在 Windows 上 WindowsPath 对 "/" 的处理会导致无限递归）。
            p = PurePosixPath(remote_path)
            if p == PurePosixPath("/") or p == PurePosixPath("."):
                return
            try:
                sftp_client.stat(str(p))
            except OSError:
                _makedirs(sftp_client, str(p.parent))
                sftp_client.mkdir(str(p))

        try:
            _makedirs(sftp, path)
            logger.info(f"created remote directory: {path}")
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to create remote directory: {e}") from e

    def remove_remote_file(self, path: str) -> None:
        """删除远程文件"""
        sftp = self._get_sftp()
        try:
            sftp.remove(path)
            logger.info(f"deleted remote file: {path}")
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to delete remote file: {e}") from e

    def remove_remote_directory(self, path: str, recursive: bool = False) -> None:
        """删除远程目录"""
        sftp = self._get_sftp()

        def _rm_recursive(sftp_client: paramiko.SFTPClient, remote_path: str) -> None:
            """递归删除目录内容，先收集后删除以避免不一致状态"""
            entries: list[tuple[str, bool]] = []
            try:
                for entry in sftp_client.listdir_attr(remote_path):
                    entries.append((entry.filename, bool(entry.st_mode & stat.S_IFDIR)))
            except OSError:
                return
            # 先删除文件，再递归删除子目录
            for name, is_dir in entries:
                full_path = f"{remote_path}/{name}"
                if is_dir:
                    _rm_recursive(sftp_client, full_path)
                else:
                    sftp_client.remove(full_path)
            sftp_client.rmdir(remote_path)

        try:
            if recursive:
                _rm_recursive(sftp, path)
            else:
                sftp.rmdir(path)
            logger.info(f"deleted remote directory: {path}")
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to delete remote directory: {e}") from e

    def remote_file_exists(self, path: str) -> bool:
        """检查远程文件是否存在"""
        try:
            sftp = self._get_sftp()
            sftp.stat(path)
            return True
        except OSError:
            return False
        except SSHConnectionError:
            return False

    def get_remote_file_info(self, path: str) -> dict[str, Any]:
        """获取远程文件信息"""
        sftp = self._get_sftp()
        try:
            stat_result = sftp.stat(path)
            mode = stat_result.st_mode
            return {
                "name": Path(path).name,
                "size": stat_result.st_size,
                "mode": oct(mode)[-3:] if mode else "000",
                "mtime": stat_result.st_mtime,
                "is_dir": stat.S_ISDIR(mode),
                "is_file": stat.S_ISREG(mode),
            }
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to get file info: {e}") from e
