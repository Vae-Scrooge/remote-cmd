# 更新日志

所有 notable 的更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/).

## [Unreleased]

## [2.1.0] - 2026-08-22

本版本包含两层范围：未特别标注的 `Added` / `Changed` / `Fixed` / `Security` 条目记录
v2.1.0 的主要实现变更（Paramiko 可靠性、异步连接池、重试策略、异常模型、安全与 CLI）；
标注 **[发布加固]** 的条目记录发布前最终加固，不是“仅文档变更”。

### Added

- **AsyncConnectionPool 正式接入 AsyncBatchExecutor**（此前该池在生产路径零消费，异步内核每次
  尝试都新建连接）：多主机或需重试时内部按主机创建池、跨重试复用连接、批结束后自动
  `close_all`，与同步 `BatchExecutor` 的 `SyncConnectionPool` 行为完全对齐。
- `AsyncBatchExecutor` / `BatchExecutor` 新增 `pool_factory` 构造参数（外部连接池注入）：
  提供时执行器从工厂获取池并复用连接，**绝不关闭**（所有权归调用方，适合长驻服务跨批次
  复用）；内部创建的池仍由执行器在单次 `execute` 结束后自动关闭。
- 新增重试策略模块 `remote_cmd.service.retry_policy`（`is_retryable` / `compute_backoff_delay`）：
  显式区分瞬态（可重试）与永久性（绝不重试）错误；指数退避 + full jitter。
- 异常层次新增细分类型（保留既有父类捕获行为，向后兼容）：
  `SSHAuthenticationError(SSHConnectionError)`、`SSHTimeoutError(SSHConnectionError)`、
  `SSHCommandTimeoutError(SSHCommandError)`、`CredentialError(RemoteCmdError)`、
  `ConfigurationError`（`ConfigError` 别名）；`CredentialEncryptionError` 改为继承
  `CredentialError`（归入 `RemoteCmdError` 层级，既有导入路径不变）。
- `AsyncConnectionPool` 新增 `client_factory` 参数（与 `SyncConnectionPool` 对齐，测试可注入）。
- CLI `run` 命令新增 `--timeout/-T` 选项（此前未提供命令执行超时，挂起的远端命令会永久阻塞 CLI）。
- `SSHClient` / `AsyncSSHClient` 环境变量注入新增键名校验（`validate_environment`）。

### Changed

- **重试语义收紧**（`BatchExecutor` / `AsyncBatchExecutor`）：认证、凭据、配置、校验及
  编程错误（`ValueError` / `TypeError` / `KeyError` / `RuntimeError`）立即失败不再重试；
  未识别的 `Exception` 保持历史可重试行为（兼容注入自定义 client_factory 的调用方）。
- **重试等待改为指数退避 + full jitter**：`retry_delay` 语义由"固定间隔"变为"基础延迟"，
  第 n 次失败后等待 `0` 到 `min(60s, retry_delay * 2^n)`（含端点）内的随机值，避免多主机同步重试的
  惊群效应。
- `SSHClient.execute` / `execute_sudo` 的 `timeout` 明确为 **wall-clock 语义**（与
  `AsyncSSHClient` 的 `conn.run(timeout=...)` 对齐）：超时后关闭通道终止远端命令并抛出
  `SSHCommandTimeoutError`。此前 timeout 对静默挂起的命令完全不生效（永久阻塞）。
- `AsyncSSHClient.execute` 不再通过 `conn.run(env=...)` 重复注入环境变量（该路径依赖
  服务端 `AcceptEnv` 且与同步实现语义分叉），统一仅保留命令前缀 `export` 注入。
- **[发布加固] `BatchExecutor(use_async=True)` 在运行中的事件循环内调用**时，抛出可操作
  的项目级 `RuntimeError`（提示改用 `AsyncBatchExecutor.execute()` 直接调用），替代
  `asyncio.run` 的通用 Python 错误；其余来源的 `RuntimeError` 不受影响。

### Fixed

- **[Critical] Paramiko 大输出死锁**：`SSHClient.execute` / `execute_sudo` 此前在读取输出流
  之前调用 `recv_exit_status()`——当命令输出超过 SSH 通道窗口（默认 2MB）时远端阻塞写入、
  命令永不退出、调用永久挂起（paramiko 官方文档明确警告的场景）。现在 stderr 由后台线程
  排空、stdout 在当前线程读取，两流并发消费后获取退出状态。
- **[High] 多主机批次含未知主机时 `execute` 抛 `KeyError`**：`_prepare_pool` 在池准备阶段
  解析主机失败会上抛异常导致整批失败；现在返回 None 并由单主机路径记录
  "host not found" 错误条目（契约与单主机路径一致）。
