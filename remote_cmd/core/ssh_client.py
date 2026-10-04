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
import errno
import logging
import os
import re
import shlex
import socket
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
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

# Paramiko exposes nonblocking stream-readiness methods on Channel. Poll both
# buffers in bounded chunks from the command worker so neither stream needs a
# dedicated thread. The wait only applies while both streams are idle.
_COMMAND_POLL_INTERVAL = 0.005
_COMMAND_READ_CHUNK_SIZE = 32 * 1024


def _create_download_temp(local_file: Path) -> Path:
    """Create a same-directory staging file for an atomic local download."""
    try:
        existing_mode = stat.S_IMODE(local_file.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{local_file.name}.",
        suffix=".part",
        dir=local_file.parent,
    )
    temp_path = Path(temp_name)
    try:
        os.close(fd)
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        return temp_path
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def _remote_temporary_path(remote_path: str, label: str = "part") -> str:
    """Return a unique sibling path without extending a near-limit filename."""
    return str(PurePosixPath(remote_path).parent / f".remote-cmd-{uuid.uuid4().hex}.{label}")


def _resolve_remote_upload_target(
    sftp: paramiko.SFTPClient,
    remote_path: str,
) -> tuple[str, Optional[int], bool]:
    """Resolve an upload target, following a final symlink like SFTP ``put``.

    Returns the path to replace, the existing target mode when available, and
    whether a destination existed. The mode is used to avoid changing the
    permissions of an existing file when the staged file is committed.
    """
    try:
        attrs = sftp.lstat(remote_path)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT:
            return remote_path, None, False
        raise

    mode = attrs.st_mode
    if isinstance(mode, int) and stat.S_ISLNK(mode):
        # Paramiko's put() follows a destination symlink. Resolve it before
        # staging so the final rename preserves that behavior.
        remote_path = sftp.normalize(remote_path)
        try:
            attrs = sftp.stat(remote_path)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ENOENT:
                return remote_path, None, False
            raise
        mode = attrs.st_mode

    if isinstance(mode, int) and stat.S_ISDIR(mode):
        raise SSHFileTransferError(f"remote destination is a directory: {remote_path}")

    return remote_path, mode if isinstance(mode, int) else None, True


