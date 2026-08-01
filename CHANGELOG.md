# 更新日志

所有 notable 的更改都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/).

## [Unreleased]

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
- 发布到 PyPI，支持 `pip install remote-cmd`
- 添加 PyPI 版本和下载量 badge
- README 添加英文简介，方便海外用户
- setup.py 添加 PyPI 下载链接和项目 URL

## [1.0.0] - 2024-01-15

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
