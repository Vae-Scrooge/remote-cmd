"""
原生异步 SSH 客户端（基于 asyncssh 实现）

本模块直接使用 asyncssh 原生 async/await API，可避免线程池开销，在大规模并发
场景下显著降低 CPU 与线程占用。它是项目中唯一的 AsyncSSHClient 实现
（旧的 run_in_executor 包装 Paramiko 版本已在 P2 合并中移除）。

设计目标：
- 对外 API 与同步 `SSHClient` 保持一致（connect / disconnect / execute /
  execute_sudo / upload_file / download_file / list_remote_directory）。
- 复用 `ConnectionConfig` / `CommandResult` 数据契约，保证与同步 SSHClient 行为对齐。
- 安全：默认使用 known_hosts 校验；未显式提供时回退到 asyncssh 的默认策略
  （与同步实现 RejectPolicy 等价），仅在配置显式开启 AutoAdd 时才放宽。
- 密码、密钥等敏感信息不入日志（沿用项目 SensitiveDataFilter 规范）。
"""

import logging
import shlex
import stat
from pathlib import Path
from typing import Any, Optional

import asyncssh

from remote_cmd.core.ssh_client import CommandResult, ConnectionConfig
from remote_cmd.utils.exceptions import (
    SSHCommandError,
    SSHConnectionError,
    SSHFileTransferError,
)

logger = logging.getLogger(__name__)


