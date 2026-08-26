# 系统架构文档

本文档详细介绍 Remote CMD 的系统架构、设计决策和技术实现细节。

## 目录

- [架构概览](#架构概览)
- [分层架构](#分层架构)
- [核心组件](#核心组件)
- [数据流](#数据流)
- [设计模式](#设计模式)
- [扩展性设计](#扩展性设计)
- [性能考虑](#性能考虑)
- [安全设计](#安全设计)

---

## 架构概览

Remote CMD 采用分层架构设计，将系统划分为多个职责清晰的层次，确保代码的可维护性和可扩展性。

### 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         用户交互层                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   CLI 工具    │  │  Python API  │  │   配置文件管理        │  │
│  │  remote-cmd   │  │  程序化调用   │  │  YAML/JSON/环境变量   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼──────────────────┼─────────────────────┼──────────────┘
          │                  │                     │
          ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         业务逻辑层                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            HostService + HostRepository 主机服务          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │   │
│  │  │   添加主机    │  │   删除主机    │  │   查询主机    │   │   │
│  │  │   标签管理    │  │   批量操作    │  │   连接测试    │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │   │
│  └────────────────────┬────────────────────────────────────┘   │
└───────────────────────┼────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                         核心功能层                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    SSHClient SSH 客户端                   │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │   │
│  │  │   连接管理    │  │   命令执行    │  │   文件传输    │   │   │
│  │  │   认证处理    │  │   输出处理    │  │   SFTP 操作  │   │   │
│  │  │   会话管理    │  │   超时控制    │  │   目录操作    │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │   │
│  └────────────────────┬────────────────────────────────────┘   │
└───────────────────────┼────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                         网络传输层                                │
│                    Paramiko (SSHv2 协议实现)                     │
│              支持：密码认证、密钥认证、SFTP、端口转发              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 分层架构

### 1. 用户交互层

负责与用户交互，提供多种使用方式。

#### CLI 工具 (`remote_cmd/cli/`)
- 基于 Click 框架构建
- 提供友好的命令行界面
- 支持子命令和参数解析
- 彩色输出和进度显示

#### Python API (`remote_cmd/core/`)
- 提供程序化接口
- 支持类型提示和 IDE 自动补全
- 上下文管理器确保资源释放

#### 配置管理 (`remote_cmd/utils/config.py`)
- 支持 YAML/JSON 格式
- 环境变量覆盖
- 默认配置管理

### 2. 业务逻辑层

#### HostService + HostRepository (`remote_cmd/service/host_service.py` + `remote_cmd/repository/`)
主机服务的核心职责：
- 主机配置的 CRUD 操作
- 标签分类和筛选
- 批量操作支持
- 数据持久化（JsonHostRepository / SqliteHostRepository / 存储引擎自动切换）

**设计决策：**
- Repository 定义存储接口，Json/SQLite 实现可插拔切换
- HostService 承载业务逻辑与凭据解析，与存储解耦
- 存储引擎按文件扩展名自动选择（.json/.db/.sqlite）或显式 storage_backend

### 3. 核心功能层

#### SSHClient (`remote_cmd/core/ssh_client.py`)
SSH 客户端的核心功能：
- 连接管理（建立、维护、断开）
- 命令执行（同步；异步实现见 `AsyncSSHClient`）
- 文件传输（SFTP）
- 会话复用

**设计决策：**
- 使用上下文管理器确保连接关闭
- 异常转换，统一错误处理
- 支持 `SyncConnectionPool` / `AsyncConnectionPool`，批量执行器按需复用连接

### 4. 网络传输层

使用 Paramiko 库实现 SSHv2 协议：
- 密码认证
- 公钥认证（RSA/ED25519）
- SFTP 文件传输
- 端口转发（未来扩展）

---

## 核心组件

### SSHClient 组件

```python
class SSHClient:
    """
    SSH 客户端组件
    
    职责：
    1. 管理 SSH 连接生命周期
    2. 执行远程命令
    3. 传输文件
    4. 处理异常
    """
    
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self._client = None
        self._sftp = None
    
    def connect(self) -> "SSHClient":
        # 建立连接
        pass
    
    def execute(self, command: str) -> CommandResult:
        # 执行命令
        pass
    
    def upload_file(self, local: str, remote: str):
        # 上传文件
        pass
```

**组件关系：**
```
SSHClient *-- ConnectionConfig
SSHClient o-- paramiko.SSHClient
SSHClient o-- paramiko.SFTPClient
```

### HostService 组件

```python
class HostService:
    """
    主机服务组件

    职责：
    1. 管理主机集合
    2. 委托仓库持久化
    3. 提供筛选和查询
    4. 批量操作支持
    """

    def __init__(self, repository: HostRepository):
        self._repository = repository

    def add_host(self, host: Host):
        # 添加主机
        pass

    def connect_to_host(self, name: str) -> SSHClient:
        # 连接到指定主机
        pass
```

**组件关系：**
```
HostService o-- HostRepository
HostService *-- "*" Host
HostRepository <|.. JsonHostRepository
HostRepository <|.. SqliteHostRepository
HostService ..> SSHClient : creates
Host *-- ConnectionConfig
```

---

## 数据流

### 命令执行数据流

```
用户调用
    │
    ▼
┌─────────────────┐
│  CLI/API 入口   │  解析参数，验证输入
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   HostService   │  查找主机配置
│   get_host()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   SSHClient     │  建立连接
│    connect()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Paramiko     │  SSH 协议通信
│   SSHClient     │
└────────┬────────┘
         │
         ▼
    远程服务器
         │
         ▼
┌─────────────────┐
│   SSHClient     │  接收响应
│    execute()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  CommandResult  │  封装结果
└────────┬────────┘
         │
         ▼
    返回给用户
```

### 文件传输数据流

```
用户调用 upload_file()
         │
         ▼
┌──────────────────┐
│  验证本地文件存在  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  建立 SFTP 通道   │  使用现有 SSH 连接
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Paramiko SFTP   │  SFTP put 操作
│     put()        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  验证传输结果     │  检查返回值
└────────┬─────────┘
         │
         ▼
    返回成功/失败
```

---

## 设计模式

### 1. 上下文管理器模式

用于自动管理资源生命周期：

```python
# 使用上下文管理器自动关闭连接
with SSHClient(config) as client:
    result = client.execute("ls -la")
    # 连接自动关闭，无需手动调用 disconnect()
```

**好处：**
- 防止资源泄露
- 代码更简洁
- 异常安全

### 2. 策略模式

认证方式的策略模式：

```python
# 不同的认证策略
config1 = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="secret"  # 密码认证策略
)

config2 = ConnectionConfig(
    hostname="example.com",
    username="admin",
    key_filename="~/.ssh/id_rsa"  # 密钥认证策略
)
```

### 3. 工厂模式

HostService 作为 SSHClient 的工厂：

```python
# HostService 创建 SSHClient
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
client = manager.connect_to_host("web-server")
# 返回已连接的 SSHClient 实例
```

### 4. 数据类模式

使用 @dataclass 定义数据对象：

```python
@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    
    @property
    def success(self) -> bool:
        return self.exit_code == 0
```

---

## 扩展性设计

### 插件架构

预留的扩展点：

```python
# 自定义认证插件（未来）
class AuthPlugin:
    def authenticate(self, client: SSHClient) -> bool:
        pass

# 自定义命令处理器（未来）
class CommandHandler:
    def before_execute(self, command: str) -> str:
        pass
    
    def after_execute(self, result: CommandResult):
        pass
```

### 钩子系统

事件钩子设计：

```python
# 连接钩子
@hooks.connect
def on_connect(client: SSHClient):
    logger.info(f"Connected to {client.config.hostname}")

# 命令执行钩子
@hooks.execute
def on_execute(command: str, result: CommandResult):
    metrics.record_command(command, result.exit_code)
```

### 自定义后端

支持自定义 SSH 后端：

```python
# 抽象基类
class SSHBackend(ABC):
    @abstractmethod
    def connect(self, config: ConnectionConfig):
        pass

# 可以替换为其他实现
class AsyncSSHBackend(SSHBackend):
    pass
```

---

## 性能考虑

### 连接复用

当前实现：
- 直接使用 `SSHClient` / `AsyncSSHClient` 时，每个客户端实例代表一个连接；
  调用方可在同一客户端上连续执行多个命令。
- 同步 `BatchExecutor` 在多主机或重试批次中按主机使用 `SyncConnectionPool`。
- 异步 `AsyncBatchExecutor` 在多主机或重试批次中按主机使用
  `AsyncConnectionPool`（`remote_cmd.core.async_connection_pool`），基于 asyncssh
  复用连接并带空闲/生命周期回收与健康检查。
- 两个执行器均支持 `pool_factory` 注入外部池；外部池由调用方拥有、执行器绝不关闭，
  内部创建的池在批次结束后自动关闭。

```python
# 连接池（已实现，供异步批量执行使用）
from remote_cmd.core.async_connection_pool import AsyncConnectionPool

pool = AsyncConnectionPool(config, max_connections=10)
client = await pool.acquire()   # 复用现有连接或创建新连接
await pool.release(client)
```

### 批量操作优化

并行执行示例：

```python
from concurrent.futures import ThreadPoolExecutor

def parallel_execute(hosts, command):
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for host in hosts:
            future = executor.submit(execute_on_host, host, command)
            futures.append(future)
        
        results = [f.result() for f in futures]
    return results
```

### 文件传输优化

- 启用压缩传输
- 分块传输大文件
- 支持断点续传（计划中）

---

## 安全设计

### 认证安全

1. **密码管理**
   - 不在日志中记录密码
   - 支持密码文件读取
   - 环境变量传递

2. **密钥管理**
   - 支持 SSH Agent
   - 密钥文件权限检查
   - 凭据加密存储（`CredentialEncryption`）

### 传输安全

- 使用 SSH 加密通道
- 支持密钥指纹验证
- 默认严格进行主机密钥检查，可按受控场景配置策略

### 配置安全

```python
# 敏感信息处理
class SecureConfig:
    def get_password(self) -> str:
        # 从安全存储获取
        pass
    
    def mask_sensitive(self, data: dict) -> dict:
        # 脱敏处理
        masked = data.copy()
        if 'password' in masked:
            masked['password'] = '***'
        return masked
```

---

## 技术栈

### 核心依赖

| 库 | 版本 | 用途 |
|----|------|------|
| paramiko | >=3.0.0 | SSH 协议实现 |
| click | >=8.0.0 | CLI 框架 |
| pyyaml | >=6.0 | YAML 解析 |

### 开发工具

| 工具 | 用途 |
|------|------|
| pytest | 单元测试 |
| black | 代码格式化 |
| flake8 | 代码检查 |
| mypy | 类型检查 |

---

## 未来规划

### 短期计划

- [x] 连接池支持
- [x] 异步 API
- [x] 凭据加密存储
- [x] 批量并行执行

### 长期计划

- [ ] Web UI 界面
- [ ] 跳板机/堡垒机支持
- [ ] Ansible 集成
- [ ] Docker 容器支持

---

**最后更新：** 2026-08-23（v2.1.0 发布审计）
