# 快速入门教程

本教程将帮助你在 15 分钟内掌握 Remote CMD 的基本用法。

## 目录

- [环境准备](#环境准备)
- [安装](#安装)
- [第一个连接](#第一个连接)
- [管理多台服务器](#管理多台服务器)
- [文件传输](#文件传输)
- [批量操作](#批量操作)
- [下一步](#下一步)

---

## 环境准备

### 系统要求

- **Python**: 3.9 或更高版本
- **操作系统**: Windows、macOS、Linux
- **网络**: 能够连接到目标 SSH 服务器

### 检查 Python 版本

```bash
python --version
# 或
python3 --version
```

如果版本低于 3.8，请先升级 Python。

### 准备测试服务器

你需要至少一台可以通过 SSH 访问的服务器。可以是：
- 本地虚拟机（VirtualBox、VMware）
- 云服务器（AWS、Azure、阿里云等）
- Docker 容器
- 局域网内的物理机

确保你有：
- 服务器的 IP 地址或主机名
- 用户名和密码 或 SSH 私钥
- SSH 端口（默认 22）

---

## 安装

### 方式一：从 PyPI 安装（推荐）

```bash
pip install remote-cmd
```

### 方式二：从源代码安装

```bash
# 克隆仓库
git clone https://github.com/Vae-Scrooge/remote-cmd.git
cd remote-cmd

# 创建虚拟环境（可选但推荐）
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 安装
pip install -e .
```

### 验证安装

```bash
# 检查版本
remote-cmd --version

# 查看帮助
remote-cmd --help
```

---

## 第一个连接

### 场景

连接到你的第一台服务器并执行一些基本命令。

### 使用 CLI

```bash
# 1. 添加主机配置
# 无私钥时按安全提示交互输入密码（不使用命令行参数传递密码）
remote-cmd host add my-server 192.168.1.100 ubuntu

# 查看已添加的主机
remote-cmd host list

# 2. 测试连接
remote-cmd host test my-server

# 3. 执行命令
remote-cmd run my-server "whoami"
remote-cmd run my-server "pwd"
remote-cmd run my-server "ls -la"
# 可选：为可能挂起的命令设置 wall-clock 超时
remote-cmd run my-server "uptime" --timeout 30

# 4. 查看系统信息
remote-cmd run my-server "uptime"
remote-cmd run my-server "df -h"
remote-cmd run my-server "free -h"
```

### 使用 Python API

创建一个 Python 脚本 `first_connection.py`：

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

# 配置连接
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    password="yourpassword",
    port=22
)

# 建立连接并执行命令
with SSHClient(config) as client:
    print("✅ 连接成功！")
    
    # 执行命令
    result = client.execute("whoami")
    print(f"当前用户: {result.stdout.strip()}")
    
    result = client.execute("pwd")
    print(f"当前目录: {result.stdout.strip()}")
    
    result = client.execute("uptime")
    print(f"系统运行时间: {result.stdout.strip()}")
```

运行脚本：

```bash
python first_connection.py
```

### 使用 SSH Key

如果你有 SSH 私钥，可以更安全地连接：

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

## 管理多台服务器

### 场景

管理一个包含 Web 服务器和数据库服务器的集群。

### 添加多台服务器

```bash
# Web 服务器 1
remote-cmd host add web-01 192.168.1.10 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag web \
    --tag production \
    --description "Primary web server"

# Web 服务器 2
remote-cmd host add web-02 192.168.1.11 ubuntu \
    --key ~/.ssh/id_rsa \
    --tag web \
    --tag production \
    --description "Secondary web server"

# 数据库服务器
remote-cmd host add db-01 192.168.1.20 admin \
    --tag database \
    --tag production \
    --description "MySQL database server"
# 无私钥时执行上一条命令后按提示交互输入密码
```

### 查看和筛选

```bash
# 列出所有主机
remote-cmd host list

# 只查看 Web 服务器
remote-cmd host list --tag web

# 只查看生产环境服务器
remote-cmd host list --tag production
```

### Python API 管理

```python
from remote_cmd.core.host import Host
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

# 创建仓库与主机服务
repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

# 批量添加
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

# 保存配置
repo.flush()

# 查看所有标签
print("可用标签:", manager.list_tags())

# 按标签筛选
web_servers = manager.list_hosts(tag="web")
for host in web_servers:
    print(f"Web 服务器: {host.name} ({host.hostname})")
```

---

## 文件传输

### 场景

上传应用代码到服务器，或下载日志文件到本地分析。

### 上传文件

```bash
# CLI
remote-cmd upload my-server ./deploy.sh /tmp/deploy.sh

# 验证上传
remote-cmd run my-server "ls -la /tmp/deploy.sh"
```

### 下载文件

```bash
# CLI
remote-cmd download my-server /var/log/nginx/access.log ./logs/

# 下载到当前目录
remote-cmd download my-server /etc/nginx/nginx.conf ./
```

### Python API 文件操作

```python
from remote_cmd.core.ssh_client import SSHClient, ConnectionConfig

config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    key_filename="~/.ssh/id_rsa"
)

with SSHClient(config) as client:
    # 上传文件
    print("📤 上传 deploy.sh...")
    client.upload_file("./deploy.sh", "/tmp/deploy.sh")
    
    # 验证上传
    result = client.execute("ls -la /tmp/deploy.sh")
    print(f"远程文件: {result.stdout}")
    
    # 下载日志
    print("📥 下载日志文件...")
    client.download_file("/var/log/syslog", "./syslog")
    
    # 列出远程目录
    print("📂 远程 /tmp 目录内容:")
    entries = client.list_remote_directory("/tmp")
    for entry in entries[:5]:  # 只显示前 5 个
        icon = "📁" if entry.is_dir else "📄"
        print(f"  {icon} {entry.name}")
```

---

## 批量操作

### 场景

同时在多台服务器上执行相同的操作。

### 批量执行命令

```python
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

# 在所有 Web 服务器上执行
for host in manager.list_hosts(tag="web"):
    print(f"\n🖥️  {host.name} ({host.hostname})")
    
    try:
        with manager.connect_to_host(host.name) as client:
            # 检查 Nginx 状态
            result = client.execute("systemctl status nginx")
            
            if result.success:
                print("  ✅ Nginx 运行正常")
            else:
                print("  ⚠️  Nginx 状态异常")
                print(f"     {result.stderr[:100]}")
    
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")

print("\n✨ 批量操作完成")
```

### 并行执行（高级）

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

repo = JsonHostRepository("my-hosts.json")
manager = HostService(repository=repo)

def check_host(host):
    """检查单个主机的状态"""
    try:
        with manager.connect_to_host(host.name) as client:
            result = client.execute("uptime")
            return host.name, True, result.stdout.strip()
    except Exception as e:
        return host.name, False, str(e)

hosts = manager.list_hosts()

print(f"🚀 并行检查 {len(hosts)} 台服务器...\n")

with ThreadPoolExecutor(max_workers=5) as executor:
    # 提交所有任务
    future_to_host = {
        executor.submit(check_host, host): host 
        for host in hosts
    }
    
    # 处理结果
    for future in as_completed(future_to_host):
        host_name, success, message = future.result()
        status = "✅" if success else "❌"
        print(f"{status} {host_name}: {message[:50]}")
```

---

## 实战：自动化部署脚本

创建一个完整的部署脚本：

```python
#!/usr/bin/env python3
"""
deploy.py - 简单的自动化部署脚本

使用方式：
    python deploy.py <host_name>
"""

import sys
import argparse
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService

def deploy(host_name: str):
    """部署应用到指定服务器"""
    repo = JsonHostRepository("my-hosts.json")
    manager = HostService(repository=repo)
    
    print(f"🚀 开始部署到 {host_name}...")
    print("=" * 50)
    
    try:
        with manager.connect_to_host(host_name) as client:
            # 1. 上传代码
            print("📤 上传应用代码...")
            client.upload_file("./app.tar.gz", "/tmp/app.tar.gz")
            
            # 2. 停止服务
            print("🛑 停止应用服务...")
            result = client.execute("sudo systemctl stop myapp")
            
            # 3. 部署代码
            print("📦 解压并部署...")
            commands = [
                "cd /var/www && tar -xzf /tmp/app.tar.gz",
                "cd /var/www/app && pip install -r requirements.txt",
            ]
            for cmd in commands:
                result = client.execute(cmd)
                if not result.success:
                    print(f"❌ 部署失败: {result.stderr}")
                    sys.exit(1)
            
            # 4. 启动服务
            print("▶️  启动应用服务...")
            result = client.execute("sudo systemctl start myapp")
            
            # 5. 健康检查
            print("🔍 健康检查...")
            result = client.execute("curl -s http://localhost:8080/health")
            
            if "ok" in result.stdout.lower():
                print("\n✅ 部署成功！")
            else:
                print("\n⚠️  部署完成但健康检查失败")
                
    except Exception as e:
        print(f"\n❌ 部署失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="部署脚本")
    parser.add_argument("host", help="目标主机名称")
    args = parser.parse_args()
    
    deploy(args.host)
```

使用方法：

```bash
# 部署到单台服务器
python deploy.py web-01

# 部署到所有 Web 服务器
for host in web-01 web-02; do
    python deploy.py $host
done
```

---

## 常见问题

### 1. 连接超时

**问题：**
```
SSHTimeoutError: connection timeout: 192.168.1.100
```

**解决方案：**
- 检查网络连接：`ping <hostname>`
- 检查 SSH 端口：`nc -zv <hostname> 22`
- 增加超时时间：

```python
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="ubuntu",
    password="pass",
    timeout=60  # 增加超时时间
)
```

### 2. 认证失败

**问题：**
```
SSHAuthenticationError: authentication failed: Authentication failed.
```

**解决方案：**
- 检查用户名和密码
- 检查 SSH 密钥权限：`chmod 600 ~/.ssh/id_rsa`
- 检查 authorized_keys 配置

### 3. 权限不足

**问题：**
```
Permission denied
```

**解决方案：**
```python
# 使用 sudo
result = client.execute_sudo("systemctl restart nginx", password="sudopass")
```

---

## 下一步

恭喜你完成了快速入门！接下来你可以：

1. **[阅读 API 文档](./API.md)** - 了解所有可用的 API
2. **[查看高级教程](./tutorial-advanced.md)** - 学习更多高级功能
3. **[查看示例代码](../examples/)** - 参考更多使用示例
4. **[阅读故障排查](./TROUBLESHOOTING.md)** - 解决常见问题

---

## 获取帮助

如果你遇到问题：

1. 查看 [故障排查指南](./TROUBLESHOOTING.md)
2. 搜索 [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues)
3. 提交新的 Issue

---

**祝你使用愉快！** 🎉
