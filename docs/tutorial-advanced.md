# Advanced Tutorial

This tutorial covers Remote CMD's advanced features and best practices to help you manage remote servers more efficiently.

## Table of Contents

- [Advanced Connection Configuration](#advanced-connection-configuration)
- [Error Handling and Retries](#error-handling-and-retries)
- [Parallel Batch Operations](#parallel-batch-operations)
- [Logging and Monitoring](#logging-and-monitoring)
- [Security Best Practices](#security-best-practices)
- [Performance Optimization](#performance-optimization)
- [Custom Extensions](#custom-extensions)

---

## Advanced Connection Configuration

### Connection Options

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

# Advanced connection configuration
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="secret",
    port=22,
    timeout=60,           # Connection timeout
    compress=True,        # Enable compression (good for slow networks)
)

with SSHClient(config) as client:
    result = client.execute("ls -la")
```

### Environment Variable Injection

Inject environment variables when executing a command:

```python
with SSHClient(config) as client:
    # Inject environment variables
    result = client.execute(
        "echo $APP_ENV && echo $DB_HOST",
        environment={
            "APP_ENV": "production",
            "DB_HOST": "192.168.1.200"
        }
    )
    print(result.stdout)
    # Output:
    # production
    # 192.168.1.200
```

### Command Timeout Control

Prevent long-running commands from blocking:

```python
from remote_cmd.utils.exceptions import SSHCommandTimeoutError

with SSHClient(config) as client:
    # 5-second timeout
    try:
        result = client.execute("sleep 10", timeout=5)
    except SSHCommandTimeoutError:
        print("Command timed out")

    # Use nohup for long-running tasks
    result = client.execute(
        "nohup long_running_task > /tmp/output.log 2>&1 &"
    )
```

---

## Error Handling and Retries

### Exception Types

Remote CMD provides a detailed exception hierarchy:

```python
from remote_cmd.utils.exceptions import (
    SSHConnectionError,      # Connection error
    SSHAuthenticationError,  # Auth failure (permanent, not retried)
    SSHTimeoutError,         # Connection timeout (transient, retried)
    SSHCommandError,         # Command execution error
    SSHCommandTimeoutError,  # Command timeout (transient, retried)
    SSHFileTransferError,    # File transfer error
    CredentialError,         # Credential parse/decrypt error (permanent, not retried)
    ConfigError,            # Configuration error
    ValidationError         # Input validation error
)

# Batch executors use the same classification: unrecognized Exception
# subclasses remain retryable for compatibility with custom client_factory;
# custom implementations should prefer typed remote_cmd exceptions.

def safe_execute(client, command: str, max_retries: int = 3):
    """Safely execute a command with retry classification consistent with Remote CMD"""
    import time

    from remote_cmd.service.retry_policy import compute_backoff_delay, is_retryable

    for attempt in range(max_retries + 1):
        try:
            result = client.execute(command)
            return result

        except Exception as e:
            if attempt >= max_retries or not is_retryable(e):
                raise
            delay = compute_backoff_delay(attempt, base_delay=1.0)
            print(f"Execution failed, retrying in {delay:.2f}s ({attempt + 1}/{max_retries})...")
            time.sleep(delay)
```

### Robust Error Handling

```python
import logging
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)

def robust_batch_execute(hosts, command):
    """Robustly execute a command across hosts"""
    results = []
    failed_hosts = []

    for host in hosts:
        try:
            logger.info(f"Processing host: {host.name}")

            with manager.connect_to_host(host.name) as client:
                result = client.execute(command)

                results.append({
                    "host": host.name,
                    "success": result.success,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code
                })

        except SSHConnectionError as e:
            logger.error(f"Connection to {host.name} failed: {e}")
            failed_hosts.append({"host": host.name, "error": str(e)})

        except Exception as e:
            logger.error(f"Error processing {host.name}: {e}")
            failed_hosts.append({"host": host.name, "error": str(e)})

    # Generate a report
    success_count = len([r for r in results if r["success"]])
    print(f"\nExecution complete: {success_count}/{len(hosts)} succeeded")

    if failed_hosts:
        print(f"Failed hosts: {len(failed_hosts)}")
        for f in failed_hosts:
            print(f"  - {f['host']}: {f['error']}")

    return results, failed_hosts
```

### Smart Retry Mechanism

```python
from functools import wraps
import time

from remote_cmd.service.retry_policy import compute_backoff_delay, is_retryable

def retry_on_failure(max_retries=3, delay=1):
    """Retry decorator using Remote CMD's classification policy"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt >= max_retries - 1 or not is_retryable(e):
                        break
                    current_delay = compute_backoff_delay(
                        attempt,
                        base_delay=delay,
                        max_delay=60.0,
                    )
                    print(f"Attempt {attempt + 1} failed: {e}")
                    print(f"Retrying in {current_delay:.2f}s...")
                    time.sleep(current_delay)

            raise last_exception
        return wrapper
    return decorator

@retry_on_failure(max_retries=3, delay=1)
def deploy_to_host(host_name):
    """Deploy to the specified host with retries"""
    with manager.connect_to_host(host_name) as client:
        client.upload_file("./app.tar.gz", "/tmp/app.tar.gz")
        result = client.execute("cd /var/www && tar -xzf /tmp/app.tar.gz")
        if not result.success:
            raise SSHCommandError(f"Deployment failed: {result.stderr}")
```

---

## Parallel Batch Operations

### Using ThreadPoolExecutor

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

def execute_on_host(host, command):
    """Execute a command on a single host"""
    try:
        with manager.connect_to_host(host.name) as client:
            result = client.execute(command)
            return {
                "host": host.name,
                "success": result.success,
                "output": result.stdout if result.success else result.stderr
            }
    except Exception as e:
        return {
            "host": host.name,
            "success": False,
            "error": str(e)
        }

def parallel_execute(hosts, command, max_workers=5):
    """Execute a command in parallel across multiple hosts"""
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_host = {
            executor.submit(execute_on_host, host, command): host
            for host in hosts
        }

        # Process completed tasks
        for future in as_completed(future_to_host):
            host = future_to_host[future]
            try:
                result = future.result()
                results.append(result)
                status = "✅" if result["success"] else "❌"
                print(f"{status} {host.name}")
            except Exception as e:
                print(f"❌ {host.name}: {e}")
                results.append({
                    "host": host.name,
                    "success": False,
                    "error": str(e)
                })

    return results

# Usage example
repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
web_hosts = manager.list_hosts(tag="web")

results = parallel_execute(
    hosts=web_hosts,
    command="systemctl status nginx",
    max_workers=5
)

# Generate a summary report
success_count = sum(1 for r in results if r["success"])
print(f"\nSummary: {success_count}/{len(results)} succeeded")
```

### Async Execution Pattern

```python
import asyncio
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor

async def batch_async_execute(host_names, command, max_concurrency=5):
    """Batch execution using the native asyncssh kernel"""
    executor = AsyncBatchExecutor(
        manager,
        max_concurrency=max_concurrency,
        command_timeout=30,
    )
    return await executor.execute(
        host_names,
        command,
        retry_count=1,
        retry_delay=1.0,
    )

# Usage example
results = asyncio.run(
    batch_async_execute([host.name for host in web_hosts], "uptime")
)
```

In multi-host or retry scenarios, `AsyncBatchExecutor` uses `AsyncConnectionPool` per host internally to reuse connections, and closes the internally created pool automatically after the batch. If you inject an external pool via `pool_factory`, the pool is caller-owned and is never closed by the executor; this suits long-lived services reusing pools across batches. Do not call `BatchExecutor(use_async=True)` inside an already-running event loop; use `await AsyncBatchExecutor.execute()` directly instead.

### Batch File Transfer

```python
def parallel_upload(hosts, local_path, remote_path, max_workers=3):
    """Upload a file to multiple hosts in parallel"""
    def upload_to_host(host):
        try:
            with manager.connect_to_host(host.name) as client:
                client.upload_file(local_path, remote_path)
                return {"host": host.name, "success": True}
        except Exception as e:
            return {"host": host.name, "success": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(upload_to_host, host) for host in hosts]
        results = [f.result() for f in futures]

    return results

# Distribute a config file
config_file = "./nginx.conf"
web_hosts = manager.list_hosts(tag="web")

results = parallel_upload(
    hosts=web_hosts,
    local_path=config_file,
    remote_path="/tmp/nginx.conf",
    max_workers=3
)
```

---

## Logging and Monitoring

### Enable Verbose Logging

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('remote_cmd.log')
    ]
)

# View Remote CMD logs
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### Execution Monitoring

```python
from dataclasses import dataclass
from datetime import datetime
from typing import List

@dataclass
class ExecutionLog:
    timestamp: datetime
    host: str
    command: str
    duration: float
    success: bool
    exit_code: int

class ExecutionMonitor:
    def __init__(self):
        self.logs: List[ExecutionLog] = []

    def record(self, host: str, command: str, duration: float, result):
        log = ExecutionLog(
            timestamp=datetime.now(),
            host=host,
            command=command,
            duration=duration,
            success=result.success,
            exit_code=result.exit_code
        )
        self.logs.append(log)

    def generate_report(self):
        """Generate an execution report"""
        if not self.logs:
            return "No execution records"

        total = len(self.logs)
        success = sum(1 for log in self.logs if log.success)
        avg_duration = sum(log.duration for log in self.logs) / total

        report = f"""
Execution Report
========
Total executions: {total}
Success: {success}
Failure: {total - success}
Success rate: {success/total*100:.1f}%
Average duration: {avg_duration:.2f}s

Details:
"""
        for log in self.logs:
            status = "✓" if log.success else "✗"
            report += f"\n  {status} [{log.host}] {log.command[:50]}"
            report += f" ({log.duration:.2f}s)"

        return report

# Usage example
monitor = ExecutionMonitor()

import time
for host in manager.list_hosts(tag="web"):
    with manager.connect_to_host(host.name) as client:
        start = time.time()
        result = client.execute("uptime")
        duration = time.time() - start

        monitor.record(host.name, "uptime", duration, result)

print(monitor.generate_report())
```

---

## Security Best Practices

### 1. Use SSH Keys Instead of Passwords

```python
# ✅ Recommended: use an SSH key
config = ConnectionConfig(
    hostname="example.com",
    username="deploy",
    key_filename="~/.ssh/id_rsa"
)

# ❌ Not recommended: hard-coded password
config = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="hardcoded_password"  # Security risk
)
```

### 2. Key File Permissions

```python
import os
from pathlib import Path

def check_key_permissions(key_path: str):
    """Check SSH key file permissions"""
    key_file = Path(key_path).expanduser()

    if not key_file.exists():
        raise FileNotFoundError(f"Key file does not exist: {key_path}")

    # Get file permissions
    stat = key_file.stat()
    mode = oct(stat.st_mode)[-3:]

    # Check whether permissions are 600
    if mode != "600":
        print(f"⚠️  Warning: key file permissions are {mode}, recommend setting to 600")
        print(f"   Run: chmod 600 {key_path}")

    return True

# Usage
key_path = "~/.ssh/id_rsa"
check_key_permissions(key_path)
```

### 3. Sensitive Data Handling

```python
import os
from getpass import getpass

def get_secure_password():
    """Securely retrieve a password"""
    # Prefer reading from an environment variable
    password = os.environ.get('SSH_PASSWORD')

    if not password:
        # Interactive input (hidden)
        password = getpass("Enter SSH password: ")

    return password

def mask_sensitive_data(data: dict) -> dict:
    """Mask sensitive data"""
    sensitive_keys = ['password', 'key_filename', 'secret']
    masked = data.copy()

    for key in sensitive_keys:
        if key in masked:
            masked[key] = '***'

    return masked
```

### 4. Using a Bastion Host

```python
# Connect to an internal server via a bastion host
bastion_config = ConnectionConfig(
    hostname="bastion.example.com",
    username="jumpuser",
    key_filename="~/.ssh/bastion_key"
)

target_config = ConnectionConfig(
    hostname="internal-server.local",
    username="admin",
    key_filename="~/.ssh/internal_key"
)

# First connect to the bastion host
with SSHClient(bastion_config) as bastion:
    # Set up port forwarding, then connect to the target via the bastion
    pass
```

---

## Performance Optimization

### 1. Connection Reuse

```python
from contextlib import contextmanager

@contextmanager
def managed_connection(manager, host_name):
    """Manage a connection, supporting reuse"""
    client = None
    try:
        client = manager.connect_to_host(host_name)
        yield client
    finally:
        if client:
            client.disconnect()

# Execute multiple commands on a single connection
with managed_connection(manager, "web-01") as client:
    client.execute("cd /var/www")
    client.execute("git pull")
    client.execute("npm install")
    client.execute("npm run build")
```

### 2. Compressed Transfer

```python
# Enable compression (good for slow networks)
config = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="pass",
    compress=True  # Enable compression
)

# Compress before transferring large files
commands = [
    "tar -czf /tmp/logs.tar.gz /var/log/app/",
    # Download the archive
    # Extract locally
]
```

### 3. Batch Optimization

```python
# ❌ Inefficient: connect one by one
for host in hosts:
    with manager.connect_to_host(host.name) as client:
        client.execute("command")

# ✅ Efficient: parallel connections
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [
        executor.submit(execute_command, host, "command")
        for host in hosts
    ]
    results = [f.result() for f in futures]
```

---

## Custom Extensions

### Custom Command Processor

```python
class CommandProcessor:
    """Custom command processor"""

    def before_execute(self, command: str) -> str:
        """Pre-execution processing"""
        # Add a timestamp
        return f"echo '[{datetime.now()}] Executing: {command}' && {command}"

    def after_execute(self, result):
        """Post-execution processing"""
        if result.success:
            print(f"✓ Command succeeded: {result.command}")
        else:
            print(f"✗ Command failed: {result.exit_code}")

# Usage
processor = CommandProcessor()
enhanced_command = processor.before_execute("ls -la")
result = client.execute(enhanced_command)
processor.after_execute(result)
```

### Plugin System Example

```python
class PluginManager:
    """Simple plugin manager"""

    def __init__(self):
        self.hooks = {
            'pre_connect': [],
            'post_connect': [],
            'pre_execute': [],
            'post_execute': []
        }

    def register(self, hook_name, callback):
        """Register a hook"""
        if hook_name in self.hooks:
            self.hooks[hook_name].append(callback)

    def execute(self, hook_name, *args, **kwargs):
        """Execute hooks"""
        for callback in self.hooks.get(hook_name, []):
            callback(*args, **kwargs)

# Create the plugin manager
plugins = PluginManager()

# Register a logging plugin
def log_connection(host):
    print(f"[LOG] Connected to: {host}")

plugins.register('post_connect', log_connection)

# Usage
plugins.execute('post_connect', 'web-01')
```

---

## Summary

This tutorial covered Remote CMD's advanced features:

- **Advanced configuration**: environment variables, timeout control
- **Error handling**: exception types, retry mechanisms
- **Batch operations**: parallel execution, async patterns
- **Logging and monitoring**: verbose logs, execution monitoring
- **Security practices**: SSH keys, sensitive data handling
- **Performance optimization**: connection reuse, compressed transfer
- **Custom extensions**: plugin system

With these techniques you can manage remote servers more efficiently and securely.

---

## Next Steps

- [View the API Documentation](./API.md)
- [Read Troubleshooting](./TROUBLESHOOTING.md)
- [Browse the Examples](../examples/)
- [Contribute](../CONTRIBUTING.md)
