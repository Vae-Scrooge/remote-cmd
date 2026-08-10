# 故障排查指南

本文档列出了使用 Remote CMD 时可能遇到的常见问题及其解决方案。

## 目录

- [连接问题](#连接问题)
- [认证问题](#认证问题)
- [命令执行问题](#命令执行问题)
- [文件传输问题](#文件传输问题)
- [性能问题](#性能问题)
- [配置问题](#配置问题)
- [环境问题](#环境问题)
- [调试技巧](#调试技巧)
- [获取帮助](#获取帮助)

---

## 连接问题

### 1. 连接超时 (Connection Timeout)

**错误信息：**
```
SSHConnectionError: Connection timeout to 192.168.1.100
```

**可能原因：**
- 网络不可达
- 防火墙阻挡
- SSH 服务未运行
- IP 地址或端口错误

**解决方案：**

```bash
# 1. 检查网络连通性
ping 192.168.1.100

# 2. 检查 SSH 端口
nc -zv 192.168.1.100 22
# 或
telnet 192.168.1.100 22

# 3. 增加超时时间
python -m remote_cmd host add my-server 192.168.1.100 admin --password pass --port 22
```

```python
# Python API 中设置更长的超时
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="pass",
    timeout=60  # 增加到 60 秒
)
```

---

### 2. 主机不可达 (Host Unreachable)

**错误信息：**
```
SSHConnectionError: Host not found: example.com
```

**解决方案：**

```bash
# 1. 检查 DNS 解析
nslookup example.com
dig example.com

# 2. 使用 IP 地址代替域名
python -m remote_cmd host add my-server 192.168.1.100 admin --password pass

# 3. 检查 hosts 文件
# Linux/macOS: /etc/hosts
# Windows: C:\Windows\System32\drivers\etc\hosts
```

---

### 3. 连接被拒绝 (Connection Refused)

**错误信息：**
```
SSHConnectionError: [Errno 111] Connection refused
```

**可能原因：**
- SSH 服务未启动
- 端口错误
- 防火墙阻挡

**解决方案：**

```bash
# 在远程服务器上检查 SSH 服务状态
# Linux (Debian/Ubuntu):
sudo systemctl status ssh

# Linux (CentOS/RHEL):
sudo systemctl status sshd

# 启动 SSH 服务
sudo systemctl start ssh
sudo systemctl enable ssh

# 检查端口
sudo netstat -tlnp | grep ssh
sudo ss -tlnp | grep ssh

# 检查防火墙
sudo ufw status
sudo iptables -L | grep 22

# 临时关闭防火墙测试（仅用于测试）
sudo ufw disable
```

---

## 认证问题

### 1. 认证失败 (Authentication Failed)

**错误信息：**
```
SSHConnectionError: Authentication failed: Authentication failed.
```

**可能原因：**
- 用户名错误
- 密码错误
- SSH Key 错误
- 权限不足

**解决方案：**

#### 情况 A：密码认证失败

```bash
# 1. 确认用户名和密码
ssh admin@192.168.1.100  # 手动测试

# 2. 检查用户是否存在
id username

# 3. 检查用户是否被锁定
sudo passwd -S username
```

#### 情况 B：SSH Key 认证失败

```bash
# 1. 检查私钥文件权限（必须是 600）
ls -la ~/.ssh/id_rsa
chmod 600 ~/.ssh/id_rsa

# 2. 检查公钥是否在 authorized_keys 中
cat ~/.ssh/id_rsa.pub
ssh user@server "cat ~/.ssh/authorized_keys"

# 3. 检查 authorized_keys 权限
ssh user@server "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

# 4. 使用绝对路径
python -m remote_cmd host add my-server 192.168.1.100 admin \
    --key /home/username/.ssh/id_rsa  # 使用绝对路径，不是 ~/
```

#### 情况 C：SELinux 问题（CentOS/RHEL）

```bash
# 检查 SELinux 状态
getenforce

# 临时设置为宽容模式（测试）
sudo setenforce 0

# 或者修复 SELinux 上下文
restorecon -Rv ~/.ssh
```

---

### 2. 主机密钥验证失败

**错误信息：**
```
paramiko.SSHException: Server '192.168.1.100' not found in known_hosts
```

**解决方案：**

```bash
# 方法 1：手动添加主机密钥
ssh-keyscan -H 192.168.1.100 >> ~/.ssh/known_hosts

# 方法 2：首次连接时接受
ssh user@192.168.1.100  # 输入 yes

# 方法 3：代码中禁用严格检查（不推荐用于生产环境）
```

```python
# Python API 中（已默认实现）
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
```

---

### 3. 权限拒绝 (Permission Denied)

**错误信息：**
```
paramiko.SSHException: Permission denied
```

**可能原因：**
- 用户被禁用 SSH 登录
- 只允许 Key 认证，但使用了密码
- root 登录被禁止

**解决方案：**

```bash
# 检查 SSH 配置
cat /etc/ssh/sshd_config | grep -E "^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication)"

# 确保以下配置正确：
# PasswordAuthentication yes
# PubkeyAuthentication yes
# PermitRootLogin yes  # 或 prohibit-password

# 重启 SSH 服务
sudo systemctl restart sshd
```

---

## 命令执行问题

### 1. 命令未找到 (Command Not Found)

**错误信息：**
```
stdout: 
stderr: bash: command: command not found
exit_code: 127
```

**解决方案：**

```python
# 1. 使用完整路径
result = client.execute("/usr/bin/python3 --version")

# 2. 先 source 环境
result = client.execute("source ~/.bashrc && python3 --version")

# 3. 使用 which 查找路径
result = client.execute("which python3")
python_path = result.stdout.strip()
result = client.execute(f"{python_path} --version")
```

---

### 2. 权限不足 (Permission Denied)

**错误信息：**
```
stderr: Permission denied
exit_code: 126
```

**解决方案：**

```python
# 使用 sudo
result = client.execute_sudo("systemctl restart nginx", password="sudopass")

# 或者先检查权限
result = client.execute("whoami")
print(f"当前用户: {result.stdout.strip()}")

# 检查文件权限
result = client.execute("ls -la /path/to/file")
```

---

### 3. 命令超时 (Command Timeout)

**错误信息：**
```
socket.timeout: The read operation timed out
```

**解决方案：**

```python
# 增加超时时间
result = client.execute("long_running_command", timeout=300)  # 5 分钟

# 或使用 nohup 在后台运行
result = client.execute("nohup long_command > /tmp/output.log 2>&1 &")
```

---

## 文件传输问题

### 1. 文件不存在 (File Not Found)

**错误信息：**
```
SSHFileTransferError: Local file not found: ./file.txt
```

**解决方案：**

```python
import os
from pathlib import Path

# 检查文件是否存在
local_path = "./file.txt"
if not os.path.exists(local_path):
    print(f"错误：文件 {local_path} 不存在")
    print(f"当前目录: {os.getcwd()}")
    print(f"目录内容: {os.listdir('.')}")
else:
    client.upload_file(local_path, "/remote/path/")

# 使用绝对路径
local_path = Path("./file.txt").resolve()
client.upload_file(str(local_path), "/remote/path/")
```

---

### 2. 权限拒绝 (Permission Denied)

**错误信息：**
```
SSHFileTransferError: [Errno 13] Permission denied: '/remote/path'
```

**解决方案：**

```python
# 1. 上传到临时目录，然后移动
client.upload_file("./file.txt", "/tmp/file.txt")
client.execute_sudo("mv /tmp/file.txt /restricted/path/", password="pass")

# 2. 检查远程目录权限
result = client.execute("ls -ld /remote/path")
print(result.stdout)

# 3. 使用 sudo
client.execute_sudo("chmod 777 /remote/path", password="pass")
```

---

### 3. 磁盘空间不足 (No Space Left)

**错误信息：**
```
SSHFileTransferError: [Errno 28] No space left on device
```

**解决方案：**

```bash
# 检查磁盘空间
df -h

# 清理空间
sudo apt clean  # Debian/Ubuntu
sudo yum clean all  # CentOS/RHEL
docker system prune  # 如果使用 Docker

# 查找大文件
sudo du -sh /var/log/*
sudo find /tmp -type f -mtime +7 -delete
```

---

## 性能问题

### 1. 连接速度慢

**症状：**
- 建立连接需要很长时间
- 命令执行延迟高

**解决方案：**

```python
# 1. 启用压缩
config = ConnectionConfig(
    hostname="192.168.1.100",
    username="admin",
    password="pass",
    compress=True  # 启用压缩
)

# 2. 使用连接池（示例）
from contextlib import contextmanager

@contextmanager
def get_client(config):
    client = SSHClient(config)
    try:
        yield client.connect()
    finally:
        client.disconnect()

# 复用连接
with get_client(config) as client:
    for cmd in commands:
        result = client.execute(cmd)
```

---

### 2. 文件传输慢

**解决方案：**

```python
# 1. 压缩后传输
client.execute("tar -czf /tmp/archive.tar.gz /large/directory")
client.download_file("/tmp/archive.tar.gz", "./archive.tar.gz")

# 2. 使用 rsync（如果可用）
result = client.execute("which rsync")
if result.success:
    # 使用 rsync
    pass
```

---

## 配置问题

### 1. 配置文件解析错误

**错误信息：**
```
yaml.scanner.ScannerError: mapping values are not allowed here
```

**解决方案：**

```yaml
# 确保 YAML 格式正确
# config.yaml

# 正确
hosts_file: hosts.json

# 错误（冒号后要有空格）
hosts_file:hosts.json

# 正确（列表格式）
tags:
  - web
  - production

# 错误
tags: [web, production]  # 这也正确，但要一致
```

---

### 2. 配置文件找不到

**错误信息：**
```
FileNotFoundError: [Errno 2] No such file or directory: 'config.yaml'
```

**解决方案：**

```bash
# 1. 检查文件位置
ls -la config.yaml

# 2. 使用绝对路径
remote-cmd --config /full/path/to/config.yaml host list

# 3. 创建默认配置
cp config.example.yaml config.yaml
```

---

## 环境问题

### 1. Python 版本不兼容

**错误信息：**
```
SyntaxError: invalid syntax
```

**解决方案：**

```bash
# 检查 Python 版本
python --version  # 需要 3.9+

# 使用特定版本
python3.9 -m remote_cmd --version
python3.10 -m remote_cmd --version

# 创建指定版本的虚拟环境
python3.9 -m venv venv
```

---

### 2. 依赖缺失

**错误信息：**
```
ModuleNotFoundError: No module named 'paramiko'
```

**解决方案：**

```bash
# 重新安装依赖
pip install -r requirements.txt

# 或单独安装
pip install paramiko click pyyaml

# 检查安装
pip list | grep paramiko
```

---

### 3. Windows 特定问题

#### 问题：命令行输出无颜色

**解决方案：**

```bash
# 安装 colorama
pip install colorama

# 或在 PowerShell 中启用 ANSI
$env:PYTHONIOENCODING="utf-8"
```

#### 问题：路径分隔符

```python
# 使用 Path 库处理跨平台路径
from pathlib import Path, PurePosixPath

# Windows 路径
local_path = Path("C:/Users/name/file.txt")

# Linux 远程路径
remote_path = PurePosixPath("/home/user/file.txt")
```

---

## 调试技巧

### 启用详细日志

```python
import logging

# 启用调试日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 或使用远程命令的日志
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### 检查详细信息

```python
# 检查连接信息
print(f"连接状态: {client.is_connected()}")
print(f"配置: {client.config}")

# 检查命令结果
result = client.execute("echo test")
print(f"命令: {result.command}")
print(f"退出码: {result.exit_code}")
print(f"stdout: {repr(result.stdout)}")
print(f"stderr: {repr(result.stderr)}")
```

### 使用交互式调试

```python
import pdb

# 在代码中设置断点
def my_function():
    client = SSHClient(config)
    pdb.set_trace()  # 断点
    client.connect()
```

### 网络诊断

```bash
# 检查网络连通性
ping <hostname>

# 检查端口
nc -zv <hostname> 22
telnet <hostname> 22

# 检查 DNS
nslookup <hostname>
dig <hostname>

# 检查路由
traceroute <hostname>  # Linux/macOS
tracert <hostname>     # Windows
```

---

## 获取帮助

如果以上解决方案都无法解决您的问题：

### 1. 收集信息

```bash
# 收集系统信息
python --version
pip list

# 收集错误信息
python -m remote_cmd --verbose host test my-server 2>&1 | tee error.log
```

### 2. 提交 Issue

访问 [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues) 并提交问题，请包含：

- 问题描述
- 复现步骤
- 错误信息（完整堆栈跟踪）
- 环境信息（OS、Python 版本）
- 配置文件（脱敏后）

### 3. 其他资源

- [GitHub Discussions](https://github.com/Vae-Scrooge/remote-cmd/discussions) - 社区讨论
- [API 文档](./API.md) - API 参考
- [开发指南](./DEVELOPMENT.md) - 开发相关

---

## 快速检查清单

大多数问题都可以通过检查以下项目解决：

1. ✅ 网络连接正常
2. ✅ SSH 服务运行中
3. ✅ 用户名和密码/密钥正确
4. ✅ 文件路径正确
5. ✅ 权限足够
6. ✅ Python 版本兼容（3.9+）
7. ✅ 依赖已安装

---

**提示：** 在寻求帮助前，请先：

1. 查看本文档相关章节
2. 搜索已有 Issues
3. 尝试最小复现

---

*最后更新：2024年*