- **[发布加固] 连接池关闭竞态**：`SyncConnectionPool.acquire` / `AsyncConnectionPool.acquire`
  阻塞在信号量期间 `close_all()` 完成时，取得槽位后现在复查关闭状态——归还槽位并抛出
  既有 `RuntimeError("connection pool is closed")`，而不是从已关闭的池发放游离连接。
- **[发布加固] `SSHClient._read_output` 的 stderr 排空线程 join 改为有界**（5 秒）：
  极端场景下（超时回调中 `channel.close()` 失败，或主线程读取异常退出而通道仍打开），
  排空线程可能仍阻塞——无界 join 会让调用方永久挂起。

### Security

- 环境变量**键名**注入防护：值虽经 `shlex.quote` 转义，但键直接拼入 `export {k}=...`
  命令前缀，含 shell 元字符的键（如 `A; malicious`）可造成命令注入；现在键必须匹配
  `[A-Za-z_][A-Za-z0-9_]*`，否则抛 `ValidationError`（拼接前拒绝）。
- `ConnectionConfig` 文档修正：默认主机密钥策略实为 `RejectPolicy`（原文档误写
  `WarningPolicy`）。

### 迁移说明（v2.0 → v2.1）

- **异常**：既有异常名称与导入路径保持不变，新增 SSH 异常均为既有类型的子类（或别名），
  因此 `except SSHConnectionError` / `except SSHCommandError` / `except Exception` 等既有
  捕获行为完全兼容；需要精细处理时可改为捕获新子类。例外是
  `CredentialEncryptionError` 现在额外继承 `CredentialError`，以归入统一的凭据异常层级，
  但原有 `except CredentialEncryptionError` 捕获仍有效。
- **重试**：依赖"认证失败也会重试"的调用方（不推荐）会观察到认证错误现在只执行一次；
  依赖固定重试间隔的调用方会观察到间隔变为指数退避随机值；第 n 次失败后的期望等待时间
  约为 `min(60s, retry_delay * 2^n) / 2`。未知的 `Exception` 子类仍保持可重试，以兼容
  自定义 `client_factory`；自定义实现应优先抛出类型化的 `remote_cmd` 异常以获得精确分类。
- **超时**：`SSHClient.execute(cmd, timeout=N)` 对静默挂起命令从"永久阻塞"变为
  "N 秒后抛 `SSHCommandTimeoutError`"——这是超时参数的文档语义，此前是缺陷。
- **环境变量**：`execute(..., environment={"bad key": v})` 此前会拼出损坏的 shell 命令，
  现在抛 `ValidationError`。
- **连接池**：多主机或重试批次现在会复用 `AsyncConnectionPool` / `SyncConnectionPool`；
  `pool_factory` 提供的外部池由调用方负责生命周期，执行器绝不关闭，内部创建的池则在
  `execute()` 结束后自动关闭。
- **事件循环**：`BatchExecutor(use_async=True)` 不能在活动事件循环内调用；请在该场景
  直接 `await AsyncBatchExecutor.execute()`。

## [2.0.0] - 2026-08-13

### Added

- `BatchExecutor` 新增 `use_async: bool = False` 构造参数：开启后同步 `execute` 内部切换到
  既有的 `AsyncBatchExecutor` 原生异步内核（asyncssh），以在大规模并发下降低线程/CPU 开销。
  新增 CLI `--async` 开关透传。对外 `execute` 签名与返回类型不变，便于上层无差别切换。
  调用方须确保开启时当前线程未运行 asyncio 事件循环。
- CLI `--async`/`use_async` 透传已有单元测试覆盖（`TestBatchExecutorUseAsyncSwitch`）。

### Changed

- 安全：`SSHClient` / `AsyncSSHClient` 的命令执行不再将命令全文写入 debug 日志
  （改为记录无命令的执行事件）；命令执行失败异常消息去掉命令明文，仅保留失败原因。
  防止命令中的敏感参数（密码、token 等）进入日志或异常链。
- 修复：`AsyncSSHClient.execute_sudo` 的 `timeout` 现在覆盖整个命令执行 wall-clock
  （`proc.wait(timeout=...)`），与 `execute` 的 `conn.run(timeout=...)` 语义对齐；
  避免挂起的 sudo（如等待密码）无限等待。未传 timeout 时行为不变（无限等待）。
- 校验：`BatchExecutor` / `AsyncBatchExecutor` 构造参数增加守卫——`max_concurrency` 必须
  `>= 1`、`command_timeout` 必须 `> 0`；`execute` 的 `retry_count` 必须 `>= 0`、
  `retry_delay` 必须 `>= 0`，非法值抛 `ValueError`（此前非法值会在运行时以晦涩错误暴露，
  如 `ThreadPoolExecutor(max_workers=0)` 或 `Semaphore(0)` 死锁）。
