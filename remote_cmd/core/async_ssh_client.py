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

import asyncio
import contextlib
import logging
import shlex
import stat
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Optional

import asyncssh

from remote_cmd.core.ssh_client import (
    CommandResult,
    ConnectionConfig,
    RemoteFileEntry,
    validate_environment,
)
from remote_cmd.utils.exceptions import (
    SSHAuthenticationError,
    SSHCommandError,
    SSHConnectionError,
    SSHFileTransferError,
    SSHTimeoutError,
    ValidationError,
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
            # 永久性错误：重试同一凭据只会加剧账号锁定（见 service/retry_policy.py）
            raise SSHAuthenticationError(f"authentication failed: {e}") from e
        except (OSError, asyncssh.Error) as e:
            msg = str(e).lower()
            if "timed out" in msg or "timeout" in msg or isinstance(e, asyncssh.TimeoutError):
                raise SSHTimeoutError(f"connection timeout: {self.config.hostname}") from e
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
        # 安全：键必须为合法 shell 标识符（与同步实现一致，防止命令注入）
        validate_environment(environment)
        # 安全：对 value 做 shlex.quote 转义，防止 shell 元字符注入
        env_str = ""
        if environment:
            env_str = (
                "; ".join(f"export {k}={shlex.quote(str(v))}" for k, v in environment.items())
                + "; "
            )
        full_command = f"{env_str}cd ~ && {command}"
        # 安全：不记录命令全文（可能含敏感参数），仅记录执行事件
        logger.debug("executing remote command")
        try:
            # 环境变量仅通过命令前缀的 export 注入（与同步 SSHClient 行为
            # 一致）：conn.run(env=...) 依赖服务端 AcceptEnv 且语义分叉，
            # 不再重复传递
            result = await conn.run(
                full_command,
                timeout=timeout,
                check=False,
            )
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"command execution failed: {e}") from e

        stdout_data = (
            result.stdout
            if isinstance(result.stdout, str)
            else (result.stdout.decode("utf-8", errors="replace") if result.stdout else "")
        )
        stderr_data = (
            result.stderr
            if isinstance(result.stderr, str)
            else (result.stderr.decode("utf-8", errors="replace") if result.stderr else "")
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
            proc: asyncssh.SSHClientProcess[bytes] = await conn.create_process(
                f"sudo -S {command}",
                timeout=timeout,
            )
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"sudo command execution failed: {e}") from e

        try:
            proc.stdin.write((password + "\n").encode("utf-8"))
            proc.stdin.write_eof()
            # 与 execute 的 conn.run(timeout=...) 语义对齐：timeout 覆盖整个命令执行
            # wall-clock，避免挂起的 sudo（如等待密码）无限等待
            result = await proc.wait(timeout=timeout)
        except (OSError, asyncssh.Error) as e:
            raise SSHCommandError(f"sudo command execution failed: {e}") from e

        stdout_data = (
            result.stdout
            if isinstance(result.stdout, str)
            else (result.stdout.decode("utf-8", errors="replace") if result.stdout else "")
        )
        stderr_data = (
            result.stderr
            if isinstance(result.stderr, str)
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
    async def _get_sftp(self, timeout: Optional[float] = None) -> asyncssh.SFTPClient:
        """获取 SFTP 客户端（延迟初始化）。

        v2.3：打开 SFTP channel 本身也受有效超时约束，避免握手阶段永久挂起。
        """
        conn = await self._get_conn()
        if self._sftp is None:
            effective = self._effective_sftp_timeout(timeout)
            try:
                self._sftp = await asyncio.wait_for(conn.start_sftp_client(), timeout=effective)
            except asyncio.TimeoutError as e:
                raise SSHFileTransferError(
                    f"failed to open SFTP channel: timed out after {effective:g} seconds"
                ) from e
            except (OSError, asyncssh.Error) as e:
                raise SSHFileTransferError(f"failed to open SFTP channel: {e}") from e
        return self._sftp

    def _effective_sftp_timeout(self, timeout: Optional[float]) -> float:
        """返回 SFTP 操作的有效 inactivity 超时（秒）。

        显式 timeout 优先，否则回退到 ``ConnectionConfig.timeout``。
        """
        if timeout is not None:
            if timeout <= 0:
                raise ValidationError(f"timeout must be > 0, got: {timeout}")
            return timeout
        return self.config.timeout

    def _discard_sftp(self) -> None:
        """关闭并丢弃缓存的 SFTP 会话（超时后防止复用可能失步的会话）。"""
        sftp, self._sftp = self._sftp, None
        if sftp is not None:
            with contextlib.suppress(Exception):
                sftp.exit()

    async def _run_sftp_operation(
        self,
        operation: Callable[[Callable[..., None]], Awaitable[Any]],
        timeout: Optional[float],
        description: str,
    ) -> Any:
        """运行 SFTP 操作并施加 inactivity（静默）超时。

        asyncssh 的 SFTP API 没有原生超时参数，因此使用 watchdog：
        ``operation`` 收到一个 progress 回调（传给 ``put``/``get`` 的
        ``progress_handler``），每次有数据进展就刷新时间戳；超过有效超时
        没有任何进展则取消操作、丢弃失步的 SFTP 会话并抛出
        ``SSHFileTransferError``。只要数据持续流动，长时间传输不会被打断。
        """
        effective = self._effective_sftp_timeout(timeout)
        loop = asyncio.get_running_loop()
        last_activity = loop.time()

        def _mark_progress(*_args: Any) -> None:
            nonlocal last_activity
            last_activity = loop.time()

        task = asyncio.ensure_future(operation(_mark_progress))
        interval = min(0.5, max(effective / 10.0, 0.01))
        cancelled: Optional[asyncio.CancelledError] = None
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    return task.result()
                if loop.time() - last_activity >= effective:
                    break
        except asyncio.CancelledError as exc:
            # 外层取消：记录后统一走清理路径（不在此处 await，避免
            # 取消事件再次进入本处理器）
            cancelled = exc

        # 统一清理：立即丢弃可能失步的 SFTP 会话；取消并等待子任务收尾。
        # shield 确保清理期间到达的再次取消只打断"等待"本身，而不会
        # 把清理过程转换成超时错误或被整体吞掉。
        self._discard_sftp()
        task.cancel()
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                # 清理期间外层再次取消：保留取消语义
                raise
        except Exception:  # noqa: BLE001 - 子任务失败不应掩盖超时/取消语义
            pass

        if cancelled is not None:
            raise cancelled
        raise SSHFileTransferError(
            f"{description} timed out after {effective:g} seconds of inactivity"
        )

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
        timeout: Optional[float] = None,
    ) -> None:
        """异步上传本地文件到远程服务器。

        Args:
            local_path: 本地文件路径
            remote_path: 远程目标路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``。静默超过该时长即中止
        """
        sftp = await self._get_sftp(timeout)
        local_file = Path(local_path)
        if not local_file.exists():
            raise SSHFileTransferError(f"Local file not found: {local_path}")
        logger.info(f"uploading file: {local_path} -> {remote_path}")

        async def _operation(progress: Callable[..., None]) -> None:
            await sftp.put(str(local_file), remote_path, progress_handler=progress)

        try:
            await self._run_sftp_operation(_operation, timeout, "file upload")
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"file upload failed: {e}") from e
        logger.info("file upload finished")

    async def download_file(
        self,
        remote_path: str,
        local_path: str,
        timeout: Optional[float] = None,
    ) -> None:
        """异步从远程服务器下载文件。

        Args:
            remote_path: 远程文件路径
            local_path: 本地目标路径
            timeout: inactivity 超时（秒）；None 表示使用
                ``ConnectionConfig.timeout``。静默超过该时长即中止
        """
        sftp = await self._get_sftp(timeout)
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"downloading file: {remote_path} -> {local_path}")

        async def _operation(progress: Callable[..., None]) -> None:
            await sftp.get(remote_path, str(local_file), progress_handler=progress)

        try:
            await self._run_sftp_operation(_operation, timeout, "file download")
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"file download failed: {e}") from e
        logger.info("file download finished")

    async def list_remote_directory(
        self,
        remote_path: str = ".",
        timeout: Optional[float] = None,
    ) -> list[RemoteFileEntry]:
        """异步列出远程目录内容（结构与同步 SSHClient 一致）。"""
        sftp = await self._get_sftp(timeout)

        async def _operation(_progress: Callable[..., None]) -> Sequence[Any]:
            return await sftp.readdir(remote_path)

        try:
            names = await self._run_sftp_operation(_operation, timeout, "list remote directory")
        except (OSError, asyncssh.Error) as e:
            raise SSHFileTransferError(f"failed to list remote directory: {e}") from e

        entries: list[RemoteFileEntry] = []
        for entry in names:
            attrs = entry.attrs
            mode = attrs.permissions if hasattr(attrs, "permissions") else None
            raw_size = attrs.size if hasattr(attrs, "size") else None
            raw_mtime = attrs.mtime if hasattr(attrs, "mtime") else 0
            entries.append(
                RemoteFileEntry(
                    name=str(entry.filename),
                    size=raw_size if raw_size is not None else 0,
                    mode=oct(int(mode))[-3:] if mode else "000",
                    mtime=raw_mtime,
                    is_dir=bool(mode & stat.S_IFDIR) if mode else False,
                )
            )
        return entries

    # ------------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "AsyncSSHClient":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.disconnect()


__all__ = ["AsyncSSHClient"]
