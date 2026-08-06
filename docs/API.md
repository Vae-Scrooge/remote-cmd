# API 使用指南

本文档提供 Remote CMD 公共 API 的使用示例与说明（手写维护）。

> 📚 **完整 API 参考（自动生成）**：请查看 [pdoc 生成的 API 文档](./api/remote_cmd.html)。
> 本文档聚焦用法示例，完整签名/参数/返回类型以自动生成版本为准。

## 目录

- [Core 模块](#core-模块)
  - [ConnectionConfig](#connectionconfig)
  - [SSHClient](#sshclient)
  - [CommandResult](#commandresult)
  - [Host](#host)
- [Service 模块](#service-模块)
  - [HostService](#hostservice)
  - [HostRepository](#hostrepository)
- [CLI 模块](#cli-模块)
- [Utils 模块](#utils-模块)
- [Exceptions 异常](#exceptions-异常)
- [版本兼容性](#版本兼容性)

---

## Core 模块

### ConnectionConfig

SSH 连接配置类，用于配置 SSH 连接参数。

#### 类定义

```python
@dataclass
class ConnectionConfig:
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    timeout: int = 30
    compress: bool = True
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `hostname` | `str` | 必填 | 服务器地址（IP 或域名） |
| `username` | `str` | 必填 | SSH 用户名 |
| `port` | `int` | 22 | SSH 端口 |
| `password` | `Optional[str]` | None | 密码（与 key_filename 二选一） |
| `key_filename` | `Optional[str]` | None | SSH 私钥路径 |
| `timeout` | `int` | 30 | 连接超时时间（秒） |
| `compress` | `bool` | True | 是否启用压缩 |

#### 使用示例

```python
from remote_cmd.core.ssh_client import ConnectionConfig

# 密码认证
config1 = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="secret123"
)

# 密钥认证
config2 = ConnectionConfig(
    hostname="example.com",
    username="deploy",
    key_filename="~/.ssh/id_rsa",
    port=2222,
    timeout=60
)
```

#### 异常

- `ValueError`: 当 password 和 key_filename 都未提供时抛出

---

### SSHClient

SSH 客户端类，用于管理 SSH 连接和执行远程操作。

#### 类定义

```python
class SSHClient:
    def __init__(self, config: ConnectionConfig)
    def connect(self) -> "SSHClient"
    def disconnect(self) -> None
    def execute(self, command: str, timeout: Optional[int] = None, 
                environment: Optional[Dict[str, str]] = None) -> CommandResult
    def execute_sudo(self, command: str, password: Optional[str] = None, 
                     timeout: Optional[int] = None) -> CommandResult
    def upload_file(self, local_path: str, remote_path: str) -> None
    def download_file(self, remote_path: str, local_path: str) -> None
    def list_remote_directory(self, remote_path: str = ".") -> List[Dict[str, Any]]
    def is_connected(self) -> bool
```

#### 构造函数

##### `__init__(config: ConnectionConfig)`

初始化 SSH 客户端。

**参数：**
- `config` (ConnectionConfig): 连接配置对象

**示例：**
```python
config = ConnectionConfig(hostname="example.com", username="admin", password="pass")
client = SSHClient(config)
```

#### 方法

##### `connect() -> SSHClient`

建立 SSH 连接。

**返回：**
- `SSHClient`: 返回 self，支持链式调用

**异常：**
- `SSHConnectionError`: 连接失败时抛出

**示例：**
```python
client = SSHClient(config).connect()
# 或
client = SSHClient(config)
client.connect()
```

##### `disconnect() -> None`

关闭 SSH 连接并清理资源。

**示例：**
```python
client.disconnect()
```

##### `execute(command: str, timeout: Optional[int] = None, environment: Optional[Dict[str, str]] = None) -> CommandResult`

在远程服务器上执行命令。

**参数：**
- `command` (str): 要执行的命令字符串
- `timeout` (Optional[int]): 命令执行超时时间（秒），默认无超时
- `environment` (Optional[Dict[str, str]]): 环境变量字典

**返回：**
- `CommandResult`: 命令执行结果对象

**异常：**
- `SSHConnectionError`: 未连接时抛出
- `SSHCommandError`: 命令执行失败时抛出

**示例：**
```python
# 简单执行
result = client.execute("ls -la")

# 带超时
result = client.execute("sleep 10", timeout=5)

# 带环境变量
result = client.execute(
    "echo $MY_VAR",
    environment={"MY_VAR": "hello"}
)

# 检查结果
if result.success:
    print(f"输出: {result.stdout}")
else:
    print(f"错误: {result.stderr}")
```

##### `execute_sudo(command: str, password: Optional[str] = None, timeout: Optional[int] = None) -> CommandResult`

使用 sudo 权限执行命令。

**参数：**
- `command` (str): 要执行的命令
- `password` (Optional[str]): sudo 密码（如配置了免密 sudo 可不传）
- `timeout` (Optional[int]): 超时时间（秒）

**返回：**
- `CommandResult`: 命令执行结果

**示例：**
```python
# 使用密码
result = client.execute_sudo("apt update", password="sudopass")

# 免密 sudo
result = client.execute_sudo("systemctl restart nginx")
```

##### `upload_file(local_path: str, remote_path: str) -> None`

上传文件到远程服务器。

**参数：**
- `local_path` (str): 本地文件路径
- `remote_path` (str): 远程目标路径

**异常：**
- `SSHFileTransferError`: 传输失败时抛出
- `SSHConnectionError`: 未连接时抛出

**示例：**
```python
client.upload_file("./local_script.sh", "/tmp/remote_script.sh")
```

##### `download_file(remote_path: str, local_path: str) -> None`

从远程服务器下载文件。

**参数：**
- `remote_path` (str): 远程文件路径
- `local_path` (str): 本地保存路径

**异常：**
- `SSHFileTransferError`: 传输失败时抛出
- `SSHConnectionError`: 未连接时抛出

**示例：**
```python
client.download_file("/var/log/nginx/error.log", "./logs/error.log")
```

##### `list_remote_directory(remote_path: str = ".") -> List[Dict[str, Any]]`

列出远程目录内容。

**参数：**
- `remote_path` (str): 远程目录路径，默认为当前目录

**返回：**
- `List[Dict[str, Any]]`: 文件/目录信息列表
  - `name` (str): 文件名
  - `size` (int): 文件大小（字节）
  - `mode` (str): 权限模式（如 "644"）
  - `mtime` (int): 修改时间戳
  - `is_dir` (bool): 是否为目录

**异常：**
- `SSHFileTransferError`: 列出目录失败时抛出
- `SSHConnectionError`: 未连接时抛出

**示例：**
```python
entries = client.list_remote_directory("/var/www")
for entry in entries:
    type_icon = "📁" if entry["is_dir"] else "📄"
    print(f"{type_icon} {entry['name']}: {entry['size']} bytes")
```

##### `is_connected() -> bool`

检查 SSH 连接是否活跃。

**返回：**
- `bool`: 连接状态

**示例：**
```python
if client.is_connected():
    print("连接正常")
else:
    print("连接已断开")
```

#### 上下文管理器

SSHClient 支持上下文管理器，确保资源正确释放：

```python
# 推荐用法
with SSHClient(config) as client:
    result = client.execute("uptime")
    # 连接会自动关闭

# 等价于
try:
    client = SSHClient(config).connect()
    result = client.execute("uptime")
finally:
    client.disconnect()
```

---

### CommandResult

命令执行结果类。

#### 类定义

```python
@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    
    @property
    def success(self) -> bool
```

#### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `command` | `str` | 执行的命令 |
| `stdout` | `str` | 标准输出 |
| `stderr` | `str` | 标准错误 |
| `exit_code` | `int` | 退出码 |
| `success` | `bool` | 是否成功（exit_code == 0） |

#### 使用示例

```python
result = client.execute("ls -la")

# 检查是否成功
if result.success:
    print(f"命令输出:\n{result.stdout}")
else:
    print(f"命令失败 (exit code: {result.exit_code})")
    print(f"错误信息:\n{result.stderr}")

# 打印结果摘要
print(result)  # 输出: ✓ [0] ls -la
```

---

### Host

主机配置数据类。

#### 类定义

```python
@dataclass
class Host:
    name: str
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    tags: Optional[List[str]] = None
    description: str = ""
    
    def to_connection_config(self) -> ConnectionConfig
    def to_dict(self) -> Dict[str, Any]
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Host"
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | `str` | 必填 | 主机别名（唯一标识） |
| `hostname` | `str` | 必填 | 服务器地址 |
| `username` | `str` | 必填 | SSH 用户名 |
| `port` | `int` | 22 | SSH 端口 |
| `password` | `Optional[str]` | None | 密码 |
| `key_filename` | `Optional[str]` | None | SSH 密钥路径 |
| `tags` | `Optional[List[str]]` | None | 标签列表 |
| `description` | `str` | "" | 描述信息 |

#### 方法

##### `to_connection_config() -> ConnectionConfig`

转换为 ConnectionConfig 对象。

**返回：**
- `ConnectionConfig`: 连接配置对象

**示例：**
```python
host = Host(name="web", hostname="192.168.1.10", username="admin")
config = host.to_connection_config()
client = SSHClient(config)
```

##### `to_dict() -> Dict[str, Any]`

转换为字典。

**返回：**
- `Dict[str, Any]`: 主机配置字典

##### `from_dict(data: Dict[str, Any]) -> Host`

从字典创建 Host 对象。

**参数：**
- `data` (Dict[str, Any]): 主机配置字典

**返回：**
- `Host`: Host 对象

**示例：**
```python
data = {
    "name": "web",
    "hostname": "192.168.1.10",
    "username": "admin",
    "tags": ["production"]
}
host = Host.from_dict(data)
```

---

### HostService

主机服务类，承载主机管理的业务逻辑，委托 `HostRepository` 持久化。

#### 类定义

```python
class HostService:
    def __init__(self, repository: HostRepository, encryption: Optional[CredentialEncryption] = None,
                 credential_provider: Optional[CredentialProvider] = None)
    def add_host(self, host: Host) -> Host
    def update_host(self, name: str, **kwargs) -> Host
    def remove_host(self, name: str) -> None
    def get_host(self, name: str) -> Host
    def resolve_host(self, name: str) -> Host
    def list_hosts(self, tag: Optional[str] = None) -> List[Host]
    def list_tags(self) -> List[str]
    def connect_to_host(self, name: str) -> SSHClient
    def test_connection(self, name: str) -> bool
    def test_all_connections(self) -> Dict[str, bool]
```

#### 构造函数

##### `__init__(repository: HostRepository, encryption=None, credential_provider=None)`

初始化主机服务。

**参数：**
- `repository` (HostRepository): 存储仓库（`JsonHostRepository` / `SqliteHostRepository`）
- `encryption` (Optional[CredentialEncryption]): 密码加密器（可选）
- `credential_provider` (Optional[CredentialProvider]): 凭据提供链（可选）

**示例：**
```python
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

# 从文件加载
repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
```

#### 方法

##### `add_host(host: Host) -> None`

添加主机。

**参数：**
- `host` (Host): Host 对象

**异常：**
- `ValueError`: 主机名已存在

**示例：**
```python
host = Host(
    name="web-server",
    hostname="192.168.1.10",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa",
    tags=["production", "web"]
)
manager.add_host(host)
repo.flush()
```

##### `remove_host(name: str) -> None`

删除主机。

**参数：**
- `name` (str): 主机名

**异常：**
- `KeyError`: 主机不存在

**示例：**
```python
manager.remove_host("web-server")
repo.flush()
```

##### `get_host(name: str) -> Host`

获取主机。

**参数：**
- `name` (str): 主机名

**返回：**
- `Host`: 主机对象

**异常：**
- `KeyError`: 主机不存在

**示例：**
```python
host = manager.get_host("web-server")
print(f"主机地址: {host.hostname}")
```

##### `list_hosts(tag: Optional[str] = None) -> List[Host]`

列出所有主机，可按标签筛选。

**参数：**
- `tag` (Optional[str]): 标签筛选（可选）

**返回：**
- `List[Host]`: 主机列表

**示例：**
```python
# 所有主机
all_hosts = manager.list_hosts()

# 生产环境主机
prod_hosts = manager.list_hosts(tag="production")

# Web 服务器
web_hosts = manager.list_hosts(tag="web")
```

##### `list_tags() -> List[str]`

列出所有标签。

**返回：**
- `List[str]`: 标签列表（已排序）

**示例：**
```python
tags = manager.list_tags()
print(f"可用标签: {', '.join(tags)}")
```

##### `connect_to_host(name: str) -> SSHClient`

连接到指定主机。

**参数：**
- `name` (str): 主机名

**返回：**
- `SSHClient`: 已连接的 SSHClient 实例

**异常：**
- `KeyError`: 主机不存在
- `SSHConnectionError`: 连接失败

**示例：**
```python
with manager.connect_to_host("web-server") as client:
    result = client.execute("uptime")
    print(result.stdout)
```

##### `test_connection(name: str) -> bool`

测试连接。

**参数：**
- `name` (str): 主机名

**返回：**
- `bool`: 连接是否成功

**示例：**
```python
if manager.test_connection("web-server"):
    print("连接成功")
else:
    print("连接失败")
```

##### `test_all_connections() -> Dict[str, bool]`

测试所有主机连接。

**返回：**
- `Dict[str, bool]`: 主机名到连接状态的映射

**示例：**
```python
results = manager.test_all_connections()
for name, success in results.items():
    status = "✓" if success else "✗"
    print(f"{status} {name}")
```

---

### HostRepository

主机存储仓库抽象接口，定义主机配置的持久化契约。

#### 类定义

```python
class HostRepository(ABC):
    def save(self, host: Host) -> None
    def get(self, name: str) -> Host
    def delete(self, name: str) -> None
    def list(self, tag: Optional[str] = None) -> List[Host]
    def list_tags(self) -> List[str]
    def contains(self, name: str) -> bool
    def count(self) -> int
    def flush(self) -> None
```

#### 实现

| 类 | 存储格式 | 适用场景 |
|----|---------|---------|
| `JsonHostRepository` | JSON 文件（原子写入、可选加密） | 默认、轻量配置 |
| `SqliteHostRepository` | SQLite 数据库（索引、分页、搜索） | 大批量主机 |

#### 存储引擎自动切换

可通过 `remote_cmd.service.storage_factory.build_repository` 根据文件扩展名
（`.json` / `.db` / `.sqlite`）自动选择存储引擎，或显式指定 `storage_backend`：

```python
from remote_cmd.service.storage_factory import build_repository

repo = build_repository("hosts.json")          # JsonHostRepository
repo = build_repository("hosts.db")            # SqliteHostRepository
repo = build_repository("hosts.json", storage_backend="sqlite")  # 显式优先
```

---

## 异步模块（v1.1.0+）

> 异步模块基于 `asyncssh`，需要额外安装：
> ```bash
> pip install "remote_cmd_manager[async]"
> ```
> 未安装 asyncssh 时，`import remote_cmd` 仍可用，但异步符号不会被导出。

### AsyncSSHClient

原生异步 SSH 客户端，基于 asyncssh 实现，API 与同步 `SSHClient` 保持一致。

#### 类定义

```python
class AsyncSSHClient:
    def __init__(self, config: ConnectionConfig, loop: Optional[Any] = None)
    async def connect(self) -> "AsyncSSHClient"
    async def disconnect(self) -> None
    def is_connected(self) -> bool
    async def execute(self, command: str, timeout: Optional[int] = None,
                      environment: Optional[Dict[str, str]] = None) -> CommandResult
    async def execute_sudo(self, command: str, password: Optional[str] = None,
                           timeout: Optional[int] = None) -> CommandResult
    async def upload_file(self, local_path: str, remote_path: str) -> None
    async def download_file(self, remote_path: str, local_path: str) -> None
    async def list_remote_directory(self, remote_path: str = ".") -> List[Dict[str, Any]]
```

#### 构造函数

##### `__init__(config: ConnectionConfig, loop: Optional[Any] = None)`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config` | `ConnectionConfig` | 必填 | SSH 连接配置（与同步版一致） |
| `loop` | `Optional[Any]` | None | 已忽略；asyncssh 自行从当前事件循环取用，保留仅为向后兼容 |

#### 使用示例

```python
import asyncio
from remote_cmd.core.async_ssh_client import AsyncSSHClient
from remote_cmd.core.ssh_client import ConnectionConfig

async def main():
    config = ConnectionConfig(
        hostname="192.168.1.100",
        username="admin",
        key_filename="~/.ssh/id_rsa",
    )
    async with AsyncSSHClient(config) as client:
        result = await client.execute("uptime")
        print(result.stdout)

asyncio.run(main())
```

#### 与同步 SSHClient 的差异

| 特性 | `SSHClient` | `AsyncSSHClient` |
|------|-------------|------------------|
| 底层 | paramiko | asyncssh |
| 调用方式 | 同步阻塞 | `async`/`await` |
| 上下文管理器 | 同步 | 异步（`async with`） |
| 适用场景 | 简单脚本、CLI | 高并发批量执行 |
| 返回类型 | `CommandResult` | `CommandResult`（一致） |

#### 异常

- `SSHConnectionError`: 连接/认证失败（含超时与密钥文件不存在）
- `SSHCommandError`: 命令执行失败
- `SSHFileTransferError`: 文件传输失败

---

### AsyncConnectionPool

原生异步 SSH 连接池，复用连接并带空闲/生命周期回收与健康检查。

#### 类定义

```python
class AsyncConnectionPool:
    def __init__(self, config: ConnectionConfig, max_connections: int = 10,
                 max_lifetime: int = 3600, idle_timeout: int = 300,
                 health_check_interval: int = 60)
    async def acquire(self) -> AsyncSSHClient
    async def release(self, conn: Optional[AsyncSSHClient]) -> None
    def acquire_context(self) -> "_AcquireContext"
    def get_metrics(self) -> Dict[str, Any]
    def stop_monitor(self) -> None
    async def close_all(self) -> None
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config` | `ConnectionConfig` | 必填 | 用于建立 SSH 连接的配置 |
| `max_connections` | `int` | 10 | 最大连接数 |
| `max_lifetime` | `int` | 3600 | 连接最大生命周期（秒），超过自动关闭 |
| `idle_timeout` | `int` | 300 | 空闲超时（秒），超过自动关闭 |
| `health_check_interval` | `int` | 60 | 后台清理任务周期（秒） |

#### 使用示例

```python
import asyncio
from remote_cmd.core.async_connection_pool import AsyncConnectionPool
from remote_cmd.core.ssh_client import ConnectionConfig

async def main():
    pool = AsyncConnectionPool(
        ConnectionConfig(hostname="192.168.1.100", username="admin",
                         key_filename="~/.ssh/id_rsa"),
        max_connections=5,
    )
    async with pool:
        async with pool.acquire_context() as client:
            result = await client.execute("uptime")
            print(result.stdout)
    # 也可手动管理：
    # client = await pool.acquire()
    # await pool.release(client)

asyncio.run(main())
```

#### `get_metrics()` 返回的指标

| 键 | 说明 |
|----|------|
| `active` | 当前活跃连接数 |
| `idle` | 空闲连接数 |
| `total_connections` | 连接池持有的总连接数 |
| `total_created` | 累计创建数 |
| `reconnects` | 累计重连数 |
| `failed` | 累计失败数 |

---

### SyncConnectionPool

同步 SSH 连接池，为反复执行的短命令场景（批量执行、连接测试）复用连接，
避免每次操作都重新握手。与 `AsyncConnectionPool` 对称设计。

#### 类定义

```python
class SyncConnectionPool:
    def __init__(self, config: ConnectionConfig, max_connections: int = 10,
                 max_lifetime: int = 3600, idle_timeout: int = 300,
                 health_check_interval: int = 60)
    def acquire(self) -> SSHClient
    def release(self, conn: Optional[SSHClient]) -> None
    def acquire_context(self) -> SyncConnectionPool._AcquireContext
    def get_metrics(self) -> Dict[str, Any]
    def close_all(self) -> None
```

#### 使用示例

```python
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.core.sync_connection_pool import SyncConnectionPool

config = ConnectionConfig(hostname="192.168.1.100", username="admin")
pool = SyncConnectionPool(config, max_connections=5)

with pool.acquire_context() as client:
    result = client.execute("uptime")

pool.close_all()
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `config` | `ConnectionConfig` | 必填 | 连接配置 |
| `max_connections` | `int` | 10 | 最大连接数 |
| `max_lifetime` | `int` | 3600 | 连接最大生命周期（秒） |
| `idle_timeout` | `int` | 300 | 空闲超时（秒） |
| `health_check_interval` | `int` | 60 | 后台清理线程周期（秒） |

#### 指标（get_metrics）

与 `AsyncConnectionPool` 相同的键：`active` / `idle` / `total_connections` /
`total_created` / `reconnects` / `failed`。

> **Note**: `BatchExecutor`（同步内核）在批处理期间自动按主机复用连接，
> 单个批次结束后统一关闭。无需手动创建连接池。

---

### AsyncBatchExecutor

原生异步批量命令执行器，基于 `asyncio.Semaphore` 控制并发。

#### 类定义

```python
class AsyncBatchExecutor:
    def __init__(self, host_service: HostService, max_concurrency: int = 10,
                 command_timeout: int = 30)
    async def execute(self, host_names: List[str], command: str,
                      retry_count: int = 0, retry_delay: float = 1.0,
                      progress_callback: Optional[ProgressCallback] = None) -> BatchResult
```

#### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `host_service` | `HostService` | 必填 | 主机服务（提供主机配置与凭据解析） |
| `max_concurrency` | `int` | 10 | 最大并发主机数 |
| `command_timeout` | `int` | 30 | 单条命令超时（秒） |

`execute` 参数与同步 `BatchExecutor.execute` 一致：`retry_count` 失败重试次数、
`retry_delay` 重试间隔（秒）、`progress_callback` 进度回调
`(completed, total, host_name)`（可为同步或 async 函数）。

#### 使用示例

```python
import asyncio
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor
from remote_cmd.service.host_service import HostService
from remote_cmd.repository.json_host_repository import JsonHostRepository

async def main():
    service = HostService(repository=JsonHostRepository("hosts.json"))
    executor = AsyncBatchExecutor(service, max_concurrency=5)

    async def progress(completed, total, host_name):
        print(f"[{completed}/{total}] {host_name}")

    result = await executor.execute(
        ["web-01", "web-02", "db-01"],
        "uptime",
        retry_count=1,
        progress_callback=progress,
    )
    print(result.summary())

asyncio.run(main())
```

#### 返回值

`BatchResult`，与同步 `BatchExecutor` 完全一致（`total` / `success` / `failed` /
`duration` / `results`，以及 `success_rate` / `failed_hosts` / `summary()`）。

#### 异常

- `ValueError`: `host_names` 为空

#### 与同步 BatchExecutor 的关系

`BatchExecutor(host_service, use_async=True)` 在内部委托给 `AsyncBatchExecutor`，
对外保持同步接口。两者数据契约（`BatchResult` / `BatchHostResult`）完全一致，
可无差别切换。

---

## CLI 模块

### 命令行接口

Remote CMD 提供完整的命令行工具。

#### 全局选项

```bash
# 版本信息
remote-cmd --version

# 帮助信息
remote-cmd --help

# 指定配置文件
remote-cmd --config /path/to/config.yaml <command>

# 详细输出
remote-cmd --verbose <command>
```

#### host 命令组

##### host add

添加主机。

```bash
remote-cmd host add <name> <hostname> <username> [options]

选项：
  -p, --port INTEGER          SSH 端口 (默认: 22)
  -P, --password TEXT         密码
  -k, --key TEXT              SSH 私钥文件路径
  -t, --tag TEXT              标签（可多次使用）
  -d, --description TEXT      描述信息
```

**示例：**
```bash
remote-cmd host add web-server 192.168.1.10 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag production \
    --tag web \
    --description "Production web server"
```

##### host list

列出主机。

```bash
remote-cmd host list [options]

选项：
  -t, --tag TEXT              按标签筛选
```

**示例：**
```bash
# 列出所有主机
remote-cmd host list

# 只列出 Web 服务器
remote-cmd host list --tag web
```

##### host remove

删除主机。

```bash
remote-cmd host remove <name>
```

**注意：** 会要求确认。

**示例：**
```bash
remote-cmd host remove web-server
```

##### host test

测试连接。

```bash
remote-cmd host test <name>
```

**示例：**
```bash
remote-cmd host test web-server
```

#### run 命令

执行远程命令。

```bash
remote-cmd run <host_name> <command>
```

**示例：**
```bash
remote-cmd run my-server "ls -la"
remote-cmd run my-server "systemctl status nginx"
remote-cmd run my-server "df -h"
```

#### upload 命令

上传文件。

```bash
remote-cmd upload <host_name> <local_path> <remote_path>
```

**示例：**
```bash
remote-cmd upload my-server ./app.tar.gz /tmp/app.tar.gz
```

#### download 命令

下载文件。

```bash
remote-cmd download <host_name> <remote_path> <local_path>
```

**示例：**
```bash
remote-cmd download my-server /var/log/nginx/error.log ./logs/
```

---

## Utils 模块

### Config 配置管理

```python
from remote_cmd.utils.config import load_config, save_config, get_default_config_path

# 加载配置
config = load_config("config.yaml")

# 保存配置
save_config(config, "config.yaml")

# 获取默认配置路径
path = get_default_config_path()
```

#### load_config(config_path: str) -> Dict[str, Any]

加载配置文件。

**参数：**
- `config_path` (str): 配置文件路径

**返回：**
- `Dict[str, Any]`: 配置字典

**支持格式：**
- YAML (.yaml, .yml)
- JSON (.json)

#### save_config(config: Dict[str, Any], config_path: str) -> None

保存配置文件。

**参数：**
- `config` (Dict[str, Any]): 配置字典
- `config_path` (str): 配置文件路径

#### get_default_config_path() -> str

获取默认配置文件路径。

**返回：**
- `str`: 配置文件路径

---

## Exceptions 异常

### 异常层次结构

```
RemoteCmdError (Base)
├── SSHError
│   ├── SSHConnectionError
│   ├── SSHCommandError
│   └── SSHFileTransferError
├── ConfigError
└── ValidationError
```

### 异常类

#### RemoteCmdError

所有异常的基类。

```python
from remote_cmd.utils.exceptions import RemoteCmdError

try:
    # ...
except RemoteCmdError as e:
    print(f"Remote CMD 错误: {e}")
```

#### SSHError

SSH 相关异常的基类。

#### SSHConnectionError

SSH 连接失败时抛出。

**常见原因：**
- 主机不可达
- 认证失败
- 网络超时

```python
from remote_cmd.utils.exceptions import SSHConnectionError

try:
    client.connect()
except SSHConnectionError as e:
    print(f"连接失败: {e}")
    # 可能的处理：
    # - 检查网络连接
    # - 验证凭据
    # - 检查防火墙设置
```

#### SSHCommandError

命令执行失败时抛出。

```python
from remote_cmd.utils.exceptions import SSHCommandError

try:
    result = client.execute("invalid_command")
except SSHCommandError as e:
    print(f"命令执行失败: {e}")
```

#### SSHFileTransferError

文件传输失败时抛出。

```python
from remote_cmd.utils.exceptions import SSHFileTransferError

try:
    client.upload_file("./local.txt", "/remote/path/")
except SSHFileTransferError as e:
    print(f"文件传输失败: {e}")
```

#### ConfigError

配置错误时抛出。

#### ValidationError

输入验证失败时抛出。

---

## 版本兼容性

| API | 版本 | 状态 |
|-----|------|------|
| SSHClient | 1.0.0+ | ✅ 稳定 |
| ConnectionConfig | 1.0.0+ | ✅ 稳定 |
| CommandResult | 1.0.0+ | ✅ 稳定 |
| HostManager | 1.x（已移除） | ❌ 已移除（改用 HostService + HostRepository） |
| AsyncSSHClient | 1.1.0+ | ✅ 稳定（需 `[async]` extra） |
| AsyncConnectionPool | 1.1.0+ | ✅ 稳定（需 `[async]` extra） |
| AsyncBatchExecutor | 1.1.0+ | ✅ 稳定（需 `[async]` extra） |
| SyncConnectionPool | 1.1.1+ | ✅ 稳定 |
| HostService | 1.0.0+ | ✅ 稳定（推荐架构） |
| CLI | 1.0.0+ | ✅ 稳定 |

---

## 反馈

如果您发现 API 文档有误或需要补充，请提交 [Issue](https://github.com/Vae-Scrooge/remote-cmd/issues)。

---

**最后更新：** 2026-08-01（v1.1.0 起新增异步模块文档，v1.1.1 新增 SyncConnectionPool）
