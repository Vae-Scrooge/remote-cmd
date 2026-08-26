# 高级使用教程

本教程介绍 Remote CMD 的高级功能和最佳实践，帮助你更高效地管理远程服务器。

## 目录

- [高级连接配置](#高级连接配置)
- [错误处理和重试](#错误处理和重试)
- [批量并行操作](#批量并行操作)
- [日志和监控](#日志和监控)
- [安全最佳实践](#安全最佳实践)
- [性能优化](#性能优化)
- [自定义扩展](#自定义扩展)

---

## 高级连接配置

### 连接配置选项

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

# 高级连接配置
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="secret",
    port=22,
    timeout=60,           # 连接超时时间
    compress=True,        # 启用压缩（适合慢速网络）
)

with SSHClient(config) as client:
    result = client.execute("ls -la")
```

### 环境变量注入

在命令执行时注入环境变量：

```python
with SSHClient(config) as client:
    # 注入环境变量
    result = client.execute(
        "echo $APP_ENV && echo $DB_HOST",
        environment={
            "APP_ENV": "production",
            "DB_HOST": "192.168.1.200"
        }
    )
    print(result.stdout)
    # 输出:
    # production
    # 192.168.1.200
```

### 命令超时控制

防止长时间运行的命令阻塞：

```python
from remote_cmd.utils.exceptions import SSHCommandTimeoutError

with SSHClient(config) as client:
    # 5 秒超时
    try:
        result = client.execute("sleep 10", timeout=5)
    except SSHCommandTimeoutError:
        print("命令执行超时")
    
    # 长时间任务使用 nohup
    result = client.execute(
        "nohup long_running_task > /tmp/output.log 2>&1 &"
    )
```

---

## 错误处理和重试

### 异常类型

Remote CMD 提供了详细的异常层次：

```python
from remote_cmd.utils.exceptions import (
    SSHConnectionError,      # 连接错误
    SSHAuthenticationError,  # 认证失败（永久性，不重试）
    SSHTimeoutError,         # 连接超时（瞬态，可重试）
    SSHCommandError,         # 命令执行错误
    SSHCommandTimeoutError,  # 命令超时（瞬态，可重试）
    SSHFileTransferError,    # 文件传输错误
    CredentialError,         # 凭据解析/解密错误（永久性，不重试）
    ConfigError,            # 配置错误
    ValidationError         # 输入验证错误
)

# 批量执行器使用同一分类：未识别的 Exception 子类仍保持可重试，以兼容
# 自定义 client_factory；自定义实现应优先抛出类型化的 remote_cmd 异常。

def safe_execute(client, command: str, max_retries: int = 3):
    """安全执行命令，带与 Remote CMD 一致的重试分类"""
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
            print(f"执行失败，{delay:.2f} 秒后重试 {attempt + 1}/{max_retries}...")
            time.sleep(delay)
```

### 健壮的错误处理

```python
import logging
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)

def robust_batch_execute(hosts, command):
    """健壮地批量执行命令"""
    results = []
    failed_hosts = []
    
    for host in hosts:
        try:
            logger.info(f"处理主机: {host.name}")
            
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
            logger.error(f"连接 {host.name} 失败: {e}")
            failed_hosts.append({"host": host.name, "error": str(e)})
            
        except Exception as e:
            logger.error(f"处理 {host.name} 时出错: {e}")
            failed_hosts.append({"host": host.name, "error": str(e)})
    
    # 生成报告
    success_count = len([r for r in results if r["success"]])
    print(f"\n执行完成: {success_count}/{len(hosts)} 成功")
    
    if failed_hosts:
        print(f"失败的主机: {len(failed_hosts)}")
        for f in failed_hosts:
            print(f"  - {f['host']}: {f['error']}")
    
    return results, failed_hosts
```

### 智能重试机制

```python
from functools import wraps
import time

from remote_cmd.service.retry_policy import compute_backoff_delay, is_retryable

def retry_on_failure(max_retries=3, delay=1):
    """使用 Remote CMD 分类策略的重试装饰器"""
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
                    print(f"尝试 {attempt + 1} 失败: {e}")
                    print(f"{current_delay:.2f} 秒后重试...")
                    time.sleep(current_delay)

            raise last_exception
        return wrapper
    return decorator

@retry_on_failure(max_retries=3, delay=1)
def deploy_to_host(host_name):
    """部署到指定主机，带重试"""
    with manager.connect_to_host(host_name) as client:
        client.upload_file("./app.tar.gz", "/tmp/app.tar.gz")
        result = client.execute("cd /var/www && tar -xzf /tmp/app.tar.gz")
        if not result.success:
            raise SSHCommandError(f"部署失败: {result.stderr}")
```

---

## 批量并行操作

### 使用 ThreadPoolExecutor

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

def execute_on_host(host, command):
    """在单个主机上执行命令"""
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
    """并行执行命令到多台主机"""
    results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_host = {
            executor.submit(execute_on_host, host, command): host
            for host in hosts
        }
        
        # 处理完成的任务
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

# 使用示例
repo = JsonHostRepository("hosts.json")
manager = HostService(repository=repo)
web_hosts = manager.list_hosts(tag="web")

results = parallel_execute(
    hosts=web_hosts,
    command="systemctl status nginx",
    max_workers=5
)

# 生成汇总报告
success_count = sum(1 for r in results if r["success"])
print(f"\n汇总: {success_count}/{len(results)} 成功")
```

### 异步执行模式

```python
import asyncio
from remote_cmd.service.async_batch_executor import AsyncBatchExecutor

async def batch_async_execute(host_names, command, max_concurrency=5):
    """使用原生 asyncssh 内核批量执行"""
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

# 使用示例
results = asyncio.run(
    batch_async_execute([host.name for host in web_hosts], "uptime")
)
```

多主机或需要重试时，`AsyncBatchExecutor` 内部按主机使用 `AsyncConnectionPool` 复用连接，
批次结束后自动关闭内部创建的池。若通过 `pool_factory` 注入外部池，池由调用方拥有，
执行器绝不会关闭；适合长驻服务跨批次复用。不要在已经运行的事件循环中调用
`BatchExecutor(use_async=True)`，此时应直接 `await AsyncBatchExecutor.execute()`。

### 批量文件传输

```python
def parallel_upload(hosts, local_path, remote_path, max_workers=3):
    """并行上传文件到多台主机"""
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

# 分发配置文件
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

## 日志和监控

### 启用详细日志

```python
import logging

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('remote_cmd.log')
    ]
)

# 查看 Remote CMD 日志
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### 执行监控

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
        """生成执行报告"""
        if not self.logs:
            return "没有执行记录"
        
        total = len(self.logs)
        success = sum(1 for log in self.logs if log.success)
        avg_duration = sum(log.duration for log in self.logs) / total
        
        report = f"""
执行报告
========
总执行次数: {total}
成功: {success}
失败: {total - success}
成功率: {success/total*100:.1f}%
平均耗时: {avg_duration:.2f}秒

详细记录:
"""
        for log in self.logs:
            status = "✓" if log.success else "✗"
            report += f"\n  {status} [{log.host}] {log.command[:50]}"
            report += f" ({log.duration:.2f}s)"
        
        return report

# 使用示例
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

## 安全最佳实践

### 1. 使用 SSH Key 而非密码

```python
# ✅ 推荐：使用 SSH Key
config = ConnectionConfig(
    hostname="example.com",
    username="deploy",
    key_filename="~/.ssh/id_rsa"
)

# ❌ 不推荐：硬编码密码
config = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="hardcoded_password"  # 安全隐患
)
```

### 2. 密钥文件权限

```python
import os
from pathlib import Path

def check_key_permissions(key_path: str):
    """检查 SSH 密钥文件权限"""
    key_file = Path(key_path).expanduser()
    
    if not key_file.exists():
        raise FileNotFoundError(f"密钥文件不存在: {key_path}")
    
    # 获取文件权限
    stat = key_file.stat()
    mode = oct(stat.st_mode)[-3:]
    
    # 检查权限是否为 600
    if mode != "600":
        print(f"⚠️  警告: 密钥文件权限为 {mode}，建议设置为 600")
        print(f"   运行: chmod 600 {key_path}")
    
    return True

# 使用
key_path = "~/.ssh/id_rsa"
check_key_permissions(key_path)
```

### 3. 敏感信息处理

```python
import os
from getpass import getpass

def get_secure_password():
    """安全获取密码"""
    # 优先从环境变量获取
    password = os.environ.get('SSH_PASSWORD')
    
    if not password:
        # 交互式输入（不显示）
        password = getpass("请输入 SSH 密码: ")
    
    return password

def mask_sensitive_data(data: dict) -> dict:
    """脱敏敏感数据"""
    sensitive_keys = ['password', 'key_filename', 'secret']
    masked = data.copy()
    
    for key in sensitive_keys:
        if key in masked:
            masked[key] = '***'
    
    return masked
```

### 4. 使用跳板机（Bastion Host）

```python
# 通过跳板机连接内网服务器
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

# 先连接跳板机
with SSHClient(bastion_config) as bastion:
    # 配置端口转发
    # 然后通过跳板机连接目标服务器
    pass
```

---

## 性能优化

### 1. 连接复用

```python
from contextlib import contextmanager

@contextmanager
def managed_connection(manager, host_name):
    """管理连接，支持复用"""
    client = None
    try:
        client = manager.connect_to_host(host_name)
        yield client
    finally:
        if client:
            client.disconnect()

# 在一个连接中执行多个命令
with managed_connection(manager, "web-01") as client:
    client.execute("cd /var/www")
    client.execute("git pull")
    client.execute("npm install")
    client.execute("npm run build")
```

### 2. 压缩传输

```python
# 启用压缩（适合慢速网络）
config = ConnectionConfig(
    hostname="example.com",
    username="admin",
    password="pass",
    compress=True  # 启用压缩
)

# 大文件传输前先压缩
commands = [
    "tar -czf /tmp/logs.tar.gz /var/log/app/",
    # 下载压缩包
    # 本地解压
]
```

### 3. 批量操作优化

```python
# ❌ 低效：逐个连接
for host in hosts:
    with manager.connect_to_host(host.name) as client:
        client.execute("command")

# ✅ 高效：并行连接
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [
        executor.submit(execute_command, host, "command")
        for host in hosts
    ]
    results = [f.result() for f in futures]
```

---

## 自定义扩展

### 自定义命令处理器

```python
class CommandProcessor:
    """自定义命令处理器"""
    
    def before_execute(self, command: str) -> str:
        """命令执行前处理"""
        # 添加时间戳
        return f"echo '[{datetime.now()}] Executing: {command}' && {command}"
    
    def after_execute(self, result):
        """命令执行后处理"""
        if result.success:
            print(f"✓ 命令成功: {result.command}")
        else:
            print(f"✗ 命令失败: {result.exit_code}")

# 使用
processor = CommandProcessor()
enhanced_command = processor.before_execute("ls -la")
result = client.execute(enhanced_command)
processor.after_execute(result)
```

### 插件系统示例

```python
class PluginManager:
    """简单插件管理器"""
    
    def __init__(self):
        self.hooks = {
            'pre_connect': [],
            'post_connect': [],
            'pre_execute': [],
            'post_execute': []
        }
    
    def register(self, hook_name, callback):
        """注册钩子"""
        if hook_name in self.hooks:
            self.hooks[hook_name].append(callback)
    
    def execute(self, hook_name, *args, **kwargs):
        """执行钩子"""
        for callback in self.hooks.get(hook_name, []):
            callback(*args, **kwargs)

# 创建插件管理器
plugins = PluginManager()

# 注册日志插件
def log_connection(host):
    print(f"[LOG] 连接到: {host}")

plugins.register('post_connect', log_connection)

# 使用
plugins.execute('post_connect', 'web-01')
```

---

## 总结

本教程介绍了 Remote CMD 的高级功能：

- **高级配置**：环境变量、超时控制
- **错误处理**：异常类型、重试机制
- **批量操作**：并行执行、异步模式
- **日志监控**：详细日志、执行监控
- **安全实践**：SSH Key、敏感信息处理
- **性能优化**：连接复用、压缩传输
- **自定义扩展**：插件系统

掌握这些技巧后，你可以更高效、更安全地管理远程服务器。

---

## 下一步

- [查看 API 文档](./API.md)
- [阅读故障排查](./TROUBLESHOOTING.md)
- [查看示例代码](../examples/)
- [参与贡献](../CONTRIBUTING.md)
