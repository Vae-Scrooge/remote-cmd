# 更新日志

所有 notable 的更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/).

## [Unreleased]

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
