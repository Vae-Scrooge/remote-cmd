<p align="center">
  <img src="https://img.shields.io/pypi/v/remote_cmd_manager?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI" alt="PyPI">
  <img src="https://img.shields.io/pypi/dm/remote_cmd_manager?style=for-the-badge&logo=python&logoColor=white&label=Downloads" alt="Downloads">
  <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=for-the-badge&logo=github" alt="Stars">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/github/license/Vae-Scrooge/remote-cmd?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/github/actions/workflow/status/Vae-Scrooge/remote-cmd/ci.yml?style=for-the-badge&logo=githubactions&label=CI" alt="CI">
</p>

<h1 align="center">Remote CMD — SSH Server Management<br><small>Without the Overhead</small></h1>

<p align="center">
  <img src="https://img.shields.io/badge/English-blue?style=flat-square" alt="English"> ·
  <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/中文-gray?style=flat-square" alt="中文"></a>
</p>

<p align="center">
  <b><code>pip install remote_cmd_manager</code></b> &nbsp;·&nbsp;
  <a href="#quick-start">Quick Start</a> &nbsp;·&nbsp;
  <a href="#use-cases">Use Cases</a> &nbsp;·&nbsp;
  <a href="#cli-reference">CLI Reference</a> &nbsp;·&nbsp;
  <a href="#python-api">Python API</a> &nbsp;·&nbsp;
  <a href="#documentation">Documentation</a> &nbsp;·&nbsp;
  <a href="#contributing">Contributing</a>
</p>

---

**Remote CMD** is a lightweight Python CLI + API for managing servers over SSH. Add hosts, run commands, transfer files, and organize hosts with tags — no Ansible DSL or shell loops required.

```bash
# One command to get started
pip install remote_cmd_manager && remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa && remote-cmd run web-01 "uptime"
```

---

## v2.1.0 Release Highlights

v2.1.0 contains both major implementation changes and a final release-hardening pass. See the
[full migration notes](./CHANGELOG.md) before upgrading automated callers.

### Major Implementation Changes

- Paramiko command execution drains stdout and stderr before retrieving the exit status, preventing SSH channel-window deadlocks on large output.
- Paramiko command timeouts are wall-clock enforced; a silent or hung command closes its channel and raises `SSHCommandTimeoutError`.
- `AsyncBatchExecutor` now uses `AsyncConnectionPool` for multi-host and retry workloads, reusing connections across attempts.
- `pool_factory` enables caller-supplied pools; external pools are caller-owned and never closed by either executor, while internally created pools are closed automatically after `execute()`.
- Retries classify permanent failures (authentication, credentials, configuration, validation, and programming errors) as non-retryable; unknown `Exception` subclasses remain retryable for backward compatibility. `retry_delay` is now the exponential-backoff base and full jitter is applied with a 60-second cap.
- Unknown hosts produce per-host failure results instead of aborting a multi-host batch, and duplicate host names are executed once.
- The exception hierarchy adds `SSHAuthenticationError`, `SSHTimeoutError`, `SSHCommandTimeoutError`, `CredentialError`, and the `ConfigurationError` alias without removing existing catch paths.
- Environment-variable names are validated before shell interpolation, and command text is excluded from library logs and execution errors.
- `remote-cmd run` supports `--timeout/-T` for command execution limits.

### Final Release Hardening

- Sync and async pools re-check their closed state after semaphore acquisition, preventing a shutdown race from issuing a connection from a closed pool.
- `BatchExecutor(use_async=True)` now reports an actionable error when called from an active event loop; use `AsyncBatchExecutor.execute()` there instead.
- The Paramiko stderr drain join is bounded to prevent an exceptional reader path from blocking the caller indefinitely.

## Table of Contents

