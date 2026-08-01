<p align="center">
  <img src="https://img.shields.io/pypi/v/remote_cmd_manager?style=for-the-badge&logo=pypi&logoColor=white&label=PyPI" alt="PyPI">
  <img src="https://img.shields.io/pypi/dm/remote_cmd_manager?style=for-the-badge&logo=python&logoColor=white&label=Downloads" alt="Downloads">
  <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=for-the-badge&logo=github" alt="Stars">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge&logo=python" alt="Python">
  <img src="https://img.shields.io/github/license/Vae-Scrooge/remote-cmd?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/github/actions/workflow/status/Vae-Scrooge/remote-cmd/ci.yml?style=for-the-badge&logo=githubactions&label=CI" alt="CI">
  <img src="https://img.shields.io/badge/code%20style-black-black?style=for-the-badge" alt="Code Style">
</p>

<h1 align="center">Remote CMD — SSH Server Management<br><small>Without the Overhead</small></h1>

<p align="center">
  <b><code>pip install remote_cmd_manager</code></b> &nbsp;·&nbsp;
  <a href="#quick-start">Quick Start</a> &nbsp;·&nbsp;
  <a href="#use-cases">Use Cases</a> &nbsp;·&nbsp;
  <a href="#python-api">Python API</a> &nbsp;·&nbsp;
  <a href="./docs">Docs</a> &nbsp;·&nbsp;
  <a href="./CONTRIBUTING.md">Contributing</a>
</p>

<p align="center">
  <a href="https://asciinema.org/a/9yLeYj73muPUuAQY" target="_blank">
    <img src="https://asciinema.org/a/9yLeYj73muPUuAQY.svg" width="720" alt="Demo">
  </a>
</p>

---

**Remote CMD** is a lightweight Python CLI + API for managing servers over SSH. Add hosts, run commands, transfer files, and target groups by tags — no Ansible DSL or shell loops required.

```bash
# One command to get started
pip install remote_cmd_manager && remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa && remote-cmd run web-01 "uptime"
```

---

## Why Remote CMD?

| Feature | `remote-cmd` | `ssh` + shell | Ansible | Fabric |
|---|---|---|---|---|
| Host CRUD + tag groups | ✅ Built-in | ❌ Manual | ✅ Inventory | ❌ |
| Batch commands across hosts | ✅ `batch-run` | ❌ Write a loop | ✅ Playbook | ✅ |
| File transfer (upload/download) | ✅ Built-in | ✅ scp | ✅ copy module | ✅ |
| Python API | ✅ `from remote_cmd import ...` | ❌ | ❌ YAML-only | ✅ |
| Zero setup | ✅ `pip install → go` | ❌ Config SSH | ❌ `ansible.cfg` | ❌ |
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

# 4. Run across all production servers
remote-cmd batch-run -t production "df -h /"
```

---

## Use Cases

### 🖥️ System Admin — Check disk across 20 servers in one command
```bash
remote-cmd batch-run -t production "df -h / | tail -1"
# Output:
#   ✓ web-01  → /dev/sda1  32G  12G  19G  40% /
#   ✓ web-02  → /dev/sda1  32G  28G   3G  90% /   ⚠️
#   ✗ db-01   → Connection refused
```

### 🚀 Deploy — Pull code and restart service
```python
from remote_cmd.service.host_service import HostService
from remote_cmd.repository.json_host_repository import JsonHostRepository

service = HostService(repository=JsonHostRepository("hosts.json"))
for host in service.list_hosts(tag="staging"):
    with service.connect_to_host(host.name) as client:
        client.execute("cd /app && git pull")
        client.execute("pip install -r requirements.txt")
        client.execute_sudo("systemctl restart app", password="sudopass")
```

### 🔥 Incident Response — Check logs across all servers
```bash
remote-cmd batch-run -t web "journalctl -xe -n 50 | grep -i error"
```

### 🔧 Config Update — Upload and reload nginx across tagged hosts
```bash
# Upload new config
scp nginx.conf user@server:/tmp/nginx.conf  # or use the upload command
remote-cmd run web-01 "sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t && sudo systemctl reload nginx"
```

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
        print(f"{entry['name']}: {entry['size']} bytes")
```

---

## Features

| Category | Details |
|---|---|
| **SSH Auth** | Password + key file + ssh-agent |
| **Commands** | Single, multi-line, sudo with password |
| **File Transfer** | Upload/download via SFTP |
| **Host Management** | CRUD with JSON/YAML persistence |
| **Tag System** | Filter hosts by tag (e.g., `production`, `web`, `db`) |
| **Batch Ops** | Run commands across any host group |
| **Connection Test** | Ping all hosts and report status |
| **Type Safety** | Full type annotations + mypy strict |

---

## Installation

```bash
# From PyPI (recommended) — 同步 API 与 CLI
pip install remote_cmd_manager

# With async native support (AsyncSSHClient / AsyncConnectionPool / AsyncBatchExecutor)
pip install "remote_cmd_manager[async]"

# From source
git clone git@github.com:Vae-Scrooge/remote-cmd.git
cd remote-cmd
pip install -e ".[dev]"
```

The `[async]` extra installs `asyncssh` and enables the native async execution
kernel: `AsyncSSHClient`, `AsyncConnectionPool` and `AsyncBatchExecutor`
(also available via `BatchExecutor(use_async=True)`). Without it, `import remote_cmd`
still works — async symbols are simply not exported.

---

## Documentation

| Document | Contents |
|---|---|
| [API Reference](./docs/API.md) | Full API docs: SSHClient, AsyncSSHClient, HostService, and more |
| [API Docs (auto-generated)](./docs/api/remote_cmd.html) | Complete API reference generated by pdoc |
| [Quickstart Tutorial](./docs/tutorial-quickstart.md) | Step-by-step walkthrough |
| [Advanced Tutorial](./docs/tutorial-advanced.md) | Batch ops, error handling, production patterns |
| [Development Guide](./docs/DEVELOPMENT.md) | Setup dev environment, contributing |
| [Troubleshooting](./docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [Changelog](./CHANGELOG.md) | Release history |
| [Mobile Remote Guide](./MOBILE-REMOTE-GUIDE.md) | Manage servers from your phone |

---

## Project Status

**Beta.** The core API is stable. Breaking changes will be communicated via semantic versioning.

**Roadmap:**
- [x] Async SSH operations (parallel execution) — v1.1.0
- [ ] Configuration profiles (AWS, GCP, custom)
- [ ] Output formatting (JSON, table)

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](./CONTRIBUTING.md) to get started.

Before contributing, please read our [Code of Conduct](./CODE_OF_CONDUCT.md).

---

## License

MIT © [Vae-Scrooge](https://github.com/Vae-Scrooge)

---

<p align="center">
  <a href="https://github.com/Vae-Scrooge/remote-cmd">
    <img src="https://img.shields.io/github/stars/Vae-Scrooge/remote-cmd?style=social" alt="Star">
  </a>
  <br>
  <sub>If you find this project useful, <strong>star it on GitHub</strong> ⭐</sub>
</p>