def _commit_remote_upload(
    sftp: paramiko.SFTPClient,
    staged_path: str,
    remote_path: str,
    existing_mode: Optional[int],
    destination_existed: bool,
) -> None:
    """Replace the destination only after a complete staged upload.

    Prefer OpenSSH's atomic POSIX rename. Fall back to standard SFTP rename
    when the destination is absent; when replacing an existing regular file on
    servers without POSIX rename, preserve it under a unique backup until the
    staged file is committed. A failed commit attempts to restore that backup.
    """
    if existing_mode is not None:
        # SFTP put traditionally preserves an existing file's mode by opening
        # it for truncation. Staging creates a fresh file, so copy the mode
        # before rename to retain that behavior without a permissions window.
        sftp.chmod(staged_path, stat.S_IMODE(existing_mode))

    try:
        sftp.posix_rename(staged_path, remote_path)
        return
    except (OSError, paramiko.SSHException) as posix_error:
        try:
            sftp.rename(staged_path, remote_path)
            return
        except (OSError, paramiko.SSHException) as rename_error:
            if not destination_existed:
                # Do not overwrite a file created concurrently after the
                # initial lstat. Ordinary rename is safe for the absent-target
                # case; if it failed, surface the failure instead of backing
                # up an unrelated concurrent destination.
                raise rename_error
            try:
                attrs = sftp.lstat(remote_path)
            except OSError as stat_error:
                if getattr(stat_error, "errno", None) == errno.ENOENT:
                    raise rename_error
                raise stat_error from posix_error

            mode = attrs.st_mode
            if not isinstance(mode, int) or stat.S_ISDIR(mode):
                raise posix_error

            backup_path = _remote_temporary_path(remote_path, "backup")
            try:
                sftp.rename(remote_path, backup_path)
            except (OSError, paramiko.SSHException) as backup_error:
                raise backup_error from posix_error
            try:
                sftp.rename(staged_path, remote_path)
            except BaseException:
                try:
                    sftp.rename(backup_path, remote_path)
                except Exception:  # noqa: BLE001 - preserve/report fallback backup
                    logger.error("failed to restore remote upload backup", exc_info=True)
                raise
            try:
                sftp.remove(backup_path)
            except (OSError, paramiko.SSHException):
                logger.warning("remote upload succeeded but backup cleanup failed")


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

    def __post_init__(self) -> None:
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
            else:
                # Paramiko does not automatically load ~/.ssh/known_hosts.
                # Load its standard system/user host-key files before applying
                # RejectPolicy, preserving strict verification and normal SSH UX.
                self._client.load_system_host_keys()

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
            with contextlib.suppress(Exception):
                self.disconnect()
            raise SSHAuthenticationError(f"authentication failed: {e}") from e
        except socket.timeout as e:
            with contextlib.suppress(Exception):
                self.disconnect()
            raise SSHTimeoutError(f"connection timeout: {self.config.hostname}") from e
        except socket.gaierror as e:
            with contextlib.suppress(Exception):
                self.disconnect()
            raise SSHConnectionError(f"could not resolve hostname: {self.config.hostname}") from e
        except (OSError, paramiko.SSHException) as e:
            with contextlib.suppress(Exception):
                self.disconnect()
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
        *,
        timed_out_event: Optional[threading.Event] = None,
        manage_timeout: bool = True,
        deadline: Optional[float] = None,
    ) -> tuple[int, str, str]:
        """轮询排空命令的两个输出流并返回 (exit_code, stdout, stderr)。

        大输出死锁防护（paramiko 官方文档对 ``recv_exit_status`` 的警告
        场景）：SSH 通道窗口（默认 2MB）限制远端可发送的未确认数据量。
        若在排空输出流之前等待退出状态、或只阻塞读取其中一流，远端写满
        窗口后会阻塞，命令永不退出 → 死锁。因此：

        - 在同一命令 worker 中轮流 poll stdout/stderr readiness，每轮每流
          最多读取一个有限 chunk；双流 flood 时也持续为两侧归还窗口；
        - 只有 exit-status 已就绪且两个输入 buffer 都为空后才取状态包。

        超时语义为 wall-clock：外层 watchdog 从 exec request 前运行到
        ``recv_exit_status`` 返回；readiness polling 使用同一 monotonic
        deadline。watchdog 覆盖 Paramiko exec request 内部不带 timeout 的
        event wait，并关闭 channel（若关闭失败则关闭 transport）。

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
        # Keep the file-like argument in the private signature for call-site
        # compatibility; both Paramiko file wrappers share stdout.channel.
        _ = stderr
        timed_out = timed_out_event or threading.Event()
        if deadline is None and timeout is not None:
            deadline = time.monotonic() + timeout
        stdout_bytes = bytearray()
        stderr_bytes = bytearray()
        exit_code = -1

        def _on_timeout() -> None:
            timed_out.set()
            # close() interrupts exec/read/status waits. If channel-level close
            # fails, dropping the transport is the stronger cancellation.
            self._close_command_channel(channel)

        timer: Optional[threading.Timer] = None
        if timeout is not None and manage_timeout:
            timer = threading.Timer(timeout, _on_timeout)
            timer.daemon = True
            timer.start()

        try:
            while True:
                if timed_out.is_set():
                    raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")
                if deadline is not None and time.monotonic() >= deadline:
                    _on_timeout()
                    raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")

                # Bounded alternating reads ensure a continuously-ready stdout
                # cannot starve stderr (or vice versa) and keep SSH window
                # adjustments flowing on both streams.
                made_progress = False
                if channel.recv_ready():
                    chunk = channel.recv(_COMMAND_READ_CHUNK_SIZE)
                    if chunk:
                        stdout_bytes.extend(chunk)
                        made_progress = True
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(_COMMAND_READ_CHUNK_SIZE)
                    if chunk:
                        stderr_bytes.extend(chunk)
                        made_progress = True

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    # exit_status_ready() makes recv_exit_status() immediate;
                    # all preceding DATA packets are already in these buffers.
                    exit_code = channel.recv_exit_status()
                    break

                if not made_progress:
                    wait_for = _COMMAND_POLL_INTERVAL
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            _on_timeout()
                            raise SSHCommandTimeoutError(
                                f"command timed out after {timeout} seconds"
                            )
                        wait_for = min(wait_for, remaining)
                    # The watchdog event interrupts this wait at the wall-clock
                    # deadline rather than adding a full polling interval.
                    timed_out.wait(wait_for)
        except BaseException:
            # No helper reader remains to be orphaned. On cancellation or a
            # stream exception, stop the remote command before propagating.
            if not timed_out.is_set():
                self._close_command_channel(channel)
            raise
        finally:
            if timer is not None:
                timer.cancel()

        if timed_out.is_set():
            raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds")
        return (
            exit_code,
            bytes(stdout_bytes).decode("utf-8", errors="replace"),
            bytes(stderr_bytes).decode("utf-8", errors="replace"),
        )

    def _close_command_channel(self, channel: Any) -> None:
        """Close the command channel, falling back to its transport on failure."""
        try:
            channel.close()
            return
        except Exception:  # noqa: BLE001 - use transport close as stronger cleanup
            logger.debug("failed to close SSH command channel; closing transport", exc_info=True)
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()

    def _get_sftp(self, timeout: Optional[float] = None) -> paramiko.SFTPClient:
        """获取 SFTP 客户端（延迟初始化）。

        v2.3：每次取用时把有效的 inactivity（静默）超时应用到 SFTP 底层
        channel。Paramiko ``Channel.settimeout`` 对 ``recv``/``sendall`` 生效，
        传输正常时不受影响；通道静默超过该时长会抛出 ``socket.timeout``，
        从而中止操作而不是永久阻塞。

        Args:
            timeout: 本次操作的 inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``
        """
        if not self._client:
            raise SSHConnectionError("not connected, call connect() first")
        effective = self._effective_sftp_timeout(timeout)
        open_timed_out: Optional[threading.Event] = None
        try:
            if not self._sftp:
                timed_out = threading.Event()
                open_timed_out = timed_out
                opened_sftp: list[paramiko.SFTPClient] = []

                def _close_stalled_open() -> None:
                    timed_out.set()
                    if opened_sftp:
                        with contextlib.suppress(Exception):
                            opened_sftp[0].close()
                    elif self._client is not None:
                        # During subsystem negotiation the SFTP channel is not
                        # returned yet; closing transport interrupts open_sftp.
                        with contextlib.suppress(Exception):
                            self._client.close()

                timer = threading.Timer(effective, _close_stalled_open)
                timer.daemon = True
                timer.start()
                try:
                    opened = self._client.open_sftp()
                    opened_sftp.append(opened)
                    if timed_out.is_set():
                        raise socket.timeout("timed out opening SFTP channel")
                    self._sftp = opened
                finally:
                    timer.cancel()
            # 显式应用（含 per-call 覆盖）：同一缓存的 SFTP channel 可能上次
            # 使用不同的超时值
            self._sftp.get_channel().settimeout(effective)
            return self._sftp
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("open SFTP channel", effective) from e
        except (paramiko.SSHException, OSError, EOFError) as e:
            if open_timed_out is not None and open_timed_out.is_set():
                raise self._sftp_timeout_error("open SFTP channel", effective) from e
            self._discard_sftp()
            raise SSHFileTransferError(f"failed to open SFTP channel: {e}") from e

    def _effective_sftp_timeout(self, timeout: Optional[float]) -> float:
        """返回 SFTP 操作的有效 inactivity 超时（秒）。

        显式 timeout 优先，否则回退到 ``ConnectionConfig.timeout``。
        与命令执行不同，SFTP 使用"静默"语义：只要数据持续流动，长时间
        传输不会被中止；只有超过该时长没有任何进展才会失败。
        """
        if timeout is not None:
            if timeout <= 0:
                raise ValidationError(f"timeout must be > 0, got: {timeout}")
            return timeout
        return self.config.timeout

    def _discard_sftp(self) -> None:
        """关闭并丢弃缓存的 SFTP 会话。

        超时后 SFTP 请求可能仍处于未完成状态；复用同一通道会导致响应错配
        （desynchronization）。关闭并置空后，下一次 SFTP 操作会重新建立
        channel，而不是复用可能损坏的会话。
        """
        sftp, self._sftp = self._sftp, None
        if sftp is not None:
            with contextlib.suppress(Exception):
                sftp.close()

    def _sftp_timeout_error(self, description: str, effective: float) -> SSHFileTransferError:
        """构造并返回超时错误（由调用方 ``raise ... from e`` 触发清理链）。"""
        self._discard_sftp()
        return SSHFileTransferError(
            f"{description} timed out after {effective:g} seconds of inactivity"
        )

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

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
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

    def _start_command_timeout(
        self, timeout: Optional[int]
    ) -> tuple[threading.Event, Optional[threading.Timer], list[Any], Optional[float]]:
        """在 exec request 前启动 wall-clock watchdog。"""
        if timeout is not None and timeout <= 0:
            raise ValidationError(f"timeout must be > 0, got: {timeout}")
        timed_out = threading.Event()
        channel_ref: list[Any] = []
        deadline = None if timeout is None else time.monotonic() + timeout
        if timeout is None:
            return timed_out, None, channel_ref, None

        def _on_timeout() -> None:
            timed_out.set()
            if channel_ref:
                self._close_command_channel(channel_ref[0])
                return
            # Paramiko's exec request can block before SSHClient.exec_command
            # returns the channel. Closing the transport is the only available
            # cancellation primitive during that setup window.
            if self._client is not None:
                with contextlib.suppress(Exception):
                    self._client.close()

        timer = threading.Timer(timeout, _on_timeout)
        timer.daemon = True
        timer.start()
        return timed_out, timer, channel_ref, deadline

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
                超时后关闭通道（channel 尚未建立时关闭 transport）并抛出
                SSHCommandTimeoutError。
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

            # Timer 覆盖 channel/exec 请求、输出排空和 exit-status 等待；
            # 否则 Paramiko 的 exec request 或 recv_exit_status 可永久挂起。
            timed_out, timer, channel_ref, deadline = self._start_command_timeout(timeout)
            try:
                stdin, stdout, stderr = self._client.exec_command(
                    full_command,
                    timeout=timeout,
                )
                channel_ref.append(stdout.channel)

                # 获取命令执行结果（先排空两流，再取退出状态）。
                exit_code, stdout_data, stderr_data = self._read_output(
                    stdout,
                    stderr,
                    timeout,
                    timed_out_event=timed_out,
                    manage_timeout=False,
                    deadline=deadline,
                )
            finally:
                if timer is not None:
                    timer.cancel()

            # 构建结果对象
            result = CommandResult(
                command=command,
                stdout=stdout_data,
                stderr=stderr_data,
                exit_code=exit_code,
            )

            logger.debug(f"command finished, exit code: {exit_code}")
            return result

        except SSHCommandTimeoutError:
            raise
        except (paramiko.SSHException, OSError) as e:
            if timeout is not None and (timed_out.is_set() or isinstance(e, socket.timeout)):
                raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds") from e
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
        timed_out, timer, channel_ref, deadline = self._start_command_timeout(timeout)
        try:
            full_command = f"sudo -S {command}"
            # get_pty=False：避免 PTY 合并 stdout/stderr（与文档"独立分离"一致），
            # 同时关闭 PTY echo 防止 sudo 密码被回显到 stdout 造成凭据泄露
            stdin, stdout, stderr = self._client.exec_command(
                full_command,
                get_pty=False,
                timeout=timeout,
            )
            channel_ref.append(stdout.channel)
            stdin.write(password + "\n")
            stdin.flush()

            # 与 execute 一致：先并发排空两流（防大输出死锁），再取退出状态；
            # watchdog 从 exec request 前开始计时，覆盖密码写入和 exit-status。
            exit_code, stdout_data, stderr_data = self._read_output(
                stdout,
                stderr,
                timeout,
                timed_out_event=timed_out,
                manage_timeout=False,
                deadline=deadline,
            )

            return CommandResult(
                command=command,
                stdout=stdout_data,
                stderr=stderr_data,
                exit_code=exit_code,
            )
        except SSHCommandTimeoutError:
            raise
        except (paramiko.SSHException, OSError) as e:
            if timeout is not None and (timed_out.is_set() or isinstance(e, socket.timeout)):
                raise SSHCommandTimeoutError(f"command timed out after {timeout} seconds") from e
            raise SSHCommandError(f"sudo command execution failed: {e}") from e
        finally:
            if timer is not None:
                timer.cancel()

    # ========================================================================
    # 文件传输方法
    # ========================================================================

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        timeout: Optional[float] = None,
    ) -> None:
        """
        上传本地文件到远程服务器

        Args:
            local_path: 本地文件路径
            remote_path: 远程目标路径（绝对路径）
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``。通道静默超过该时长即中止

        Raises:
            SSHFileTransferError: 文件传输失败或超时时抛出
            SSHConnectionError: 未连接时抛出

        Example:
            >>> client.upload_file("./script.sh", "/home/user/script.sh")
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)

        # 验证本地文件存在
        local_file = Path(local_path)
        if not local_file.exists():
            raise SSHFileTransferError(f"Local file not found: {local_path}")

        try:
            target_path, existing_mode, destination_existed = _resolve_remote_upload_target(
                sftp, remote_path
            )
        except (paramiko.SSHException, OSError, EOFError) as e:
            self._discard_sftp()
            raise SSHFileTransferError(f"file upload failed: {e}") from e

        # Keep the in-progress file in a private same-directory container. The
        # directory's 0700 mode protects partial sensitive uploads from other
        # remote users until the complete file is committed.
        staging_dir = _remote_temporary_path(target_path, "tmpdir")
        staged_path = str(PurePosixPath(staging_dir) / "upload")
        try:
            logger.info(f"uploading file: {local_path} -> {remote_path}")
            sftp.mkdir(staging_dir, mode=0o700)
            sftp.put(str(local_file), staged_path)
            _commit_remote_upload(
                sftp,
                staged_path,
                target_path,
                existing_mode,
                destination_existed,
            )
            try:
                sftp.rmdir(staging_dir)
            except Exception:  # noqa: BLE001 - final file is already committed
                self._discard_sftp()
                logger.warning("upload succeeded but private staging directory cleanup failed")
            logger.info("file upload finished")
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("file upload", effective) from e
        except (paramiko.SSHException, OSError, EOFError) as e:
            with contextlib.suppress(Exception):
                sftp.remove(staged_path)
            with contextlib.suppress(Exception):
                sftp.rmdir(staging_dir)
            self._discard_sftp()
            raise SSHFileTransferError(f"file upload failed: {e}") from e
        except BaseException:
            with contextlib.suppress(Exception):
                sftp.remove(staged_path)
            with contextlib.suppress(Exception):
                sftp.rmdir(staging_dir)
            self._discard_sftp()
            raise

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        timeout: Optional[float] = None,
    ) -> None:
        """
        从远程服务器下载文件到本地

        Args:
            remote_path: 远程文件路径
            local_path: 本地目标路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``。通道静默超过该时长即中止

        Raises:
            SSHFileTransferError: 文件传输失败或超时时抛出
            SSHConnectionError: 未连接时抛出

        Note:
            如果本地目录不存在，将自动创建

        Example:
            >>> client.download_file("/var/log/syslog", "./logs/syslog")
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)

        # 确保本地目录存在
        local_file = Path(local_path)
        try:
            local_file.parent.mkdir(parents=True, exist_ok=True)
            staged_file = _create_download_temp(local_file)
        except OSError as e:
            raise SSHFileTransferError(f"file download failed: {e}") from e

        # 执行下载
        try:
            logger.info(f"downloading file: {remote_path} -> {local_path}")
            sftp.get(remote_path, str(staged_file))
            os.replace(staged_file, local_file)
            logger.info("file download finished")
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("file download", effective) from e
        except (paramiko.SSHException, OSError, EOFError) as e:
            self._discard_sftp()
            raise SSHFileTransferError(f"file download failed: {e}") from e
        except BaseException:
            self._discard_sftp()
            raise
        finally:
            with contextlib.suppress(OSError):
                staged_file.unlink()

    def list_remote_directory(
        self,
        remote_path: str = ".",
        timeout: Optional[float] = None,
    ) -> list[RemoteFileEntry]:
        """
        列出远程目录内容

        Args:
            remote_path: 远程目录路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``

        Returns:
            List[RemoteFileEntry]: 目录项信息列表

        Raises:
            SSHFileTransferError: 列出目录失败或超时时抛出
            SSHConnectionError: 未连接时抛出

        Example:
            >>> entries = client.list_remote_directory("/home/user")
            >>> for entry in entries:
            ...     print(f"{entry.name}: {entry.size} bytes")
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)

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
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("list remote directory", effective) from e
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to list remote directory: {e}") from e

    def create_remote_directory(self, path: str, timeout: Optional[float] = None) -> None:
        """创建远程目录（支持递归创建）

        Args:
            path: 远程目录路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)

        def _makedirs(sftp_client: paramiko.SFTPClient, remote_path: str) -> None:
            # 远端路径始终是 POSIX 语义，必须用 PurePosixPath 处理，
            # 不能用本地 Path（在 Windows 上 WindowsPath 对 "/" 的处理会导致无限递归）。
            p = PurePosixPath(remote_path)
            if p == PurePosixPath("/") or p == PurePosixPath("."):
                return
            try:
                sftp_client.stat(str(p))
            except (socket.timeout, TimeoutError):
                raise
            except OSError:
                _makedirs(sftp_client, str(p.parent))
                sftp_client.mkdir(str(p))

        try:
            _makedirs(sftp, path)
            logger.info(f"created remote directory: {path}")
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("create remote directory", effective) from e
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to create remote directory: {e}") from e

    def remove_remote_file(self, path: str, timeout: Optional[float] = None) -> None:
        """删除远程文件

        Args:
            path: 远程文件路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)
        try:
            sftp.remove(path)
            logger.info(f"deleted remote file: {path}")
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("delete remote file", effective) from e
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to delete remote file: {e}") from e

    def remove_remote_directory(
        self,
        path: str,
        recursive: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """删除远程目录

        Args:
            path: 远程目录路径
            recursive: 是否递归删除目录内容
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)

        def _rm_recursive(sftp_client: paramiko.SFTPClient, remote_path: str) -> None:
            """递归删除目录内容，先收集后删除以避免不一致状态"""
            entries: list[tuple[str, bool]] = []
            try:
                for entry in sftp_client.listdir_attr(remote_path):
                    entries.append((entry.filename, bool(entry.st_mode & stat.S_IFDIR)))
            except (socket.timeout, TimeoutError):
                raise
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
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("delete remote directory", effective) from e
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to delete remote directory: {e}") from e

    def remote_file_exists(self, path: str, timeout: Optional[float] = None) -> bool:
        """检查远程文件是否存在。

        Args:
            path: 远程路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``

        Note:
            超时视为"无法确认存在"（返回 False），并丢弃失步的 SFTP 会话。
        """
        try:
            sftp = self._get_sftp(timeout)
            sftp.stat(path)
            return True
        except (socket.timeout, TimeoutError):
            self._discard_sftp()
            return False
        except OSError:
            return False
        except SSHConnectionError:
            return False

    def get_remote_file_info(self, path: str, timeout: Optional[float] = None) -> dict[str, Any]:
        """获取远程文件信息

        Args:
            path: 远程路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``
        """
        effective = self._effective_sftp_timeout(timeout)
        sftp = self._get_sftp(effective)
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
        except (socket.timeout, TimeoutError) as e:
            raise self._sftp_timeout_error("get remote file info", effective) from e
        except (paramiko.SSHException, OSError) as e:
            raise SSHFileTransferError(f"failed to get file info: {e}") from e
