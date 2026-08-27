# Quick Start Tutorial

This tutorial will help you master the basics of Remote CMD within 15 minutes.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Your First Connection](#your-first-connection)
- [Managing Multiple Servers](#managing-multiple-servers)
- [File Transfer](#file-transfer)
- [Batch Operations](#batch-operations)
- [Next Steps](#next-steps)

---

## Prerequisites

### System Requirements

- **Python**: 3.9 or higher
- **OS**: Windows, macOS, Linux
- **Network**: Ability to reach the target SSH server

### Check Your Python Version

```bash
python --version
# or
python3 --version
```

If your version is below 3.8, upgrade Python first.

### Prepare a Test Server

You need at least one server reachable over SSH. It can be:

- A local VM (VirtualBox, VMware)
- A cloud server (AWS, Azure, Alibaba Cloud, etc.)
- A Docker container
- A physical machine on the LAN

Make sure you have:

- The server's IP address or hostname
- A username and password, or an SSH private key
- The SSH port (default 22)

---

## Installation

### Option 1: Install from PyPI (recommended)

```bash
pip install remote-cmd
```

### Option 2: Install from Source

```bash
# Clone the repository
git clone https://github.com/Vae-Scrooge/remote-cmd.git
cd remote-cmd

# Create a virtual environment (optional but recommended)
python -m venv venv

# Activate the virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install
pip install -e .
```

### Verify Installation

```bash
# Check the version
remote-cmd --version

# View help
remote-cmd --help
```

---

## Your First Connection

### Scenario

Connect to your first server and run a few basic commands.

### Using the CLI

```bash
# 1. Add a host configuration
# When no key is provided, enter the password interactively at the secure prompt
# (password is never passed as a command-line argument)
remote-cmd host add my-server 192.168.1.100 ubuntu

# View added hosts
remote-cmd host list

# 2. Test the connection
remote-cmd host test my-server

# 3. Run commands
remote-cmd run my-server "whoami"
remote-cmd run my-server "pwd"
remote-cmd run my-server "ls -la"
# Optional: set a wall-clock timeout for commands that may hang
remote-cmd run my-server "uptime" --timeout 30

# 4. View system info
remote-cmd run my-server "uptime"
remote-cmd run my-server "df -h"
remote-cmd run my-server "free -h"
```

### Using the Python API

Create a Python script `first_connection.py`:

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

# Configure the connection
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    password="yourpassword",
    port=22
)

# Connect and run commands
with SSHClient(config) as client:
    print("✅ Connected!")

    # Run a command
    result = client.execute("whoami")
    print(f"Current user: {result.stdout.strip()}")

    result = client.execute("pwd")
    print(f"Current directory: {result.stdout.strip()}")

    result = client.execute("uptime")
    print(f"System uptime: {result.stdout.strip()}")
```

Run the script:

```bash
python first_connection.py
```

### Using an SSH Key

If you have an SSH private key, you can connect more securely:

```bash
# CLI
remote-cmd host add my-server 192.168.1.100 ubuntu --key ~/.ssh/id_rsa

# Python
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa"
)
```

---

## Managing Multiple Servers

### Scenario

Manage a cluster that includes web servers and database servers.

### Add Multiple Servers

```bash
# Web server 1
remote-cmd host add web-01 192.168.1.10 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag web \
    --tag production \
    --description "Primary web server"

# Web server 2
remote-cmd host add web-02 192.168.1.11 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag web \
    --tag production \
    --description "Secondary web server"

# Database server
remote-cmd host add db-01 192.168.1.20 admin \
    --tag database \
    --tag production \
    --description "MySQL database server"
# Enter the password interactively at the prompt when no key is provided
```

### View and Filter

```bash
# List all hosts
remote-cmd host list

# Only web servers
remote-cmd host list --tag web

# Only production servers
remote-cmd host list --tag production
```

### Manage via the Python API

```python
from remote_cmd.core.host import Host
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

# Create the repository and host service
repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

# Add in bulk
servers = [
    Host(name="web-01", hostname="192.168.1.10", username="ubuntu",
         key_filename="~/.ssh/id_rsa", tags=["web", "production"]),
    Host(name="web-02", hostname="192.168.1.11", username="ubuntu",
         key_filename="~/.ssh/id_rsa", tags=["web", "production"]),
    Host(name="db-01", hostname="192.168.1.20", username="admin",
         password="dbpassword", tags=["database", "production"]),
]

for server in servers:
    manager.add_host(server)

# Persist the configuration
repo.flush()

# View all tags
print("Available tags:", manager.list_tags())

# Filter by tag
web_servers = manager.list_hosts(tag="web")
for host in web_servers:
    print(f"Web server: {host.name} ({host.hostname})")
```

---

## File Transfer

### Scenario

Upload application code to a server, or download log files locally for analysis.

### Upload a File

```bash
# CLI
remote-cmd upload my-server ./deploy.sh /tmp/deploy.sh

# Verify the upload
remote-cmd run my-server "ls -la /tmp/deploy.sh"
```

### Download a File

```bash
# CLI
remote-cmd download my-server /var/log/nginx/access.log ./logs/

# Download to the current directory
remote-cmd download my-server /etc/nginx/nginx.conf ./
```

### Python API File Operations

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa"
)

with SSHClient(config) as client:
    # Upload a file
    print("📤 Uploading deploy.sh...")
    client.upload_file("./deploy.sh", "/tmp/deploy.sh")

    # Verify the upload
    result = client.execute("ls -la /tmp/deploy.sh")
    print(f"Remote file: {result.stdout}")

    # Download a log
    print("📥 Downloading log file...")
    client.download_file("/var/log/syslog", "./syslog")

    # List a remote directory
    print("📂 Contents of remote /tmp:")
    entries = client.list_remote_directory("/tmp")
    for entry in entries[:5]:  # Show only the first 5
        icon = "📁" if entry.is_dir else "📄"
        print(f"  {icon} {entry.name}")
```

---

## Batch Operations

### Scenario

Run the same operation across multiple servers at once.

### Batch Execute Commands

```python
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

# Run on all web servers
for host in manager.list_hosts(tag="web"):
    print(f"\n🖥️  {host.name} ({host.hostname})")

    try:
        with manager.connect_to_host(host.name) as client:
            # Check Nginx status
            result = client.execute("systemctl status nginx")

            if result.success:
                print("  ✅ Nginx is running normally")
            else:
                print("  ⚠️  Nginx status abnormal")
                print(f"     {result.stderr[:100]}")

    except Exception as e:
        print(f"  ❌ Connection failed: {e}")

print("\n✨ Batch operation complete")
```

### Parallel Execution (Advanced)

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

def check_host(host):
    """Check the status of a single host"""
    try:
        with manager.connect_to_host(host.name) as client:
            result = client.execute("uptime")
            return host.name, True, result.stdout.strip()
    except Exception as e:
        return host.name, False, str(e)

hosts = manager.list_hosts()

print(f"🚀 Checking {len(hosts)} servers in parallel...\n")

with ThreadPoolExecutor(max_workers=5) as executor:
    # Submit all tasks
    future_to_host = {
        executor.submit(check_host, host): host
        for host in hosts
    }

    # Process results
    for future in as_completed(future_to_host):
        host_name, success, message = future.result()
        status = "✅" if success else "❌"
        print(f"{status} {host_name}: {message[:50]}")
```

---

## Hands-On: Automated Deployment Script

Create a complete deployment script:

```python
#!/usr/bin/env python3
"""
deploy.py - A simple automated deployment script

Usage:
    python deploy.py <host_name>
"""

import sys
import argparse
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

def deploy(host_name: str):
    """Deploy the application to the specified server"""
    repo = JsonHostRepository("my-hosts.json")
    manager = HostService(repository=repo)

    print(f"🚀 Starting deployment to {host_name}...")
    print("=" * 50)

    try:
        with manager.connect_to_host(host_name) as client:
            # 1. Upload code
            print("📤 Uploading application code...")
            client.upload_file("./app.tar.gz", "/tmp/app.tar.gz")

            # 2. Stop the service
            print("🛑 Stopping the application service...")
            result = client.execute("sudo systemctl stop myapp")

            # 3. Deploy code
            print("📦 Extracting and deploying...")
            commands = [
                "cd /var/www && tar -xzf /tmp/app.tar.gz",
                "cd /var/www/app && pip install -r requirements.txt",
            ]
            for cmd in commands:
                result = client.execute(cmd)
                if not result.success:
                    print(f"❌ Deployment failed: {result.stderr}")
                    sys.exit(1)

            # 4. Start the service
            print("▶️  Starting the application service...")
            result = client.execute("sudo systemctl start myapp")

            # 5. Health check
            print("🔍 Health check...")
            result = client.execute("curl -s http://localhost:8080/health")

            if "ok" in result.stdout.lower():
                print("\n✅ Deployment successful!")
            else:
                print("\n⚠️  Deployment complete but health check failed")

    except Exception as e:
        print(f"\n❌ Deployment failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deployment script")
    parser.add_argument("host", help="Target host name")
    args = parser.parse_args()

    deploy(args.host)
```

Usage:

```bash
# Deploy to a single server
python deploy.py web-01

# Deploy to all web servers
for host in web-01 web-02; do
    python deploy.py $host
done
```

---

## Frequently Asked Questions

### 1. Connection Timeout

**Problem:**

```
SSHTimeoutError: connection timeout: 192.168.1.100
```

**Solutions:**

- Check network connectivity: `ping <hostname>`
- Check the SSH port: `nc -zv <hostname> 22`
- Increase the timeout:

```python
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    password="pass",
    timeout=60  # Increase the timeout
)
```

### 2. Authentication Failed

**Problem:**

```
SSHAuthenticationError: authentication failed: Authentication failed.
```

**Solutions:**

- Check the username and password
- Check SSH key permissions: `chmod 600 ~/.ssh/id_rsa`
- Check the `authorized_keys` configuration

### 3. Insufficient Permissions

**Problem:**

```
Permission denied
```

**Solution:**

```python
# Use sudo
result = client.execute_sudo("systemctl restart nginx", password="sudopass")
```

---

## Next Steps

Congratulations on completing the quick start! Next you can:

1. **[Read the API Documentation](./API.md)** - Learn about all available APIs
2. **[Read the Advanced Tutorial](./tutorial-advanced.md)** - Learn more advanced features
3. **[Browse the Examples](../examples/)** - Reference more usage examples
4. **[Read Troubleshooting](./TROUBLESHOOTING.md)** - Solve common problems

---

## Getting Help

If you run into problems:

1. Check the [Troubleshooting Guide](./TROUBLESHOOTING.md)
2. Search [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues)
3. Open a new Issue

---

**Happy using!** 🎉