- 安全：批量执行开始日志不再包含命令全文（与命令执行脱敏原则一致）。
- 版本号收敛为单一来源：新增轻量无副作用模块 `remote_cmd/_version.py` 作为版本唯一真相源；
  `remote_cmd/__init__.py` 的 `__version__` 改从 `_version` 导入；`pyproject.toml` 的
  `version` 改为动态读取（`[tool.setuptools.dynamic]`）。开发者只需修改 `_version.py` 一处，
  打包自动取用，且 setuptools 解析版本时不会触发 `remote_cmd` 包级 import（避免干净构建
  环境缺依赖时 ImportError）。公共 API（`__all__` 中的 `__version__` 条目）保持不变。
- 修复：`SyncConnectionPool` / `AsyncConnectionPool` 增加生命周期守卫——`close_all()` 之后
  再 `acquire` 抛 `RuntimeError("connection pool is closed")`，`release` 在关闭后直接关闭
  连接而非放回空闲队列（避免游离连接与关闭后池"复活"泄漏）。

### Fixed

- 修复：`BatchExecutor.execute` / `AsyncBatchExecutor.execute` 对 `host_names` 去重
  （保留首次出现顺序）——重复主机名只执行一次。此前重复主机会因 `results` 被后完成结果覆盖，
  导致 `total`/`success`/`failed` 统计错位（如 `["srv1","srv1"]` 报 success=0 但实际成功 1 台）。

### Changed

- 重构：8 步小步增量（详见 `REFACTORING_PLAN.md`），纯结构/类型收紧，现有行为不变，测试 410→424：
  - `core/host.py`：`Host.tags` 收紧为 `list[str] = field(default_factory=list)`（构造器仍兼容
    外部传入 `None` 的历史数据）
  - 新增 `utils/credential_guard.PasswordGuard`：统一 JSON/SQLite 仓库的密码加解密策略
  - 新增 `service/_pool_policy`（`ConnectionMeta` + 纯时间判断）与 `service/_host_runner`、
    `service/_types`：消除 sync/async 连接池与执行器的重复逻辑
  - CLI 命令补齐 `ctx: click.Context` 与返回类型；各模块 `__init__ -> None` 补齐
  - mypy 全量校验通过（31 source files, 0 error）

### Breaking

- `SSHClient.list_remote_directory` 与 `AsyncSSHClient.list_remote_directory` 返回类型由
  `list[dict]` 改为 `list[RemoteFileEntry]`（新增 dataclass）。
  外部调用方需由 `entry["name"]` 改为 `entry.name`。建议随 v2.0 发版。

## [1.2.3] - 2026-08-11

### Added
- `storage_factory.build_repository` 新增 `encryption` 参数并透传至 JSON/SQLite 仓库：
  作为防御深度，即使调用方绕过 `HostService` 直接 `save()` 明文密码，落盘仍是密文
- CLI 构建 `HostService` 时传入 `CredentialEncryption`，开启仓库级加密

### Changed
- `EnvCredentialProvider._host_env_suffix` 补充文档：非字母数字字符统一归一化为
  下划线，`web-1` 与 `web_1` 会映射到同一环境变量（需统一主机命名）

### Fixed
- `tests/integration/conftest.py` 缺失的 `# noqa: ARG002` 标注
- ruff format 全仓库统一格式

## [1.2.2] - 2026-08-06

### Added
- `EnvCredentialProvider` 支持主机专属环境变量（`REMOTE_CMD_PASSWORD_<HOST>`，优先于全局 `REMOTE_CMD_PASSWORD`），避免全局变量被误应用到所有主机
- `SqliteHostRepository` 支持 `encryption` 参数：配置后密码自动加密落库、读取时自动解密

### Changed
- `AsyncConnectionPool._check_connection` 增加空闲 fast-path：连接刚使用过（空闲未超时）时跳过探活，与 `SyncConnectionPool` 行为对齐，减少高并发下的多余往返

### Fixed
- `BatchExecutor` / `AsyncBatchExecutor` 重试全部失败时 `BatchHostResult.duration` 恒为 0，现在保留最后一次尝试的实际耗时
- `JsonHostRepository` / `SqliteHostRepository` 的 `save()` 补充文档警告：不加密直接持久化明文密码须由调用方负责

## [1.2.1] - 2026-08-06

### Fixed
- 恢复被 v1.2.0 意外移除的 `HostManager` 公共 API（作为向后兼容层重新导出，内部委托给 `HostService` + `JsonHostRepository`），避免破坏 `from remote_cmd import HostManager` 的既有兼容性
- 恢复 `tests/test_host_manager.py` 测试覆盖

## [1.1.1] - 2026-08-01

