<p align="center">
  <img src="https://img.shields.io/pypi/v/remote_cmd_manager?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI" alt="PyPI">
  <img src="https://img.shields.io/pypi/dm/remote_cmd_manager?style=for-the-badge&logo=python&logoColor=white&label=Downloads" alt="Downloads">
  <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=for-the-badge&logo=github" alt="Stars">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/github/license/Vae-Scrooge/remote-cmd?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/github/actions/workflow/status/Vae-Scrooge/remote-cmd/ci.yml?style=for-the-badge&logo=githubactions&label=CI" alt="CI">
</p>

<h1 align="center">Remote CMD — 轻量级 SSH 服务器管理工具<br><small>无需额外开销</small></h1>

<p align="center">
  <a href="./README.md"><img src="https://img.shields.io/badge/English-gray?style=flat-square" alt="English"></a> ·
  <img src="https://img.shields.io/badge/中文-blue?style=flat-square" alt="中文">
</p>

<p align="center">
  <b><code>pip install remote_cmd_manager</code></b> &nbsp;·&nbsp;
  <a href="#快速开始">快速开始</a> &nbsp;·&nbsp;
  <a href="#使用场景">使用场景</a> &nbsp;·&nbsp;
  <a href="#cli-命令参考">CLI 命令参考</a> &nbsp;·&nbsp;
  <a href="#python-api">Python API</a> &nbsp;·&nbsp;
  <a href="#文档">文档</a> &nbsp;·&nbsp;
  <a href="#参与贡献">参与贡献</a>
</p>

<p align="center">
  <a href="https://asciinema.org/a/9yLeYj73muPUuAQY" target="_blank">
    <img src="https://asciinema.org/a/9yLeYj73muPUuAQY.svg" width="720" alt="演示">
  </a>
</p>

---

**Remote CMD** 是一款轻量级的 Python CLI + API 工具，用于通过 SSH 管理服务器。添加主机、执行命令、传输文件、按标签分组管理——无需 Ansible DSL，也不用写 shell 循环。

```bash
# 一条命令即可上手
pip install remote_cmd_manager && remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa && remote-cmd run web-01 "uptime"
```

---

## 目录

