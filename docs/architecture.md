# System Architecture

This document describes the system architecture, design decisions, and technical implementation details of Remote CMD.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Layered Architecture](#layered-architecture)
- [Core Components](#core-components)
- [Data Flow](#data-flow)
- [Design Patterns](#design-patterns)
- [Extensibility](#extensibility)
- [Performance Considerations](#performance-considerations)
- [Security Design](#security-design)

---

## Architecture Overview

Remote CMD uses a layered architecture that divides the system into clearly separated layers with distinct responsibilities, ensuring maintainability and extensibility.

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                       User Interaction Layer                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   CLI Tool   │  │  Python API  │  │   Config Management   │  │
│  │  remote-cmd  │  │ Programmatic │  │  YAML/JSON/Env Vars   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼──────────────────┼─────────────────────┼──────────────┘
           │                  │                     │
           ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Business Logic Layer                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │          HostService + HostRepository (Host Service)      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │   │
│  │  │   Add Host   │  │  Remove Host │  │   Query Host │   │   │
│  │  │  Tag Manage  │  │ Batch Ops   │  │  Conn Test   │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │   │
│  └────────────────────┬────────────────────────────────────┘   │
└───────────────────────┼────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Core Function Layer                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                  SSHClient (SSH Client)                  │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │   │
│  │  │  Conn Manage │  │  Cmd Execute │  │  File Transfer│   │   │
│  │  │  Auth Handle│  │ Output Handle│  │  SFTP Ops    │   │   │
│  │  │ Session Mgmt│  │ Timeout Ctl  │  │  Dir Ops     │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │   │
│  └────────────────────┬────────────────────────────────────┘   │
└───────────────────────┼────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Network Transport Layer                    │
│                   Paramiko (SSHv2 Protocol)                      │
│         Supports: password auth, key auth, SFTP, port forward    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layered Architecture

### 1. User Interaction Layer

Responsible for interacting with the user and providing multiple usage modes.

#### CLI Tool (`remote_cmd/cli/`)

- Built on the Click framework
- Provides a friendly command-line interface
- Supports subcommands and argument parsing
- Colorized output and progress display

#### Python API (`remote_cmd/core/`)

- Provides a programmatic interface
- Supports type hints and IDE autocomplete
- Context managers ensure resource release

#### Config Management (`remote_cmd/utils/config.py`)

- Supports YAML/JSON formats
- Environment variable overrides
- Default configuration management

### 2. Business Logic Layer

#### HostService + HostRepository (`remote_cmd/service/host_service.py` + `remote_cmd/repository/`)

Core responsibilities of the host service:

- CRUD operations on host configurations
- Tag classification and filtering
- Batch operation support
- Data persistence (JsonHostRepository / SqliteHostRepository / automatic storage engine switching)

**Design decisions:**

- `Repository` defines the storage interface; Json/SQLite implementations are pluggable and switchable
- `HostService` carries the business logic and credential resolution, decoupled from storage
- The storage engine is auto-selected by file extension (`.json`/`.db`/`.sqlite`) or an explicit `storage_backend`

### 3. Core Function Layer

#### SSHClient (`remote_cmd/core/ssh_client.py`)

Core functions of the SSH client:

- Connection management (establish, maintain, disconnect)
- Command execution (synchronous; see `AsyncSSHClient` for the async implementation)
- File transfer (SFTP)
- Session reuse

**Design decisions:**

- Use a context manager to ensure connections are closed
- Exception translation for unified error handling
- Supports `SyncConnectionPool` / `AsyncConnectionPool`; batch executors reuse connections on demand

### 4. Network Transport Layer

Uses the Paramiko library to implement the SSHv2 protocol:

- Password authentication
- Public key authentication (RSA/ED25519)
- SFTP file transfer
- Port forwarding (future extension)

---

## Core Components

### SSHClient Component

```python
class SSHClient:
    """
    SSH Client Component

    Responsibilities:
    1. Manage the SSH connection lifecycle
    2. Execute remote commands
    3. Transfer files
    4. Handle exceptions
    """

    def __init__(self, config: ConnectionConfig):
        self.config = config
        self._client = None
        self._sftp = None

    def connect(self) -> "SSHClient":
        # Establish connection
        pass

    def execute(self, command: str) -> CommandResult:
        # Execute command
        pass

    def upload_file(self, local: str, remote: str):
        # Upload file
        pass
```

**Component relationships:**

```
SSHClient *-- ConnectionConfig
SSHClient o-- paramiko.SSHClient
SSHClient o-- paramiko.SFTPClient
```

### HostService Component

```python
class HostService:
    """
    Host Service Component

    Responsibilities:
    1. Manage the host collection
    2. Delegate persistence to the repository
    3. Provide filtering and querying
    4. Support batch operations
    """

    def __init__(self, repository: HostRepository):
        self._repository = repository

    def add_host(self, host: Host):
        # Add host
        pass

    def connect_to_host(self, name: str) -> SSHClient:
        # Connect to the specified host
        pass
```

**Component relationships:**

```
HostService o-- HostRepository
HostService *-- "*" Host
HostRepository <|.. JsonHostRepository
HostRepository <|.. SqliteHostRepository
HostService ..> SSHClient : creates
Host *-- ConnectionConfig
```

---

## Data Flow

### Command Execution Data Flow

```
User invocation
    │
    ▼
┌─────────────────┐
│  CLI/API Entry  │  Parse arguments, validate input
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   HostService   │  Look up host config
│   get_host()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   SSHClient     │  Establish connection
│    connect()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Paramiko     │  SSH protocol communication
│   SSHClient     │
└────────┬────────┘
         │
         ▼
    Remote server
         │
         ▼
┌─────────────────┐
│   SSHClient     │  Receive response
│    execute()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  CommandResult  │  Wrap the result
└────────┬────────┘
         │
         ▼
    Return to user
```

### File Transfer Data Flow

```
User calls upload_file()
         │
         ▼
┌──────────────────┐
│  Verify local     │
│  file exists      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Open SFTP        │  Reuse the existing SSH connection
│  channel          │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Paramiko SFTP   │  SFTP put operation
│     put()        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Verify transfer │  Check return value
│  result          │
└────────┬─────────┘
         │
         ▼
    Return success/failure
```

---

## Design Patterns

### 1. Context Manager Pattern

Used to automatically manage the resource lifecycle:

```python
# Use a context manager to auto-close the connection
with SSHClient(config) as client:
    result = client.execute("ls -la")
    # Connection auto-closes; no need to call disconnect() manually
```

**Benefits:**

- Prevents resource leaks
- Cleaner code
- Exception-safe

### 2. Strategy Pattern

The strategy pattern for authentication methods:

```python
# Different authentication strategies
config1 = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="secret"  # Password auth strategy
)

config2 = ConnectionConfig(
    hostname="example.com",
    username="admin",
    key_filename="~/.ssh/id_rsa"  # Key auth strategy
)
```

### 3. Factory Pattern

`HostService` acts as a factory for `SSHClient`:

```python
# HostService creates SSHClient
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
client = manager.connect_to_host("web-server")
# Returns a connected SSHClient instance
```

### 4. Dataclass Pattern

Use `@dataclass` to define data objects:

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

## Extensibility

### Plugin Architecture

Reserved extension points:

```python
# Custom auth plugin (future)
class AuthPlugin:
    def authenticate(self, client: SSHClient) -> bool:
        pass

# Custom command handler (future)
class CommandHandler:
    def before_execute(self, command: str) -> str:
        pass

    def after_execute(self, result: CommandResult):
        pass
```

### Hook System

Event hook design:

```python
# Connection hook
@hooks.connect
def on_connect(client: SSHClient):
    logger.info(f"Connected to {client.config.hostname}")

# Command execution hook
@hooks.execute
def on_execute(command: str, result: CommandResult):
    metrics.record_command(command, result.exit_code)
```

### Custom Backends

Supports custom SSH backends:

```python
# Abstract base class
class SSHBackend(ABC):
    @abstractmethod
    def connect(self, config: ConnectionConfig):
        pass

# Can be replaced with other implementations
class AsyncSSHBackend(SSHBackend):
    pass
```

---

## Performance Considerations

### Connection Reuse

Current implementation:

- When using `SSHClient` / `AsyncSSHClient` directly, each client instance represents one connection; the caller may execute multiple commands on the same client.
- The synchronous `BatchExecutor` uses `SyncConnectionPool` per host in multi-host or retry batches.
- The async `AsyncBatchExecutor` uses `AsyncConnectionPool` (`remote_cmd.core.async_connection_pool`) per host in multi-host or retry batches, based on asyncssh, reusing connections with idle/lifetime recycling and health checks.
- Both executors support `pool_factory` injection of an external pool; the external pool is caller-owned and never closed by the executor, while internally created pools are closed automatically after the batch completes.

```python
# Connection pool (implemented, used for async batch execution)
from remote_cmd.core.async_connection_pool import AsyncConnectionPool

pool = AsyncConnectionPool(config, max_connections=10)
client = await pool.acquire()   # Reuse an existing connection or create a new one
await pool.release(client)
```

### Batch Operation Optimization

Parallel execution example:

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

### File Transfer Optimization

- Enable compressed transfer
- Transfer large files in chunks
- Support resumable transfers (planned)

---

## Security Design

### Authentication Security

1. **Password management**
   - Passwords are never logged
   - Supports reading from password files
   - Passed via environment variables

2. **Key management**
   - Supports SSH Agent
   - Key file permission checks
   - Encrypted credential storage (`CredentialEncryption`)

### Transport Security

- Uses encrypted SSH channels
- Supports key fingerprint verification
- Host key checking is strict by default, configurable for controlled scenarios

### Config Security

```python
# Sensitive data handling
class SecureConfig:
    def get_password(self) -> str:
        # Retrieved from secure storage
        pass

    def mask_sensitive(self, data: dict) -> dict:
        # Mask sensitive data
        masked = data.copy()
        if 'password' in masked:
            masked['password'] = '***'
        return masked
```

---

## Tech Stack

### Core Dependencies

| Library | Version | Purpose |
|---------|---------|---------|
| paramiko | >=3.0.0 | SSH protocol implementation |
| click | >=8.0.0 | CLI framework |
| pyyaml | >=6.0 | YAML parsing |

### Development Tools

| Tool | Purpose |
|------|---------|
| pytest | Unit testing |
| black | Code formatting |
| flake8 | Code linting |
| mypy | Type checking |

---

## Roadmap

### Short Term

- [x] Connection pool support
- [x] Async API
- [x] Encrypted credential storage
- [x] Parallel batch execution

### Long Term

- [ ] Web UI
- [ ] Jump host / bastion support
- [ ] Ansible integration
- [ ] Docker container support

---

**Last updated:** 2026-08-23 (v2.1.0 release audit)
