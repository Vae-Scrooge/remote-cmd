# Troubleshooting Guide

This document lists common problems you may encounter while using Remote CMD and their solutions.

## Table of Contents

- [Connection Problems](#connection-problems)
- [Authentication Problems](#authentication-problems)
- [Command Execution Problems](#command-execution-problems)
- [File Transfer Problems](#file-transfer-problems)
- [Performance Problems](#performance-problems)
- [Configuration Problems](#configuration-problems)
- [Environment Problems](#environment-problems)
- [Debugging Tips](#debugging-tips)
- [Getting Help](#getting-help)

---

## Connection Problems

### 1. Connection Timeout

**Error message:**

```
SSHTimeoutError: connection timeout: 192.168.1.100
```

**Possible causes:**

- Network unreachable
- Firewall blocking
- SSH service not running
- Wrong IP address or port

**Solutions:**

```bash
# 1. Check network connectivity
ping 192.168.1.100

# 2. Check the SSH port
nc -zv 192.168.1.100 22
# or
telnet 192.168.1.100 22

# 3. Add the host (enter the password interactively at the prompt when no key is provided)
remote-cmd host add my-server 192.168.1.100 admin --port 22
```

```python
# Set a longer timeout in the Python API
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="pass",
    timeout=60  # Increase to 60 seconds
)
```

---

### 2. Host Unreachable

**Error message:**

```
SSHConnectionError: could not resolve hostname: example.com
```

**Solutions:**

```bash
# 1. Check DNS resolution
nslookup example.com
dig example.com

# 2. Use an IP address instead of a domain (enter the password interactively when no key)
remote-cmd host add my-server 192.168.1.100 admin

# 3. Check the hosts file
# Linux/macOS: /etc/hosts
# Windows: C:\Windows\System32\drivers\etc\hosts
```

---

### 3. Connection Refused

**Error message:**

```
SSHConnectionError: [Errno 111] Connection refused
```

**Possible causes:**

- SSH service not started
- Wrong port
- Firewall blocking

**Solutions:**

```bash
# Check the SSH service status on the remote server
# Linux (Debian/Ubuntu):
sudo systemctl status ssh

# Linux (CentOS/RHEL):
sudo systemctl status sshd

# Start the SSH service
sudo systemctl start ssh
sudo systemctl enable ssh

# Check the port
sudo netstat -tlnp | grep ssh
sudo ss -tlnp | grep ssh

# Check the firewall
sudo ufw status
sudo iptables -L | grep 22

# Temporarily disable the firewall for testing only
sudo ufw disable
```

---

## Authentication Problems

### 1. Authentication Failed

**Error message:**

```
SSHAuthenticationError: authentication failed: Authentication failed.
```

**Possible causes:**

- Wrong username
- Wrong password
- Wrong SSH key
- Insufficient permissions

**Solutions:**

#### Case A: Password authentication failed

```bash
# 1. Confirm username and password
ssh admin@192.168.1.100  # Manual test

# 2. Check whether the user exists
id username

# 3. Check whether the user is locked
sudo passwd -S username
```

#### Case B: SSH key authentication failed

```bash
# 1. Check private key file permissions (must be 600)
ls -la ~/.ssh/id_rsa
chmod 600 ~/.ssh/id_rsa

# 2. Check whether the public key is in authorized_keys
cat ~/.ssh/id_rsa.pub
ssh user@server "cat ~/.ssh/authorized_keys"

# 3. Check authorized_keys permissions
ssh user@server "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

# 4. Use an absolute path
remote-cmd host add my-server 192.168.1.100 admin \
    --key /home/username/.ssh/id_rsa  # Use absolute path, not ~/
```

#### Case C: SELinux issues (CentOS/RHEL)

```bash
# Check SELinux status
getenforce

# Temporarily set to permissive mode (for testing)
sudo setenforce 0

# Or fix the SELinux context
restorecon -Rv ~/.ssh
```

---

### 2. Host Key Verification Failed

**Error message:**

```
paramiko.SSHException: Server '192.168.1.100' not found in known_hosts
```

**Solutions:**

```bash
# Method 1: add the host key manually
ssh-keyscan -H 192.168.1.100 >> ~/.ssh/known_hosts

# Method 2: accept on first connection
ssh user@192.168.1.100  # Type yes

# Method 3: disable strict checking in code (not recommended for production)
```

```python
# Python API (already the default implementation)
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
```

---

### 3. Permission Denied

**Error message:**

```
paramiko.SSHException: Permission denied
```

**Possible causes:**

- The user is disabled from SSH login
- Only key auth is allowed but a password was used
- Root login is forbidden

**Solutions:**

```bash
# Check the SSH config
cat /etc/ssh/sshd_config | grep -E "^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication)"

# Ensure the following are correct:
# PasswordAuthentication yes
# PubkeyAuthentication yes
# PermitRootLogin yes  # or prohibit-password

# Restart the SSH service
sudo systemctl restart sshd
```

---

## Command Execution Problems

### 1. Command Not Found

**Error message:**

```
stdout:
stderr: bash: command: command not found
exit_code: 127
```

**Solutions:**

```python
# 1. Use the full path
result = client.execute("/usr/bin/python3 --version")

# 2. Source the environment first
result = client.execute("source ~/.bashrc && python3 --version")

# 3. Use which to find the path
result = client.execute("which python3")
python_path = result.stdout.strip()
result = client.execute(f"{python_path} --version")
```

---

### 2. Insufficient Permissions

**Error message:**

```
stderr: Permission denied
exit_code: 126
```

**Solutions:**

```python
# Use sudo
result = client.execute_sudo("systemctl restart nginx", password="sudopass")

# Or check permissions first
result = client.execute("whoami")
print(f"Current user: {result.stdout.strip()}")

# Check file permissions
result = client.execute("ls -la /path/to/file")
```

---

### 3. Command Timeout

**Error message:**

```
SSHCommandTimeoutError: command timed out after 300 seconds
```

**Solutions:**

```python
# Increase the timeout
result = client.execute("long_running_command", timeout=300)  # 5 minutes

# Or run in the background with nohup
result = client.execute("nohup long_command > /tmp/output.log 2>&1 &")
```

---

## File Transfer Problems

### 1. File Not Found

**Error message:**

```
SSHFileTransferError: Local file not found: ./file.txt
```

**Solutions:**

```python
import os
from pathlib import Path

# Check whether the file exists
local_path = "./file.txt"
if not os.path.exists(local_path):
    print(f"Error: file {local_path} does not exist")
    print(f"Current directory: {os.getcwd()}")
    print(f"Directory contents: {os.listdir('.')}")
else:
    client.upload_file(local_path, "/remote/path/")

# Use an absolute path
local_path = Path("./file.txt").resolve()
client.upload_file(str(local_path), "/remote/path/")
```

---

### 2. Permission Denied

**Error message:**

```
SSHFileTransferError: [Errno 13] Permission denied: '/remote/path'
```

**Solutions:**

```python
# 1. Upload to a temp directory, then move it
client.upload_file("./file.txt", "/tmp/file.txt")
client.execute_sudo("mv /tmp/file.txt /restricted/path/", password="pass")

# 2. Check remote directory permissions
result = client.execute("ls -ld /remote/path")
print(result.stdout)

# 3. Use sudo
client.execute_sudo("chmod 777 /remote/path", password="pass")
```

---

### 3. No Space Left on Device

**Error message:**

```
SSHFileTransferError: [Errno 28] No space left on device
```

**Solutions:**

```bash
# Check disk space
df -h

# Free up space
sudo apt clean  # Debian/Ubuntu
sudo yum clean all  # CentOS/RHEL
docker system prune  # If using Docker

# Find large files
sudo du -sh /var/log/*
sudo find /tmp -type f -mtime +7 -delete
```

---

## Performance Problems

### 1. Slow Connection

**Symptoms:**

- Establishing a connection takes a long time
- High command execution latency

**Solutions:**

```python
# 1. Enable compression
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="pass",
    compress=True  # Enable compression
)

# 2. Use a connection pool (example)
from contextlib import contextmanager

@contextmanager
def get_client(config):
    client = SSHClient(config)
    try:
        yield client.connect()
    finally:
        client.disconnect()

# Reuse the connection
with get_client(config) as client:
    for cmd in commands:
        result = client.execute(cmd)
```

---

### 2. Slow File Transfer

**Solutions:**

```python
# 1. Compress before transferring
client.execute("tar -czf /tmp/archive.tar.gz /large/directory")
client.download_file("/tmp/archive.tar.gz", "./archive.tar.gz")

# 2. Use rsync (if available)
result = client.execute("which rsync")
if result.success:
    # Use rsync
    pass
```

---

## Configuration Problems

### 1. Config Parsing Error

**Error message:**

```
yaml.scanner.ScannerError: mapping values are not allowed here
```

**Solutions:**

```yaml
# Ensure correct YAML format
# config.yaml

# Correct
hosts_file: hosts.json

# Wrong (colon must be followed by a space)
hosts_file:hosts.json

# Correct (list format)
tags:
  - web
  - production

# Wrong
tags: [web, production]  # This is also correct, but be consistent
```

---

### 2. Config File Not Found

**Error message:**

```
FileNotFoundError: [Errno 2] No such file or directory: 'config.yaml'
```

**Solutions:**

```bash
# 1. Check the file location
ls -la config.yaml

# 2. Use an absolute path
remote-cmd --config /full/path/to/config.yaml host list

# 3. Create the default config
cp config.example.yaml config.yaml
```

---

## Environment Problems

### 1. Incompatible Python Version

**Error message:**

```
SyntaxError: invalid syntax
```

**Solutions:**

```bash
# Check the Python version
python --version  # Requires 3.9+

# Use a specific version
python3.9 -m remote_cmd --version
python3.10 -m remote_cmd --version

# Create a virtual environment with the specified version
python3.9 -m venv venv
```

---

### 2. Missing Dependencies

**Error message:**

```
ModuleNotFoundError: No module named 'paramiko'
```

**Solutions:**

```bash
# Reinstall dependencies
pip install -r requirements.txt

# Or install individually
pip install paramiko click pyyaml

# Check installation
pip list | grep paramiko
```

---

### 3. Windows-Specific Issues

#### Issue: No color in command-line output

**Solution:**

```bash
# Install colorama
pip install colorama

# Or enable ANSI in PowerShell
$env:PYTHONIOENCODING="utf-8"
```

#### Issue: Path separators

```python
# Use the Path library for cross-platform paths
from pathlib import Path, PurePosixPath

# Windows path
local_path = Path("C:/Users/name/file.txt")

# Linux remote path
remote_path = PurePosixPath("/home/user/file.txt")
```

---

### 4. macOS-Specific Issues

#### Issue: Outdated system OpenSSH

macOS ships with an older OpenSSH. For best compatibility, install a newer version:

```bash
brew install openssh
```

Then ensure `/opt/homebrew/bin/ssh` (Apple Silicon) or `/usr/local/bin/ssh` (Intel) is first in your `PATH`.

#### Keychain / ssh-agent integration

`remote-cmd` works with the macOS `ssh-agent` automatically. If you store SSH keys in Keychain, ensure `UseKeychain yes` is set in your `~/.ssh/config`.

#### Gatekeeper / quarantine

Installing via `pip install remote_cmd_manager` (not a standalone binary) avoids Gatekeeper issues. If you ever distribute a standalone binary, it will require Apple notarization.

#### File permissions

Same as Linux: keep `~/.ssh` at `700` and private keys at `600`.

---

## Debugging Tips

### Enable Verbose Logging

```python
import logging

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Or use the remote_cmd logger
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### Inspect Detailed Information

```python
# Check connection info
print(f"Connection status: {client.is_connected()}")
print(f"Config: {client.config}")

# Check command results
result = client.execute("echo test")
print(f"Command: {result.command}")
print(f"Exit code: {result.exit_code}")
print(f"stdout: {repr(result.stdout)}")
print(f"stderr: {repr(result.stderr)}")
```

### Use Interactive Debugging

```python
import pdb

# Set a breakpoint in code
def my_function():
    client = SSHClient(config)
    pdb.set_trace()  # Breakpoint
    client.connect()
```

### Network Diagnostics

```bash
# Check network connectivity
ping <hostname>

# Check the port
nc -zv <hostname> 22
telnet <hostname> 22

# Check DNS
nslookup <hostname>
dig <hostname>

# Check routing
traceroute <hostname>  # Linux/macOS
tracert <hostname>     # Windows
```

---

## Getting Help

If none of the above solutions resolve your problem:

### 1. Collect Information

```bash
# Collect system information
python --version
pip list

# Collect error information
remote-cmd --verbose host test my-server 2>&1 | tee error.log
```

### 2. Open an Issue

Visit [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues) and include:

- Problem description
- Reproduction steps
- Error message (full stack trace)
- Environment information (OS, Python version)
- Config file (after removing sensitive data)

### 3. Other Resources

- [GitHub Discussions](https://github.com/Vae-Scrooge/remote-cmd/discussions) - Community discussion
- [API Documentation](./API.md) - API reference
- [Development Guide](./DEVELOPMENT.md) - Development-related

---

## Quick Checklist

Most problems can be solved by checking the following:

1. ✅ Network connection works
2. ✅ SSH service is running
3. ✅ Username and password/key are correct
4. ✅ File paths are correct
5. ✅ Sufficient permissions
6. ✅ Python version is compatible (3.9+)
7. ✅ Dependencies are installed

---

**Tip:** Before seeking help, please:

1. Check the relevant section of this document
2. Search existing Issues
3. Try to produce a minimal reproduction

---

*Last updated: 2026-08-23 (v2.1.0)*
