# Social & Promotional Content

---

## Dev.to Article: SSH Server Management Without the Ansible Overhead

*Published: draft*

---

You SSH into 10 servers to check disk space. You write a quick for loop. A typo crashes one session. You're not sure which ones you've already checked. By the time you're done, 20 minutes are gone and you have a terminal full of scrollback you need to piece together.

There's a better way.

### The Problem

Most developers SSH into servers one at a time. When you need to run the same command across multiple machines, your options are:

1. **Open 10 terminals** — error-prone, slow, no audit trail
2. **Write a shell loop** — `for host in web-01 web-02 ...; do ssh $host "df -h"; done` — works until a host is down and your loop breaks
3. **Ansible** — powerful, but you need to learn YAML DSL, write playbooks, manage inventory files. Overkill for "check disk space"
4. **Fabric** — good Python library, but no built-in host management or CLI

None of these give you a simple CLI that just works out of the box.

### Meet remote-cmd

`remote-cmd` is a Python CLI + API for SSH server management. It gives you:

- Host CRUD with tag-based grouping (`production`, `staging`, `web`, `db`)
- One-shot command execution on single or multiple hosts
- File upload/download via SFTP
- A Python API for scripting automation
- Zero setup — `pip install` and go

### Quick Demo

```bash
# Install
pip install remote_cmd_manager

# Add your first server
remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa

# Run a command
remote-cmd run web-01 "uptime"
# → 14:32:10 up 45 days,  2:15,  1 user,  load average: 0.08, 0.03, 0.05

# Add more servers and run across all production hosts
remote-cmd host add web-02 192.168.1.11 ubuntu --key ~/.ssh/id_rsa --tag production
remote-cmd host add db-01 192.168.1.20 ubuntu --key ~/.ssh/id_rsa --tag production --tag database

remote-cmd batch-run web-01 web-02 db-01 "df -h /"
# ✓ web-01  → Disk: 32G/100G (32%)
# ✓ web-02  → Disk: 45G/100G (45%)
# ✓ db-01   → Disk: 12G/50G  (24%)
```

### Real-World Use Cases

**Incident Response**
```bash
# Check errors across all web servers in 3 seconds
remote-cmd batch-run web-01 web-02 "journalctl -xe -n 50 | grep -i error"
```

**Deploy Code**
```python
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
for host in manager.list_hosts(tag="staging"):
    with manager.connect_to_host(host.name) as client:
        client.execute("cd /app && git pull")
        client.execute("pip install -r requirements.txt")
        client.execute_sudo("systemctl restart app", password="sudopass")
```

**Config Update**
```bash
# Upload new nginx config and reload across all web servers
remote-cmd upload web-01 ./nginx.conf /tmp/nginx.conf
remote-cmd run web-01 "sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t && sudo systemctl reload nginx"
```

### How It Stacks Up

| | remote-cmd | ssh + shell | Ansible | Fabric |
|---|---|---|---|---|
| Setup time | 10 seconds | None (you know ssh) | 30 min+ | 5 min |
| Host management | Built-in | Manual | Inventory YAML | Manual |
| CLI | Yes | Yes | ansible cmd | fab cmd |
| Python API | Yes | No | No (YAML) | Yes |
| Ideal for | Ad-hoc + scripts | One-off | Config mgmt | Python scripts |

### Getting Started

```bash
pip install remote_cmd_manager

# Full documentation
remote-cmd --help

# GitHub
# https://github.com/Vae-Scrooge/remote-cmd
```

The project is in beta, the core API is stable, and contributions are welcome.

If you SSH into more than a couple servers regularly, give it a try. It'll save you the "which servers did I check again?" moment.

---

## Show HN: remote-cmd — Lightweight SSH Manager CLI (Python)

**Title:** Show HN: remote-cmd – A Python CLI for SSH server management without the Ansible overhead

**URL:** https://github.com/Vae-Scrooge/remote-cmd

---

I built `remote-cmd` because I was tired of juggling 10 terminal tabs every time I needed to run a command across servers.

It's a Python CLI + API that wraps Paramiko with:
- Host CRUD with tag-based grouping (e.g., tag a host "production" + "web", then run commands across all hosts matching those tags)
- Batch execution across host groups
- SFTP file upload/download
- Python API for scripting

```bash
pip install remote_cmd_manager
remote-cmd host add web-01 192.168.1.10 ubuntu --key ~/.ssh/id_rsa
remote-cmd batch-run web-01 web-02 db-01 "df -h /"
```

I made a conscious choice to keep it simple:
- **Not** an Ansible replacement — if you need idempotent config management, use Ansible
- **Not** a Fabric replacement — if you want a library for Python SSH scripts, Fabric is great
- **Yes** a batteries-included CLI that works immediately for ad-hoc tasks

It stores hosts in a JSON file (~/.remote_cmd/hosts.json), supports password + key auth, and has a Python API so you can use it in your own automation scripts.

Would love feedback from other devs who deal with multi-server SSH tasks. What workflows do you have that this could simplify? What's missing?

GitHub: https://github.com/Vae-Scrooge/remote-cmd
PyPI: `pip install remote_cmd_manager`