- [v2.1.0 Release Highlights](#v210-release-highlights)
- [Why Remote CMD?](#why-remote-cmd)
- [Quick Start](#quick-start)
- [Use Cases](#use-cases)
- [CLI Reference](#cli-reference)
- [Python API](#python-api)
- [Features](#features)
- [Installation](#installation)
- [Documentation](#documentation)
- [Project Status](#project-status)
- [Maintainership](#maintainership)
- [Contributing](#contributing)
- [License](#license)

---

## Why Remote CMD?

| Feature | `remote-cmd` | `ssh` + shell | Ansible | Fabric |
|---|---|---|---|---|
| Host CRUD + tag groups | ✅ Built-in | ❌ Manual | ✅ Inventory | ❌ |
| Batch commands across hosts | ✅ `batch-run` | ❌ Write a loop | ✅ Playbook | ✅ |
| File transfer (upload/download) | ✅ Built-in | ✅ scp | ✅ copy module | ✅ |
| Python API | ✅ `from remote_cmd import ...` | ❌ | ❌ YAML-only | ✅ |
| Zero setup | ✅ `pip install → go` | ❌ Configure SSH | ❌ `ansible.cfg` | ❌ |
| Learning curve | **Low** | Low | **High** | Medium |

**Use `remote-cmd` when** you need a CLI that works immediately for ad-hoc SSH tasks. **Use Ansible when** you need full configuration management and idempotent playbooks.

---

## Quick Start

```bash
# 1. Install
pip install remote_cmd_manager

# 2. Add a server
remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa

# 3. Run a command
remote-cmd run web-01 "uptime"

# 4. Run across named production servers
remote-cmd batch-run web-01 web-02 db-01 "df -h /"
```

---

## Use Cases

### 🖥️ System Administrators — Check disk across 20 servers in one command

```bash
remote-cmd batch-run web-01 web-02 db-01 "df -h / | tail -1"
# Output:
#   ✓ web-01  → /dev/sda1  32G  12G  19G  40% /
#   ✓ web-02  → /dev/sda1  32G  28G   3G  90% /   ⚠️
#   ✗ db-01   → Connection refused
```

### 🚀 Deploy — Pull code and restart a service

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

### 🔥 Incident Response — Check logs across all servers

```bash
remote-cmd batch-run web-01 web-02 "journalctl -xe -n 50 | grep -i error"
```

### 🔧 Config Update — Upload and reload nginx across tagged hosts

```bash
# Upload new config, reload across web servers
remote-cmd run web-01 "sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t && sudo systemctl reload nginx"
```

---

## CLI Reference

All operations are available from the terminal:

| Command | Description |
|---|---|
| `remote-cmd host add <name> <host> <user>` | Register a server (`-k/--key`, `-p/--port`, `-t/--tag`, repeatable) |
| `remote-cmd host list [-t TAG]` | List hosts, optionally filtered by tag |
| `remote-cmd host show <name>` | Show one host's details |
| `remote-cmd host test <name>` | Test connectivity to a host |
| `remote-cmd host remove <name>` | Remove a host |
| `remote-cmd run <name> "<cmd>" [-T SECONDS]` | Run a command on one host (`--timeout/-T` sets the wall-clock limit) |
| `remote-cmd upload <name> <local> <remote>` | Upload a file via SFTP |
| `remote-cmd download <name> <remote> <local>` | Download a file via SFTP |
| `remote-cmd batch-run <name>... "<cmd>"` | Run across named hosts (`-C` concurrency, `-T` timeout, `-r` retries, `--async`, `--show-failures`) |

---

## Python API

Use Remote CMD inside your own scripts and automation:

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa",
)

with SSHClient(config) as client:
    # Execute commands
    result = client.execute("uptime")
    print(result.stdout)

    # Transfer files
    client.upload_file("./local.txt", "/remote/path/file.txt")
    client.download_file("/remote/path/file.txt", "./local.txt")

    # List remote directory
    for entry in client.list_remote_directory("/var/log"):
        print(f"{entry.name}: {entry.size} bytes")
```

---

## Features

| Category | Details |
|---|---|
| **SSH Auth** | Password + key file + ssh-agent, with pluggable credential providers |
| **Credential Chain** | Source passwords from environment, keyring, or arbitrary providers, in priority order |
| **Credential Encryption** | Fernet-encrypt secrets at rest (`CredentialEncryption`) |
| **Commands** | Single, multi-line, sudo with password |
| **File Transfer** | Upload/download via SFTP (`remote-cmd upload/download`) |
| **Host Management** | CRUD with pluggable JSON or **SQLite** persistence |
| **Tag System** | Filter hosts by tag (e.g., `production`, `web`, `db`) |
| **Batch Ops** | Run commands across any host group, synchronously or asynchronously |
| **Async Kernel** | `AsyncSSHClient` / `AsyncConnectionPool` / `AsyncBatchExecutor` via the `[async]` extra |
| **Task Runner** | Track and schedule long-running remote tasks with statuses (`TaskRunner`) |
| **Connection Test** | Test all host SSH connections and report status |
| **Secure Logging** | Structured logging that filters sensitive data (`SensitiveDataFilter`) |
| **Type Safety** | Full type annotations + mypy strict |

---

## Installation

```bash
# From PyPI (recommended) — keeps API and CLI in sync
pip install remote_cmd_manager

# With native async support (AsyncSSHClient / AsyncConnectionPool / AsyncBatchExecutor)
pip install "remote_cmd_manager[async]"

# From source
git clone git@github.com:Vae-Scrooge/remote-cmd.git
cd remote-cmd
pip install -e ".[dev]"
```

The `[async]` extra installs `asyncssh` and enables the native async execution
kernel: `AsyncSSHClient`, `AsyncConnectionPool` and `AsyncBatchExecutor`
(also available via `BatchExecutor(use_async=True)`). `BatchExecutor(use_async=True)`
also requires this extra. Without it, `import remote_cmd` still works — the async symbols
are simply not exported.

---

## Documentation

📚 **[Full Documentation Center](./docs/README.md)** — tutorials, API reference, architecture, and troubleshooting

| Document | Contents |
|---|---|
| [API Reference](./docs/API.md) | Full API docs: SSHClient, AsyncSSHClient, HostService, and more |
| [API Docs (auto-generated)](./docs/api/remote_cmd.html) | Complete API reference generated by pdoc |
| [Quickstart Tutorial](./docs/tutorial-quickstart.md) | Step-by-step walkthrough |
| [Advanced Tutorial](./docs/tutorial-advanced.md) | Batch ops, error handling, production patterns |
| [Architecture](./docs/architecture.md) | System architecture and design decisions |
| [Development Guide](./docs/DEVELOPMENT.md) | Set up the dev environment, contributing |
| [Troubleshooting](./docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [Changelog](./CHANGELOG.md) | Release history |
| [Mobile Remote Guide](./MOBILE-REMOTE-GUIDE.md) | Manage servers from your phone |

> **Note:** The documentation center and tutorials are maintained in **English**. See
> [README.zh-CN.md](./README.zh-CN.md) for the Chinese version of this page.

---

## Project Status

**Stable.** The core API is stable and versioned under semantic versioning. Breaking changes are communicated via major-version bumps, and the public API surface has been stable since the 1.x line.

**Roadmap:**
- [x] Async SSH operations (parallel execution) — v1.1.0
- [x] Pluggable storage backends (JSON + SQLite) — v1.2.x
- [x] Chainable credential providers + at-rest encryption — v1.2.x
- [ ] Configuration profiles (AWS, GCP, custom)
- [ ] Output formatting (JSON, table)
- [ ] Templated command recipes

Good first issues are labelled `good first issue` in the
[issue tracker](https://github.com/Vae-Scrooge/remote-cmd/issues) — contributions welcome.

---

## Maintainership

**Remote CMD is an actively maintained open-source project.** It is designed and
developed independently as a focused alternative to heavyweight tools for the
ad-hoc SSH tasks that come up in day-to-day server work.

- **Project health:** CI runs on every PR, Python 3.9+ is supported, and the
  public API is versioned under [semantic versioning](https://semver.org/).
- **Your code, your servers:** usage stays open under the MIT license — nothing
  is telemetry-driven or locked behind a service.
- **Why open source?** The tooling around ad-hoc SSH administration was either
  too heavy (Ansible) or too bare (raw shell loops). Remote CMD exists so that
  a single command can cover the common 90% of remote admin.

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](./CONTRIBUTING.md) to get started.

Before contributing, please read our [Code of Conduct](./CODE_OF_CONDUCT.md).

---

## License

MIT © [Vae-Scrooge](https://github.com/Vae-Scrooge/remote-cmd)

---

<p align="center">
  <a href="https://github.com/Vae-Scrooge/remote-cmd">
    <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=social" alt="Star">
  </a>
  <br>
  <sub>If you find this project useful, <strong>star it on GitHub</strong> ⭐</sub>
</p>