### Fixed
- 修复 `import remote_cmd` 在未安装 asyncssh（`[async]` extra）时直接失败的问题：
  `__init__.py` 顶层无条件导入原生异步模块导致基础安装崩溃，改为 try/except
  优雅降级——未装 asyncssh 时异步符号不导出，同步 API 不受影响

## [1.1.0] - 2026-07-31

### Added
- 原生异步 SSH 客户端 `AsyncSSHClient`（基于 asyncssh），替代线程池包装版本
- `AsyncConnectionPool` 连接池（信号量并发控制、元数据副表、空闲/生命周期回收、健康检查）
- `AsyncBatchExecutor` 原生异步批量执行器（asyncio.Semaphore 并发调度）
- 集成测试框架（paramiko ServerInterface mock SSH server）
- CI 工作流（Python 3.9-3.12 矩阵，uv + ruff + mypy + pytest）
- PyPI auto-publish workflow（release published 触发）
- 文档体系：架构文档、快速入门、高级教程、安全策略、故障排查

### Changed
- 架构合并：删除 executor 包装版 `AsyncSSHClient`，统一为原生 asyncssh 实现
- `ConnectionPool` 统一为 `AsyncConnectionPool`（`ConnectionPool` 保留为向后兼容别名）
- `BatchExecutor` 与 `AsyncBatchExecutor` 契约统一：
  - `HostService._resolve_host` 提升为公共 `resolve_host`
  - 进度回调类型共享 `ProgressCallback`
  - 异步版本补齐 `KeyboardInterrupt` 处理（对齐同步语义）
- 依赖注入类型精确化（`host_service: Any` → `HostService`）
- 安装方式优化：pip install 成为首选安装方式
- README 结构调整与国际化（英文简介、asciinema demo）

### Fixed
- **[P0 安全]** CLI 密码改用 `getpass`（避免 shell 历史泄露）
- **[P0 安全]** 凭据加密格式三重校验防碰撞
- **[P4]** CLI `click.exceptions.Exit` 继承 `RuntimeError` 被 `except Exception` 误捕获
- **[P5]** `ConnectionPool.release` 空闲超时死代码（先刷新 `_last_used` 导致 idle 恒为 0）
- **[P5]** `ConnectionPool.release` `QueueFull` 不可达分支（`await put` 改 `put_nowait`）
- **[P5]** sqlite `_txn()` 连接泄漏
- **[P5]** 凭据链未命中时解密断裂兜底机制
- **[P1]** 13 处 B904 异常链断裂、8 处 E501 行长超限、examples 多处 lint

### Security
- 命令行密码暴露风险修复（改用交互式 getpass）
- 凭据加密格式碰撞风险修复（前缀 + 明文长度 + 加密格式三重校验）

## [1.0.0] - 2026-05-31

### Added
- 发布到 PyPI，支持 `pip install remote_cmd_manager`
- 添加 PyPI 版本和下载量 badge
- README 添加英文简介，方便海外用户
- setup.py 添加 PyPI 下载链接和项目 URL

## [0.1.0] - 2024-01-15 (初始开发版)

### Added
- 初始版本发布
- ✅ SSH 连接管理（密码和密钥认证）
- ✅ 远程命令执行（同步/异步）
- ✅ 文件传输（SFTP 上传/下载）
- ✅ 主机管理系统（JSON 持久化）
- ✅ 标签分类系统
- ✅ 完整的 CLI 工具
- ✅ Python API
- ✅ 上下文管理器支持
- ✅ Sudo 命令执行
- ✅ 连接健康检查
- ✅ 配置管理（YAML/JSON）
- ✅ 完善的错误处理
- ✅ 日志系统
- ✅ 单元测试

### Core Features
- `SSHClient` - SSH 连接客户端
- `HostManager` - 主机管理器
- `ConnectionConfig` - 连接配置
- `CommandResult` - 命令执行结果
- `Host` - 主机配置数据类

### CLI Commands
- `host add` - 添加主机
- `host list` - 列出主机
- `host remove` - 删除主机
- `host test` - 测试连接
- `run` - 执行远程命令
- `upload` - 上传文件
- `download` - 下载文件

### Documentation
- README.md
- API.md
- CONTRIBUTING.md
- TROUBLESHOOTING.md
- LICENSE

---

## 版本说明

### 语义化版本规则

- **MAJOR** - 不兼容的 API 修改
- **MINOR** - 向下兼容的功能新增
- **PATCH** - 向下兼容的问题修复

### 版本标签说明

- `[Unreleased]` - 未发布的更改
- `Added` - 新增功能
- `Changed` - 变更
- `Deprecated` - 弃用
- `Removed` - 移除
- `Fixed` - 修复
- `Security` - 安全相关

---

**查看完整历史：** [GitHub Releases](https://github.com/Vae-Scrooge/remote-cmd/releases)