class AsyncSSHClient:
    """基于 asyncssh 的原生异步 SSH 客户端。

    对外接口与同步 `SSHClient` 一致，是项目中唯一的异步 SSH 客户端实现。

    Args:
        config: SSH 连接配置
        loop: 可选事件循环（已忽略；asyncssh 自行从当前事件循环获取，保留参数仅为
            向后兼容）

    Note:
        本类不持有任何线程池，真正在事件循环上完成 I/O，可与其他 asyncssh 连接
        并发复用同一事件循环。
    """

    def __init__(
        self,
        config: ConnectionConfig,
        loop: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._sftp: Optional[asyncssh.SFTPClient] = None
        # 保留 loop 入参仅为向后兼容，asyncssh 自行从当前 event loop 取用
        self._loop = loop

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    async def connect(self) -> "AsyncSSHClient":
        """异步建立 SSH 连接。

        Returns:
            AsyncSSHClient: 已连接的客户端实例（支持链式调用）

        Raises:
            SSHConnectionError: 连接/认证失败时抛出，包含原因映射
        """
        if self.is_connected():
            return self

        connect_kwargs: dict[str, Any] = {
            "host": self.config.hostname,
            "port": self.config.port,
            "username": self.config.username,
            "known_hosts": self._build_known_hosts(),
            "login_timeout": self.config.timeout,
        }

        # 认证方式：密码优先，其次密钥，最后交给 asyncssh 默认（含 agent）
        if self.config.password:
            connect_kwargs["password"] = self.config.password
        elif self.config.key_filename:
            key_path = Path(self.config.key_filename).expanduser()
            if not key_path.exists():
                raise SSHConnectionError(f"SSH key file not found: {key_path}")
            connect_kwargs["client_keys"] = [str(key_path)]

        logger.info(f"connecting to {self.config.hostname}:{self.config.port}")
        try:
            self._conn = await asyncssh.connect(**connect_kwargs)
        except asyncssh.PermissionDenied as e:
            raise SSHConnectionError(f"authentication failed: {e}") from e
        except (OSError, asyncssh.Error) as e:
            msg = str(e).lower()
            if (
                "timed out" in msg
                or "timeout" in msg
                or isinstance(e, asyncssh.TimeoutError)
            ):
                raise SSHConnectionError(f"connection timeout: {self.config.hostname}") from e
            raise SSHConnectionError(f"connection error: {e}") from e

        logger.info(f"connected to {self.config.hostname}")
        return self

    def _build_known_hosts(self) -> Any:
        """根据 ConnectionConfig 构建 asyncssh known_hosts 配置。

        策略对齐同步 SSHClient：
        - 若显式提供 known_hosts_file，使用该文件做严格校验
        - 若配置传入 paramiko.AutoAddPolicy 等价信号（通过 host_key_policy 字符串
          'auto' 或 False 判定），自动接受新主机密钥（仅用于测试/受控环境）
        - 默认使用 asyncssh 默认策略（~/.ssh/known_hosts）
        """
        if self.config.known_hosts_file:
            path = Path(self.config.known_hosts_file).expanduser()
            return str(path)
        policy = self.config.host_key_policy
        # 约定：传入字符串 "auto" 视为自动添加（受控场景）
        if isinstance(policy, str) and policy.lower() == "auto":
            # 安全：asyncssh 中 known_hosts=None 会完全跳过主机密钥校验
            # （比 paramiko AutoAddPolicy 更危险，连密钥都不落盘）。
            # asyncssh 不提供等价的 AutoAddPolicy，此处回退到默认 known_hosts
            # 校验并发出警告，避免静默禁用所有 MITM 防护。
            logger.warning(
                "SECURITY WARNING: 'auto' host key policy requested for asyncssh, "
                "but asyncssh has no AutoAddPolicy equivalent. Falling back to "
                "default known_hosts verification (~/.ssh/known_hosts). "
                "Pre-load host keys or set known_hosts_file to trust specific hosts."
            )
            return ()
        # 默认交由 asyncssh 处理用户 ~/.ssh/known_hosts
        return ()

    async def disconnect(self) -> None:
        """异步断开 SSH 连接并清理 SFTP 资源。即使连接已断开也能安全调用。"""
        if self._sftp is not None:
            try:
                # asyncssh SFTPClient.exit() 是同步方法，仅关闭通道资源
                self._sftp.exit()
            except (OSError, asyncssh.Error) as e:
                logger.warning(f"error closing SFTP connection: {e}")
            finally:
                self._sftp = None

        if self._conn is not None:
            try:
                self._conn.close()
                # await close 完成底层通道清理，但忽略可能抛出的 ConnectionLost / asyncssh.Error
                await self._conn.wait_closed()
            except (OSError, asyncssh.Error) as e:
                logger.warning(f"error closing SSH connection: {e}")
            finally:
                self._conn = None

    def is_connected(self) -> bool:
        """检查连接是否处于活动状态。"""
        return self._conn is not None and not self._conn.is_closed()

    async def _get_conn(self) -> asyncssh.SSHClientConnection:
        if self._conn is None:
            raise SSHConnectionError("not connected, call connect() first")
        return self._conn

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------
    async def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        environment: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        """异步执行远程命令。

        Args:
            command: 要执行的命令字符串
            timeout: 命令执行超时（秒），None 表示不限
            environment: 命令执行前注入的环境变量

        Returns:
            CommandResult: 命令结果（与同步实现字段一致）

        Raises:
            SSHCommandError: 命令执行失败时抛出
            SSHConnectionError: 未连接时抛出
        """
        conn = await self._get_conn()
        # 安全：对 value 做 shlex.quote 转义，防止 shell 元字符注入
        env_str = ""
        if environment:
            env_str = (
                "; ".join(f"export {k}={shlex.quote(str(v))}" for k, v in environment.items())
                + "; "
            )
        full_command = f"{env_str}cd ~ && {command}"
        logger.debug(f"executing command: {command}")
        try:
            result = await conn.run(
                full_command,
                timeout=timeout,
                check=False,
                env={k: str(v) for k, v in (environment or {}).items()},
            )
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"command execution failed '{command}': {e}") from e

        stdout_data = result.stdout if isinstance(result.stdout, str) else (
            result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        )
        stderr_data = result.stderr if isinstance(result.stderr, str) else (
            result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        )
        exit_code = int(result.exit_status) if result.exit_status is not None else -1

        return CommandResult(
            command=command,
            stdout=stdout_data,
            stderr=stderr_data,
            exit_code=exit_code,
        )

    async def execute_sudo(
        self,
        command: str,
        password: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> CommandResult:
        """以 sudo 权限异步执行命令（安全实现：密码通过 stdin 传入，不进入进程列表）。"""
        conn = await self._get_conn()
        if password is None:
            return await self.execute(f"sudo {command}", timeout=timeout)

        try:
            proc: asyncssh.SSHClientProcess = await conn.create_process(
                f"sudo -S {command}",
                timeout=timeout,
            )
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"sudo command execution failed '{command}': {e}") from e

        try:
            proc.stdin.write(password + "\n")
            proc.stdin.write_eof()
            result = await proc.wait()
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"sudo command execution failed '{command}': {e}") from e

        stdout_data = (
            result.stdout if isinstance(result.stdout, str)
            else (result.stdout.decode("utf-8", errors="replace") if result.stdout else "")
        )
        stderr_data = (
            result.stderr if isinstance(result.stderr, str)
            else (result.stderr.decode("utf-8", errors="replace") if result.stderr else "")
        )
        return CommandResult(
            command=command,
            stdout=stdout_data,
            stderr=stderr_data,
            exit_code=int(result.exit_status) if result.exit_status is not None else -1,
        )

    # ------------------------------------------------------------------
    # SFTP / 文件传输
    # ------------------------------------------------------------------
    async def _get_sftp(self) -> asyncssh.SFTPClient:
        conn = await self._get_conn()
        if self._sftp is None:
            try:
                self._sftp = await conn.start_sftp_client()
            except (OSError, asyncssh.Error) as e:
                raise SSHFileTransferError(f"failed to open SFTP channel: {e}") from e
        return self._sftp

    async def upload_file(self, local_path: str, remote_path: str) -> None:
        """异步上传本地文件到远程服务器。"""
        sftp = await self._get_sftp()
        local_file = Path(local_path)
        if not local_file.exists():
            raise SSHFileTransferError(f"Local file not found: {local_path}")
        logger.info(f"uploading file: {local_path} -> {remote_path}")
        try:
            await sftp.put(str(local_file), remote_path)
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"file upload failed: {e}") from e
        logger.info("file upload finished")

    async def download_file(self, remote_path: str, local_path: str) -> None:
        """异步从远程服务器下载文件到本地。"""
        sftp = await self._get_sftp()
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"downloading file: {remote_path} -> {local_path}")
        try:
            await sftp.get(remote_path, str(local_file))
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"file download failed: {e}") from e
        logger.info("file download finished")

    async def list_remote_directory(self, remote_path: str = ".") -> list[dict[str, Any]]:
        """异步列出远程目录内容（结构与同步 SSHClient 一致）。"""
        sftp = await self._get_sftp()
        try:
            names = await sftp.readdir(remote_path)
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"failed to list remote directory: {e}") from e

        entries: list[dict[str, Any]] = []
        for entry in names:
            attrs = entry.attrs
            mode = attrs.permissions if hasattr(attrs, "permissions") else None
            entries.append(
                {
                    "name": entry.filename,
                    "size": attrs.size if hasattr(attrs, "size") else 0,
                    "mode": oct(int(mode))[-3:] if mode else "000",
                    "mtime": attrs.mtime if hasattr(attrs, "mtime") else 0,
                    "is_dir": bool(mode & stat.S_IFDIR) if mode else False,
                }
            )
        return entries

    # ------------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "AsyncSSHClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()


__all__ = ["AsyncSSHClient"]