- [为什么选择 Remote CMD？](#为什么选择-remote-cmd)
- [快速开始](#快速开始)
- [使用场景](#使用场景)
- [CLI 命令参考](#cli-命令参考)
- [Python API](#python-api)
- [功能特性](#功能特性)
- [安装](#安装)
- [文档](#文档)
- [项目状态](#项目状态)
- [维护说明](#维护说明)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 为什么选择 Remote CMD？

| 功能 | `remote-cmd` | `ssh` + shell | Ansible | Fabric |
|---|---|---|---|---|
| 主机增删改查 + 标签分组 | ✅ 内置 | ❌ 手动 | ✅ Inventory | ❌ |
| 跨主机批量执行命令 | ✅ `batch-run` | ❌ 自己写循环 | ✅ Playbook | ✅ |
| 文件传输（上传/下载） | ✅ 内置 | ✅ scp | ✅ copy 模块 | ✅ |
| Python API | ✅ `from remote_cmd import ...` | ❌ | ❌ 仅 YAML | ✅ |
| 零配置上手 | ✅ `pip install → 开箱即用` | ❌ 需配置 SSH | ❌ 需 `ansible.cfg` | ❌ |
| 学习曲线 | **低** | 低 | **高** | 中等 |

**选择 `remote-cmd` 的场景**：需要一个开箱即用、适合临时 SSH 任务的 CLI。**选择 Ansible 的场景**：需要完整的配置管理与幂等 playbook。

---

## 快速开始

```bash
# 1. 安装
pip install remote_cmd_manager

# 2. 添加服务器
remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa

# 3. 执行命令
remote-cmd run web-01 "uptime"

# 4. 在所有生产服务器上执行命令
remote-cmd batch-run -t production "df -h /"
```

---

## 使用场景

### 🖥️ 系统管理员 — 一条命令检查 20 台服务器磁盘

```bash
remote-cmd batch-run -t production "df -h / | tail -1"
# 输出：
#   ✓ web-01  → /dev/sda1  32G  12G  19G  40% /
#   ✓ web-02  → /dev/sda1  32G  28G   3G  90% /   ⚠️
#   ✗ db-01   → 连接被拒绝
```

### 🚀 部署 — 拉取代码并重启服务

```python
from remote_cmd.service.host_service import HostService
from remote_cmd.repository import JsonHostRepository

service = HostService(repository=JsonHostRepository("hosts.json"))
for host in service.list_hosts(tag="staging"):
    with service.connect_to_host(host.name) as client:
        client.execute("cd /app && git pull")
        client.execute("pip install -r requirements.txt")
        client.execute_sudo("systemctl restart app", password="sudopass")
```

### 🔥 故障应急 — 检查所有服务器日志

```bash
remote-cmd batch-run -t web "journalctl -xe -n 50 | grep -i error"
```

### 🔧 配置更新 — 上传文件并重载 nginx

```bash
# 上传新配置并在所有 web 服务器上重载
remote-cmd run web-01 "sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t && sudo systemctl reload nginx"
```

---

## CLI 命令参考

所有操作都可以在终端中完成：

| 命令 | 说明 |
|---|---|
| `remote-cmd host add <name> <host> <user>` | 注册服务器（`-k/--key`、`-p/--port`、`-t/--tag` 可重复） |
| `remote-cmd host list [-t TAG]` | 列出主机，可按标签过滤 |
| `remote-cmd host show <name>` | 查看单个主机详情 |
| `remote-cmd host test <name>` | 测试主机连通性 |
| `remote-cmd host remove <name>` | 删除主机 |
| `remote-cmd run <name> "<cmd>"` | 在单台主机上执行命令 |
| `remote-cmd upload <name> <local> <remote>` | 通过 SFTP 上传文件 |
| `remote-cmd download <name> <remote> <local>` | 通过 SFTP 下载文件 |
| `remote-cmd batch-run -t <tag> "<cmd>"` | 在标签下所有主机上执行（`-C` 并发数、`-T` 超时、`--async`、`--show-failures`） |

---

## Python API

在您自己的脚本和自动化流程中使用 Remote CMD：

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa",
)

with SSHClient(config) as client:
    # 执行命令
    result = client.execute("uptime")
    print(result.stdout)

    # 传输文件
    client.upload_file("./local.txt", "/remote/path/file.txt")
    client.download_file("/remote/path/file.txt", "./local.txt")

    # 列出远程目录
    for entry in client.list_remote_directory("/var/log"):
        print(f"{entry['name']}: {entry['size']} bytes")
```

---

## 功能特性

| 分类 | 详情 |
|---|---|
| **SSH 认证** | 密码 + 私钥 + ssh-agent，支持可插拔凭据提供者 |
| **凭据链** | 按优先级从环境变量、系统钥匙串或任意提供者读取密码 |
| **凭据加密** | 静态加密 AES 加密机密数据（`CredentialEncryption`） |
| **命令执行** | 单条、多行、带密码的 sudo 命令 |
| **文件传输** | 通过 SFTP 上传/下载（`remote-cmd upload/download`） |
| **主机管理** | CRUD，支持可插拔的 JSON 或 **SQLite** 持久化 |
| **标签系统** | 按标签过滤主机（如 `production`、`web`、`db`） |
| **批量操作** | 跨任意主机组执行命令，支持同步与异步 |
| **异步内核** | 通过 `[async]` 扩展启用 `AsyncSSHClient` / `AsyncConnectionPool` / `AsyncBatchExecutor` |
| **任务执行器** | 跟踪并调度长时间运行的远程任务并显示状态（`TaskRunner`） |
| **连通性测试** | 对所有主机进行 ping 并报告状态 |
| **安全日志** | 结构化日志自动过滤敏感数据（`SensitiveDataFilter`） |
| **类型安全** | 完整类型注解 + mypy 严格模式 |

---

## 安装

```bash
# 从 PyPI 安装（推荐）—— 同步 API 与 CLI
pip install remote_cmd_manager

# 启用原生异步支持（AsyncSSHClient / AsyncConnectionPool / AsyncBatchExecutor）
pip install "remote_cmd_manager[async]"

# 从源码安装
git clone git@github.com:Vae-Scrooge/remote-cmd.git
cd remote-cmd
pip install -e ".[dev]"
```

安装 `[async]` 扩展会引入 `asyncssh` 并启用原生异步执行内核：
`AsyncSSHClient`、`AsyncConnectionPool` 和 `AsyncBatchExecutor`
（也可通过 `BatchExecutor(use_async=True)` 使用）。即使不安装该扩展，
`import remote_cmd` 依然可以正常工作——只是不会导出异步相关符号。

---

## 文档

📚 **[完整文档中心](./docs/README.md)** — 教程、API 参考、架构设计、故障排查

| 文档 | 内容 |
|---|---|
| [API 参考](./docs/API.md) | 完整 API 文档：SSHClient、AsyncSSHClient、HostService 等 |
| [API 文档（自动生成）](./docs/api/remote_cmd.html) | pdoc 生成的完整 API 参考 |
| [快速入门教程](./docs/tutorial-quickstart.md) | 手把手入门 |
| [高级使用教程](./docs/tutorial-advanced.md) | 批量操作、错误处理、生产环境最佳实践 |
| [架构文档](./docs/architecture.md) | 系统架构与设计决策 |
| [开发指南](./docs/DEVELOPMENT.md) | 搭建开发环境、参与贡献 |
| [故障排查](./docs/TROUBLESHOOTING.md) | 常见问题与解决方案 |
| [更新日志](./CHANGELOG.md) | 版本发布历史 |
| [手机远程指南](./MOBILE-REMOTE-GUIDE.md) | 用手机管理服务器 |

---

## 项目状态

**Beta 阶段。** 核心 API 已稳定。破坏性变更将通过语义化版本号提前告知。

**路线图：**
- [x] 异步 SSH 操作（并行执行）— v1.1.0
- [x] 可插拔存储后端（JSON + SQLite）— v1.2.x
- [x] 可链式凭据提供者 + 静态加密 — v1.2.x
- [ ] 配置档案（AWS、GCP、自定义）
- [ ] 输出格式化（JSON、表格）
- [ ] 模板化命令配方

`good first issue` 标签下的问题很适合新手——
欢迎在[问题跟踪器](https://github.com/Vae-Scrooge/remote-cmd/issues)中查看并参与贡献。

---

## 维护说明

**Remote CMD 是一个积极维护的开源项目。** 它作为重型工具的一个聚焦替代方案而独立设计与开发，
专门解决日常服务器工作中遇到的临时 SSH 任务。

- **项目健康度：** 每次 PR 都会运行 CI，支持 Python 3.9+，
  公共 API 遵循[语义化版本](https://semver.org/lang/zh-CN/)进行版本管理。
- **您的代码、您的服务器：** 基于 MIT 许可证开源——不收集遥测数据，也不锁定在任何服务之下。
- **为什么开源？** 现有的临时 SSH 管理工具要么太重（Ansible），要么太简陋（裸用 shell 循环）。
  Remote CMD 让一条命令即可覆盖远程管理的常见需求。

---

## 参与贡献

欢迎各类贡献！请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.zh-CN.md) 了解如何参与。

在贡献之前，请阅读我们的[行为准则](./CODE_OF_CONDUCT.md)。

---

## 许可证

MIT © [Vae-Scrooge](https://github.com/Vae-Scrooge/remote-cmd)

---

<p align="center">
  <a href="https://github.com/Vae-Scrooge/remote-cmd">
    <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=social" alt="Star">
  </a>
  <br>
  <sub>如果您觉得这个项目有用，欢迎 **点 Star** ⭐</sub>
</p>
