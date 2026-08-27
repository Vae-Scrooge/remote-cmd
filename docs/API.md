# API Usage Guide

This document provides usage examples and explanations for the public API of Remote CMD (maintained by hand).

> 📚 **Complete API reference (auto-generated):** see the [pdoc-generated API docs](./api/remote_cmd.html).
> This document focuses on usage examples; the full signatures/parameters/return types are authoritative in the auto-generated version.

## Table of Contents

- [Core Module](#core-module)
  - [ConnectionConfig](#connectionconfig)
  - [SSHClient](#sshclient)
  - [CommandResult](#commandresult)
  - [RemoteFileEntry](#remotefileentry)
  - [AsyncConnectionPool](#asyncconnectionpool)
  - [SyncConnectionPool](#syncconnectionpool)
  - [Host](#host)
- [Service Module](#service-module)
  - [HostService](#hostservice)
  - [HostRepository](#hostrepository)
  - [BatchExecutor](#batchexecutor)
  - [AsyncBatchExecutor](#asyncbatchexecutor)
- [CLI Module](#cli-module)
- [Utils Module](#utils-module)
- [Exceptions](#exceptions)
- [Version Compatibility](#version-compatibility)

---

## Core Module

### ConnectionConfig

SSH connection configuration class for setting SSH connection parameters.

#### Class Definition

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
    host_key_policy: Optional[Any] = None
    known_hosts_file: Optional[str] = None
```

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hostname` | `str` | required | Server address (IP or domain) |
| `username` | `str` | required | SSH username |
| `port` | `int` | 22 | SSH port |
| `password` | `Optional[str]` | None | Password (either this or `key_filename` is optional; password takes precedence when both are given) |
| `key_filename` | `Optional[str]` | None | Path to the SSH private key |
| `timeout` | `int` | 30 | Connection timeout (seconds) |
| `compress` | `bool` | True | Whether to enable compression |
| `host_key_policy` | `Optional[Any]` | None | Host key policy; defaults to `RejectPolicy` rejecting unknown keys |
| `known_hosts_file` | `Optional[str]` | None | Optional path to a known_hosts file |

#### Usage Example

```python
from remote_cmd.core.ssh_client import ConnectionConfig

# Password authentication
config1 = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="secret123"
)

# Key authentication
config2 = ConnectionConfig(
    hostname="example.com",
    username="deploy",
    key_filename="~/.ssh/id_rsa",
    port=2222,
    timeout=60
)
```

#### Exceptions

- `ValueError`: invalid hostname, username, or port

Neither password nor key is required; in that case the client can use the SSH agent. When both are provided, password authentication takes precedence.

---

### SSHClient

SSH client class for managing SSH connections and executing remote operations.

#### Class Definition

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
    def list_remote_directory(self, remote_path: str = ".") -> List[RemoteFileEntry]
    def is_connected(self) -> bool
```

#### Constructor

##### `__init__(config: ConnectionConfig)`

Initialize the SSH client.

**Parameters:**

- `config` (ConnectionConfig): the connection configuration object

**Example:**

```python
config = ConnectionConfig(hostname="example.com", username="admin", password="pass")
client = SSHClient(config)
```

#### Methods

##### `connect() -> SSHClient`

Establish the SSH connection.

**Returns:**

- `SSHClient`: returns self, supporting chained calls

**Exceptions:**

- `SSHConnectionError`: raised on connection failure
- `SSHAuthenticationError`: authentication failure (a permanent subclass of `SSHConnectionError`)
- `SSHTimeoutError`: connection-establishment timeout (a transient subclass of `SSHConnectionError`)

**Example:**

```python
client = SSHClient(config).connect()
# or
client = SSHClient(config)
client.connect()
```

##### `disconnect() -> None`

Close the SSH connection and clean up resources.

**Example:**

```python
client.disconnect()
```

##### `execute(command: str, timeout: Optional[int] = None, environment: Optional[Dict[str, str]] = None) -> CommandResult`

Execute a command on the remote server.

**Parameters:**

- `command` (str): the command string to execute
- `timeout` (Optional[int]): wall-clock command execution timeout (seconds); no timeout by default; on timeout the channel is closed and `SSHCommandTimeoutError` is raised
- `environment` (Optional[Dict[str, str]]): environment variable dictionary; keys must be valid shell identifiers

**Returns:**

- `CommandResult`: the command execution result object

**Exceptions:**

- `SSHConnectionError`: raised when not connected
- `SSHCommandError`: raised when command execution fails
- `SSHCommandTimeoutError`: the command did not complete within the wall-clock timeout
- `ValidationError`: invalid environment variable name

**Example:**

```python
# Simple execution
result = client.execute("ls -la")

# With timeout
result = client.execute("sleep 10", timeout=5)

# With environment variables
result = client.execute(
    "echo $MY_VAR",
    environment={"MY_VAR": "hello"}
)

# Check the result
if result.success:
    print(f"Output: {result.stdout}")
else:
    print(f"Error: {result.stderr}")
```

##### `execute_sudo(command: str, password: Optional[str] = None, timeout: Optional[int] = None) -> CommandResult`

Execute a command with sudo privileges.

**Parameters:**

- `command` (str): the command to execute
- `password` (Optional[str]): sudo password (omit if passwordless sudo is configured)
- `timeout` (Optional[int]): wall-clock command execution timeout (seconds); no timeout by default; on timeout the channel is closed and `SSHCommandTimeoutError` is raised

**Returns:**

- `CommandResult`: the command execution result

**Example:**

```python
# With password
result = client.execute_sudo("apt update", password="sudopass")

# Passwordless sudo
result = client.execute_sudo("systemctl restart nginx")
```

##### `upload_file(local_path: str, remote_path: str) -> None`

Upload a file to the remote server.

**Parameters:**

- `local_path` (str): local file path
- `remote_path` (str): remote destination path

**Exceptions:**

- `SSHFileTransferError`: raised on transfer failure
- `SSHConnectionError`: raised when not connected

**Example:**

```python
client.upload_file("./local_script.sh", "/tmp/remote_script.sh")
```

##### `download_file(remote_path: str, local_path: str) -> None`

Download a file from the remote server.

**Parameters:**

- `remote_path` (str): remote file path
- `local_path` (str): local save path

**Exceptions:**

- `SSHFileTransferError`: raised on transfer failure
- `SSHConnectionError`: raised when not connected

**Example:**

```python
client.download_file("/var/log/nginx/error.log", "./logs/error.log")
```

##### `list_remote_directory(remote_path: str = ".") -> List[RemoteFileEntry]`

List the contents of a remote directory.

**Parameters:**

- `remote_path` (str): remote directory path, defaults to the current directory

**Returns:**

- `List[RemoteFileEntry]`: list of file/directory info; each entry is accessed via attributes:
  - `name` (str): file name
  - `size` (int): file size (bytes)
  - `mode` (str): permission mode (e.g. "644")
  - `mtime` (int): modification timestamp
  - `is_dir` (bool): whether it is a directory

**Exceptions:**

- `SSHFileTransferError`: raised when listing the directory fails
- `SSHConnectionError`: raised when not connected

**Example:**

```python
entries = client.list_remote_directory("/var/www")
for entry in entries:
    type_icon = "📁" if entry.is_dir else "📄"
    print(f"{type_icon} {entry.name}: {entry.size} bytes")
```

##### `is_connected() -> bool`

Check whether the SSH connection is active.

**Returns:**

- `bool`: connection status

**Example:**

```python
if client.is_connected():
    print("Connected")
else:
    print("Disconnected")
```

#### Context Manager

`SSHClient` supports the context manager protocol, ensuring resources are released correctly:

```python
# Recommended usage
with SSHClient(config) as client:
    result = client.execute("uptime")
    # Connection auto-closes

# Equivalent to
try:
    client = SSHClient(config).connect()
    result = client.execute("uptime")
finally:
    client.disconnect()
```

---

### CommandResult

Command execution result class.

#### Class Definition

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

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `command` | `str` | The executed command |
| `stdout` | `str` | Standard output |
| `stderr` | `str` | Standard error |
| `exit_code` | `int` | Exit code |
| `success` | `bool` | Whether successful (exit_code == 0) |

#### Usage Example

```python
result = client.execute("ls -la")

# Check success
if result.success:
    print(f"Command output:\n{result.stdout}")
else:
    print(f"Command failed (exit code: {result.exit_code})")
    print(f"Error info:\n{result.stderr}")

# Print a result summary
print(result)  # Output: ✓ [0] ls -la
```

---

### RemoteFileEntry

A dataclass for remote file/directory entries returned by `SSHClient.list_remote_directory()` and `AsyncSSHClient.list_remote_directory()`. Access fields via attributes, not dict subscripting.

```python
for entry in client.list_remote_directory("/var/log"):
    print(entry.name, entry.size, entry.is_dir)
```

---

### Host

Host configuration dataclass.

#### Class Definition

```python
from dataclasses import dataclass, field

@dataclass
class Host:
    name: str
    hostname: str
    username: str
    port: int = 22
    password: Optional[str] = None
    key_filename: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    description: str = ""

    def to_connection_config(self) -> ConnectionConfig
    def to_dict(self) -> Dict[str, Any]
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Host"
```

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Host alias (unique identifier) |
| `hostname` | `str` | required | Server address |
| `username` | `str` | required | SSH username |
| `port` | `int` | 22 | SSH port |
| `password` | `Optional[str]` | None | Password |
| `key_filename` | `Optional[str]` | None | SSH key path |
| `tags` | `List[str]` | `[]` | Tag list |
| `description` | `str` | "" | Description |

#### Methods

##### `to_connection_config() -> ConnectionConfig`

Convert to a `ConnectionConfig` object.

**Returns:**

- `ConnectionConfig`: the connection configuration object

**Example:**

```python
host = Host(name="web", hostname="192.168.1.10", username="admin")
config = host.to_connection_config()
client = SSHClient(config)
```

##### `to_dict() -> Dict[str, Any]`

Convert to a dictionary.

**Returns:**

- `Dict[str, Any]`: the host configuration dictionary

##### `from_dict(data: Dict[str, Any]) -> Host`

Create a `Host` object from a dictionary.

**Parameters:**

- `data` (Dict[str, Any]): the host configuration dictionary

**Returns:**

- `Host`: the Host object

**Example:**

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

Host service class carrying the business logic of host management, delegating persistence to `HostRepository`.

#### Class Definition

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

#### Constructor

##### `__init__(repository: HostRepository, encryption=None, credential_provider=None)`

Initialize the host service.

**Parameters:**

- `repository` (HostRepository): the storage repository (`JsonHostRepository` / `SqliteHostRepository`)
- `encryption` (Optional[CredentialEncryption]): password encryptor (optional)
- `credential_provider` (Optional[CredentialProvider]): credential provider chain (optional)

**Example:**

```python
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

# Load from a file
repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
```

#### Methods

##### `add_host(host: Host) -> None`

Add a host.

**Parameters:**

- `host` (Host): the Host object

**Exceptions:**

- `ValueError`: host name already exists

**Example:**

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

Remove a host.

**Parameters:**

- `name` (str): the host name

**Exceptions:**

- `KeyError`: host does not exist

**Example:**

```python
manager.remove_host("web-server")
repo.flush()
```

##### `get_host(name: str) -> Host`

Get a host.

**Parameters:**

- `name` (str): the host name

**Returns:**

- `Host`: the host object

**Exceptions:**

- `KeyError`: host does not exist

**Example:**

```python
host = manager.get_host("web-server")
print(f"Host address: {host.hostname}")
```

##### `list_hosts(tag: Optional[str] = None) -> List[Host]`

List all hosts, optionally filtered by tag.

**Parameters:**

- `tag` (Optional[str]): tag filter (optional)

**Returns:**

- `List[Host]`: the host list

**Example:**

```python
# All hosts
all_hosts = manager.list_hosts()

# Production hosts
prod_hosts = manager.list_hosts(tag="production")

# Web servers
web_hosts = manager.list_hosts(tag="web")
```

##### `list_tags() -> List[str]`

List all tags.

**Returns:**

- `List[str]`: the tag list (sorted)

**Example:**

```python
tags = manager.list_tags()
print(f"Available tags: {', '.join(tags)}")
```

##### `connect_to_host(name: str) -> SSHClient`

Connect to the specified host.

**Parameters:**

- `name` (str): the host name

**Returns:**

- `SSHClient`: a connected SSHClient instance

**Exceptions:**

- `KeyError`: host does not exist
- `SSHConnectionError`: connection failed

**Example:**

```python
with manager.connect_to_host("web-server") as client:
    result = client.execute("uptime")
    print(result.stdout)
```

##### `test_connection(name: str) -> bool`

Test the connection.

**Parameters:**

- `name` (str): the host name

**Returns:**

- `bool`: whether the connection succeeded

**Example:**

```python
if manager.test_connection("web-server"):
    print("Connection succeeded")
else:
    print("Connection failed")
```

##### `test_all_connections() -> Dict[str, bool]`

Test all host connections.

**Returns:**

- `Dict[str, bool]`: mapping of host name to connection status

**Example:**

```python
results = manager.test_all_connections()
for name, success in results.items():
    status = "✓" if success else "✗"
    print(f"{status} {name}")
```

---

### HostRepository

The abstract interface for host storage, defining the persistence contract for host configuration.

#### Class Definition

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

#### Implementations

| Class | Storage Format | Use Case |
|-------|----------------|----------|
| `JsonHostRepository` | JSON file (atomic write, optional encryption) | Default, lightweight config |
| `SqliteHostRepository` | SQLite database (indexing, pagination, search) | Large host counts |

#### Automatic Storage Engine Switching

You can use `remote_cmd.service.storage_factory.build_repository` to auto-select the storage engine by file extension (`.json` / `.db` / `.sqlite`), or explicitly specify `storage_backend`:

```python
from remote_cmd.service.storage_factory import build_repository

repo = build_repository("hosts.json")          # JsonHostRepository
repo = build_repository("hosts.db")            # SqliteHostRepository
repo = build_repository("hosts.json", storage_backend="sqlite")  # explicit wins
```

---

## Async Module (v1.1.0+)

> The async module is based on `asyncssh` and requires an extra install:
> ```bash
> pip install "remote_cmd_manager[async]"
> ```
> When asyncssh is not installed, `import remote_cmd` still works, but the async symbols are not exported.

### AsyncSSHClient

Native async SSH client based on asyncssh, with an API consistent with the synchronous `SSHClient`.

#### Class Definition

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
    async def list_remote_directory(self, remote_path: str = ".") -> List[RemoteFileEntry]
```

#### Constructor

##### `__init__(config: ConnectionConfig, loop: Optional[Any] = None)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `ConnectionConfig` | required | SSH connection config (same as the sync version) |
| `loop` | `Optional[Any]` | None | Ignored; asyncssh takes the running event loop itself, kept only for backward compatibility |

#### Usage Example

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

#### Differences from Synchronous SSHClient

| Feature | `SSHClient` | `AsyncSSHClient` |
|---------|-------------|------------------|
| Underlying | paramiko | asyncssh |
| Invocation | synchronous blocking | `async`/`await` |
| Context manager | synchronous | asynchronous (`async with`) |
| Use case | simple scripts, CLI | high-concurrency batch |
| Return type | `CommandResult` | `CommandResult` (consistent) |

`AsyncSSHClient.execute`'s `timeout` is treated by asyncssh as a command execution timeout; `environment` keys must be valid shell identifiers and values are safely escaped.

#### Exceptions

- `SSHConnectionError`: connection failure (includes `SSHTimeoutError` and missing key file)
- `SSHAuthenticationError`: authentication failure (a permanent subclass of `SSHConnectionError`)
- `SSHTimeoutError`: connection-establishment timeout (a transient subclass of `SSHConnectionError`)
- `SSHCommandError`: command execution failure
- `SSHFileTransferError`: file transfer failure
- `ValidationError`: invalid environment variable name

---

### AsyncConnectionPool

Native async SSH connection pool that reuses connections with idle/lifetime recycling and health checks.

#### Class Definition

```python
class AsyncConnectionPool:
    def __init__(self, config: ConnectionConfig, max_connections: int = 10,
                 max_lifetime: int = 3600, idle_timeout: int = 300,
                 health_check_interval: int = 60,
                 client_factory: Optional[Any] = None)
    async def acquire(self) -> AsyncSSHClient
    async def release(self, conn: Optional[AsyncSSHClient]) -> None
    def acquire_context(self) -> "_AcquireContext"
    def get_metrics(self) -> Dict[str, Any]
    def stop_monitor(self) -> None
    async def close_all(self) -> None
```

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `ConnectionConfig` | required | Config used to establish SSH connections |
| `max_connections` | `int` | 10 | Maximum number of connections |
| `max_lifetime` | `int` | 3600 | Maximum connection lifetime (seconds), auto-closed when exceeded |
| `idle_timeout` | `int` | 300 | Idle timeout (seconds), auto-closed when exceeded |
| `health_check_interval` | `int` | 60 | Background cleanup task interval (seconds) |
| `client_factory` | `Optional[Any]` | `None` | Optional client factory, defaults to `AsyncSSHClient` |

After `close_all()` the pool cannot be borrowed from again; if `acquire()` was already waiting on the semaphore, it will raise `RuntimeError("connection pool is closed")` when woken after the pool closes.

#### Usage Example

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
    # Or manage manually:
    # client = await pool.acquire()
    # await pool.release(client)

asyncio.run(main())
```

#### Metrics Returned by `get_metrics()`

| Key | Description |
|-----|-------------|
| `active` | Current active connections |
| `idle` | Idle connections |
| `total_connections` | Total connections held by the pool |
| `total_created` | Cumulative created count |
| `reconnects` | Cumulative reconnect count |
| `failed` | Cumulative failure count |

---

### SyncConnectionPool

Synchronous SSH connection pool for reusing connections in frequently repeated short-command scenarios (batch execution, connection tests), avoiding a new handshake each time. Symmetric design with `AsyncConnectionPool`.

#### Class Definition

```python
class SyncConnectionPool:
    def __init__(self, config: ConnectionConfig, max_connections: int = 10,
                 max_lifetime: int = 3600, idle_timeout: int = 300,
                 health_check_interval: int = 60,
                 client_factory: Optional[Any] = None)
    def acquire(self) -> SSHClient
    def release(self, conn: Optional[SSHClient]) -> None
    def acquire_context(self) -> SyncConnectionPool._AcquireContext
    def get_metrics(self) -> Dict[str, Any]
    def close_all(self) -> None
```

#### Usage Example

```python
from remote_cmd.core.ssh_client import ConnectionConfig
from remote_cmd.core.sync_connection_pool import SyncConnectionPool

config = ConnectionConfig(hostname="192.168.1.100", username="admin")
pool = SyncConnectionPool(config, max_connections=5)

with pool.acquire_context() as client:
    result = client.execute("uptime")

pool.close_all()
```

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `ConnectionConfig` | required | Connection config |
| `max_connections` | `int` | 10 | Maximum number of connections |
| `max_lifetime` | `int` | 3600 | Maximum connection lifetime (seconds) |
| `idle_timeout` | `int` | 300 | Idle timeout (seconds) |
| `health_check_interval` | `int` | 60 | Background cleanup thread interval (seconds) |
| `client_factory` | `Optional[Any]` | `None` | Optional client factory, defaults to `SSHClient` |

After `close_all()` the pool cannot be borrowed from again; if `acquire()` was already waiting on the semaphore, it will raise `RuntimeError("connection pool is closed")` when woken after the pool closes.

#### Metrics (get_metrics)

Same keys as `AsyncConnectionPool`: `active` / `idle` / `total_connections` / `total_created` / `reconnects` / `failed`.

> **Note**: `BatchExecutor` (sync kernel) and `AsyncBatchExecutor` (async kernel) automatically reuse per-host connections in multi-host or retry batches, and close the internally created pool after a single batch completes. An external pool injected via `pool_factory` is the caller's responsibility for lifecycle; the executor never closes it.

---

### BatchExecutor

Synchronous batch command executor using `ThreadPoolExecutor`; in multi-host or retry scenarios it uses `SyncConnectionPool` per host by default. When `use_async=True` is passed, it keeps a synchronous `execute()` interface externally but internally delegates to `AsyncBatchExecutor`'s native asyncssh kernel.

#### Class Definition

```python
class BatchExecutor:
    def __init__(self, host_service: HostService, max_concurrency: int = 10,
                 command_timeout: int = 30, use_async: bool = False,
                 pool_factory: Optional[Callable] = None)
    def execute(self, host_names: List[str], command: str,
                retry_count: int = 0, retry_delay: float = 1.0,
                progress_callback: Optional[ProgressCallback] = None) -> BatchResult
```

#### Connection Pool Ownership

- In multi-host or retry scenarios, when `pool_factory` is not provided the executor creates an internal per-host pool, closed automatically after `execute()` completes.
- When `pool_factory` is provided, the pool returned by the factory is caller-owned; the executor only borrows it and never closes it, suitable for long-lived services reusing pools across batches.
- With `use_async=True` the factory must return an async pool; the sync kernel must return a sync pool.

#### Retry Behavior

Authentication, credential, configuration, validation, and programming errors are not retried. Unrecognized `Exception` subclasses remain retryable to stay compatible with custom client factories; custom factories should prefer raising typed `remote_cmd` exceptions. `retry_delay` is the exponential-backoff base delay, with full jitter applied and capped at 60 seconds. Unknown hosts become individual failure results and do not abort other hosts; duplicate host names are executed once.

`use_async=True` must not be called inside an already-running event loop; use `await AsyncBatchExecutor.execute()` directly instead.

### AsyncBatchExecutor

Native async batch command executor based on `asyncio.Semaphore` to control concurrency.

#### Class Definition

```python
class AsyncBatchExecutor:
    def __init__(self, host_service: HostService, max_concurrency: int = 10,
                 command_timeout: int = 30,
                 pool_factory: Optional[Callable] = None)
    async def execute(self, host_names: List[str], command: str,
                      retry_count: int = 0, retry_delay: float = 1.0,
                      progress_callback: Optional[ProgressCallback] = None) -> BatchResult
```

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host_service` | `HostService` | required | Host service (provides host config and credential resolution) |
| `max_concurrency` | `int` | 10 | Maximum concurrent hosts |
| `command_timeout` | `int` | 30 | Single-command timeout (seconds) |
| `pool_factory` | `Optional[Callable]` | `None` | External async connection pool factory; the returned pool is caller-owned and not closed by the executor |

The `execute` parameters are identical to the synchronous `BatchExecutor.execute`: `retry_count` is the number of retries on failure, `retry_delay` is the exponential-backoff base delay (with full jitter, capped at 60s), and `progress_callback` is a progress callback `(completed, total, host_name)` (may be sync or async).

In multi-host or retry scenarios, when `pool_factory` is not provided the executor creates an internal `AsyncConnectionPool` per host, closed automatically after the batch; when `pool_factory` is provided the external pool is the caller's responsibility for lifecycle and is never closed by the executor. Unknown hosts return individual failure results, and duplicate host names are executed once.

#### Usage Example

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

#### Return Value

`BatchResult`, identical to the synchronous `BatchExecutor` (`total` / `success` / `failed` / `duration` / `results`, plus `success_rate` / `failed_hosts` / `summary()`).

#### Exceptions

- `ValueError`: `host_names` is empty, or `retry_count` / `retry_delay` is negative

#### Relationship to Synchronous BatchExecutor

`BatchExecutor(host_service, use_async=True)` delegates internally to `AsyncBatchExecutor`, keeping a synchronous interface externally. Both share the exact same data contract (`BatchResult` / `BatchHostResult`) and are interchangeable. But `BatchExecutor(use_async=True)` must not be called inside an active event loop; use `await AsyncBatchExecutor.execute()` directly in that case.

---

## CLI Module

### Command Line Interface

Remote CMD provides a complete command-line tool.

#### Global Options

```bash
# Version info
remote-cmd --version

# Help
remote-cmd --help

# Specify a config file
remote-cmd --config /path/to/config.yaml <command>

# Verbose output
remote-cmd --verbose <command>
```

#### host command group

##### host add

Add a host.

```bash
remote-cmd host add <name> <hostname> <username> [options]

Options:
  -p, --port INTEGER          SSH port (default: 22)
  -k, --key TEXT              SSH private key file path
  -t, --tag TEXT              Tag (repeatable)
  -d, --description TEXT      Description
```

When no key is provided, the command prompts for the password interactively via `getpass`; you can also use the `REMOTE_CMD_PASSWORD` environment variable — passwords are never passed as command-line arguments.

**Example:**

```bash
remote-cmd host add web-server 192.168.1.10 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag production \
    --tag web \
    --description "Production web server"
```

##### host list

List hosts.

```bash
remote-cmd host list [options]

Options:
  -t, --tag TEXT              Filter by tag
```

**Example:**

```bash
# List all hosts
remote-cmd host list

# Only web servers
remote-cmd host list --tag web
```

##### host remove

Remove a host.

```bash
remote-cmd host remove <name>
```

**Note:** confirmation is required.

**Example:**

```bash
remote-cmd host remove web-server
```

##### host test

Test the connection.

```bash
remote-cmd host test <name>
```

**Example:**

```bash
remote-cmd host test web-server
```

#### run command

Execute a remote command.

```bash
remote-cmd run <host_name> <command>
```

Options:

```text
  -T, --timeout INTEGER       Command execution wall-clock timeout (default: none)
```

**Example:**

```bash
remote-cmd run my-server "ls -la"
remote-cmd run my-server "systemctl status nginx"
remote-cmd run my-server "df -h"
```

#### batch-run command

Execute a command in batch across specified hosts; the command currently accepts a list of host names, not a tag filter argument.

```bash
remote-cmd batch-run <host_name>... <command> [options]
```

Options include `-C/--concurrency`, `-T/--timeout`, `-r/--retry`, `--retry-delay`, `--async`, and `--show-failures`. `--retry-delay` is the exponential-backoff base delay, with full jitter applied and capped at 60 seconds.

#### upload command

Upload a file.

```bash
remote-cmd upload <host_name> <local_path> <remote_path>
```

**Example:**

```bash
remote-cmd upload my-server ./app.tar.gz /tmp/app.tar.gz
```

#### download command

Download a file.

```bash
remote-cmd download <host_name> <remote_path> <local_path>
```

**Example:**

```bash
remote-cmd download my-server /var/log/nginx/error.log ./logs/
```

---

## Utils Module

### Config Management

```python
from remote_cmd.utils.config import load_config, save_config, get_default_config_path

# Load config
config = load_config("config.yaml")

# Save config
save_config(config, "config.yaml")

# Get the default config path
path = get_default_config_path()
```

#### load_config(config_path: str) -> Dict[str, Any]

Load a config file.

**Parameters:**

- `config_path` (str): the config file path

**Returns:**

- `Dict[str, Any]`: the config dictionary

**Supported formats:**

- YAML (.yaml, .yml)
- JSON (.json)

#### save_config(config: Dict[str, Any], config_path: str) -> None

Save a config file.

**Parameters:**

- `config` (Dict[str, Any]): the config dictionary
- `config_path` (str): the config file path

#### get_default_config_path() -> str

Get the default config file path.

**Returns:**

- `str`: the config file path

---

## Exceptions

### Exception Hierarchy

```
RemoteCmdError (Base)
├── SSHError
│   ├── SSHConnectionError
│   │   ├── SSHAuthenticationError (permanent, not retried)
│   │   └── SSHTimeoutError (transient, retried)
│   ├── SSHCommandError
│   │   └── SSHCommandTimeoutError (transient, retried)
│   └── SSHFileTransferError
├── ConfigError (= ConfigurationError)
├── CredentialError
└── ValidationError
```

### Exception Classes

#### RemoteCmdError

Base class for all exceptions.

```python
from remote_cmd.utils.exceptions import RemoteCmdError

try:
    # ...
except RemoteCmdError as e:
    print(f"Remote CMD error: {e}")
```

#### SSHError

Base class for SSH-related exceptions.

#### SSHConnectionError

Raised when an SSH connection fails.

**Common causes:**

- Host unreachable
- Authentication failure
- Network timeout (subdivided into `SSHTimeoutError`)

```python
from remote_cmd.utils.exceptions import SSHConnectionError

try:
    client.connect()
except SSHConnectionError as e:
    print(f"Connection failed: {e}")
    # Possible handling:
    # - check network connectivity
    # - verify credentials
    # - check firewall settings
```

`SSHAuthenticationError` is a permanent subclass for auth failure and is not retried by batch executors; `SSHTimeoutError` is a transient subclass for connection timeout and may be retried per policy.

#### SSHCommandError

Raised when command execution fails.

```python
from remote_cmd.utils.exceptions import SSHCommandError

try:
    result = client.execute("invalid_command")
except SSHCommandError as e:
    print(f"Command execution failed: {e}")
```

Command execution timeout raises `SSHCommandTimeoutError` (a transient subclass of `SSHCommandError`).

#### CredentialError

Raised when local credential parsing or decryption fails; it is a permanent error and is not retried. `CredentialEncryptionError` is its subclass.

#### ConfigurationError

`ConfigurationError` is a compatibility alias of `ConfigError`.

#### SSHFileTransferError

Raised when a file transfer fails.

```python
from remote_cmd.utils.exceptions import SSHFileTransferError

try:
    client.upload_file("./local.txt", "/remote/path/")
except SSHFileTransferError as e:
    print(f"File transfer failed: {e}")
```

#### ConfigError

Raised on a configuration error.

#### ValidationError

Raised on input validation failure.

---

## Version Compatibility

| API | Version | Status |
|-----|---------|--------|
| SSHClient | 1.0.0+ | ✅ Stable |
| ConnectionConfig | 1.0.0+ | ✅ Stable |
| CommandResult | 1.0.0+ | ✅ Stable |
| HostManager | 1.0.0+ | ⚠️ Deprecated (backward-compat layer delegating to HostService + JsonHostRepository; new code should use HostService) |
| AsyncSSHClient | 1.1.0+ | ✅ Stable (requires the `[async]` extra) |
| AsyncConnectionPool | 1.1.0+ | ✅ Stable (requires the `[async]` extra) |
| AsyncBatchExecutor | 1.1.0+ | ✅ Stable (requires the `[async]` extra) |
| SyncConnectionPool | 1.1.1+ | ✅ Stable |
| BatchExecutor | 1.0.0+ | ✅ Stable (v2.1 supports `pool_factory`) |
| HostService | 1.0.0+ | ✅ Stable (recommended architecture) |
| CLI | 1.0.0+ | ✅ Stable |

---

## Feedback

If you find errors in the API documentation or want to supplement it, please open an [Issue](https://github.com/Vae-Scrooge/remote-cmd/issues).

---

**Last updated:** 2026-08-23 (v2.1.0 release audit)
