# 手机远程操控完全指南

<p align="center">
  <a href="./MOBILE-REMOTE-GUIDE.md"><img src="https://img.shields.io/badge/English-gray?style=flat-square" alt="English"></a> ·
  <img src="https://img.shields.io/badge/中文-blue?style=flat-square" alt="中文">
</p>

<div align="center">

[![Platform](https://img.shields.io/badge/platform-iOS%20%7C%20Android-blue)](.)
[![Protocol](https://img.shields.io/badge/protocol-SSH%20%7C%20RDP%20%7C%20VNC-orange)](.)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**从手机随时随地管理服务器、电脑和设备**

[SSH 方案](#一-ssh-远程操控方案) | [远程桌面](#二-远程桌面方案) | [手机控制手机](#三-手机控制手机方案) | [安全指南](#四-安全配置指南) | [App 对比](#五-app-对比推荐)

</div>

---

## 目录

- [前言](#前言)
- [一、SSH 远程操控方案](#一-ssh-远程操控方案)
- [二、远程桌面方案](#二-远程桌面方案)
- [三、手机控制手机方案](#三-手机控制手机方案)
- [四、命令行方案](#四-命令行方案)
- [五、安全配置指南](#五-安全配置指南)
- [六、App 对比推荐](#六-app-对比推荐)
- [七、实战示例](#七-实战示例)
- [八、常见问题](#八-常见问题)

---

## 前言

在移动互联网时代，手机不仅是通讯工具，更是强大的远程管理终端。无论你是：

- 🖥️ **系统管理员** - 需要 7×24 小时响应服务器告警
- 💻 **开发者** - 需要紧急修复线上 Bug
- 🏠 **家庭用户** - 需要远程协助父母解决电脑问题
- 📱 **极客玩家** - 希望在手机上体验完整 Linux 环境

本指南将帮助你找到最适合的手机远程操控方案。

---

## 一、SSH 远程操控方案

SSH（Secure Shell）是管理 Linux/Unix 服务器的标准协议，安全可靠。

### 📱 推荐 App

#### iOS 平台

| App | 价格 | 评分 | 特点 |
|-----|------|------|------|
| **Termius** | 免费/订阅 | ⭐⭐⭐⭐⭐ | 跨平台同步，界面美观，支持 SFTP |
| **Blink Shell** | 付费 | ⭐⭐⭐⭐⭐ | 专业级，支持 Mosh，开发者首选 |
| **ShellFish** | 免费/订阅 | ⭐⭐⭐⭐ | 专注文件管理，集成 SFTP |
| **SecureCRT** | 付费 | ⭐⭐⭐⭐ | 企业级，功能强大 |

**推荐：Termius（免费够用）或 Blink Shell（专业首选）**

#### Android 平台

| App | 价格 | 评分 | 特点 |
|-----|------|------|------|
| **Termius** | 免费/订阅 | ⭐⭐⭐⭐⭐ | 跨平台，功能全面 |
| **JuiceSSH** | 免费/Pro | ⭐⭐⭐⭐⭐ | 老牌应用，插件丰富 |
| **ConnectBot** | 开源免费 | ⭐⭐⭐⭐ | 开源轻量，无广告 |
| **SimpleSSH** | 免费 | ⭐⭐⭐⭐ | 简洁易用 |

**推荐：Termius 或 JuiceSSH**

### 🚀 快速开始（Termius）

#### 1. 下载安装
- iOS：App Store 搜索 "Termius"
- Android：Google Play 或酷安

#### 2. 添加主机
```
1. 打开 Termius → 点击右上角 "+"
2. 选择 "New Host"
3. 填写信息：
   - Alias: 我的服务器（任意名称）
   - Hostname: 192.168.1.100（IP 或域名）
   - Port: 22（默认 SSH 端口）
   - Username: root（或你的用户名）
   
4. 认证方式（二选一）：
   
   方式 A - 密码：
   - Password: 输入密码
   
   方式 B - SSH 密钥（推荐）：
   - 点击 "Key" → "Generate Key" 或导入已有密钥
```

#### 3. 连接服务器
- 点击保存的主机
- 首次连接会提示保存主机密钥 → 点击 "Accept"
- 连接成功！现在你可以输入命令了

### 🔑 密钥认证设置

**生成密钥对（推荐）：**
```bash
# 在 Termius 中：
Settings → Keychain → Generate Key → RSA/Ed25519

# 复制公钥到服务器：
# 方式 1：手动复制
1. 在 Termius 中点击密钥 → Copy Public Key
2. 登录服务器，添加到 ~/.ssh/authorized_keys

# 方式 2：使用 Termius 内置功能
1. 连接后输入密码
2. Termius 会提示保存密钥
3. 点击 "Copy ID" 自动部署公钥
```

### 💡 高级功能

**SFTP 文件传输：**
```
Termius 连接后 → 左滑 → 选择 "SFTP"
可以：
- 上传/下载文件
- 浏览远程文件系统
- 编辑文本文件
```

**端口转发（Tunnel）：**
```
应用场景：访问内网服务
设置 → Port Forwarding → Add Rule
Local Port: 8080
Remote Host: localhost
Remote Port: 80

然后手机浏览器访问 localhost:8080
```

---

## 二、远程桌面方案

远程桌面让你通过手机操控电脑的图形界面。

### 🖥️ 协议选择

| 协议 | 适用场景 | 优点 | 缺点 |
|------|----------|------|------|
| **RDP** | Windows 服务器 | 效率高，原生支持 | Linux 需要配置 |
| **VNC** | Linux/Mac/Windows | 跨平台 | 较慢，不安全 |
| **TeamViewer** | 跨平台/远程协助 | 穿透内网，易用 | 商业软件 |
| **Chrome 远程桌面** | 个人使用 | 免费，简单 | 需要 Chrome |

### 📱 推荐 App

#### iOS

| App | 协议 | 特点 |
|-----|------|------|
| **Microsoft Remote Desktop** | RDP | 微软官方，流畅 |
| **VNC Viewer** | VNC | RealVNC 官方 |
| **TeamViewer** | 专有 | 远程协助首选 |
| **AnyDesk** | 专有 | 轻量快速 |
| **Chrome 远程桌面** | 专有 | 需要 Chrome 扩展 |

#### Android

| App | 协议 | 特点 |
|-----|------|------|
| **Microsoft Remote Desktop** | RDP | 微软官方 |
| **VNC Viewer** | VNC | 官方客户端 |
| **TeamViewer** | 专有 | 功能全面 |
| **AnyDesk** | 专有 | 低延迟 |
| **RustDesk** | 专有 | 开源替代方案 |

### 🚀 快速开始

#### 方案 A：Windows RDP（局域网）

**电脑端设置：**
```
1. Windows 设置 → 系统 → 远程桌面 → 启用
2. 记下计算机名或 IP 地址
3. 确保手机和电脑在同一网络（或配置端口转发）
```

**手机端连接：**
```
1. 安装 Microsoft Remote Desktop
2. 点击 "+" → "Desktop"
3. PC Name: 192.168.1.100（或计算机名）
4. User Account: 添加 Windows 用户名密码
5. 点击连接
```

#### 方案 B：TeamViewer（跨网络）

**电脑端：**
```
1. 下载 TeamViewer（teamviewer.com）
2. 安装并运行
3. 获取 ID 和密码
```

**手机端：**
```
1. 安装 TeamViewer App
2. 输入电脑的 Partner ID
3. 输入密码
4. 连接成功！
```

**特点：**
- ✅ 无需配置路由器
- ✅ 穿透内网
- ✅ 文件传输
- ✅ 语音通话

#### 方案 C：RustDesk（开源免费）

**自托管服务器（可选）：**
```bash
# 使用 Docker 部署
docker run --net=host rustdesk/rustdesk-server-hbbr
docker run --net=host rustdesk/rustdesk-server-hbbs
```

**使用方式：**
- 类似 TeamViewer
- 完全免费
- 数据可控（可自建服务器）

---

## 三、手机控制手机方案

用于远程协助家人、朋友解决手机问题。

### 📱 推荐方案

#### TeamViewer QuickSupport（跨平台）

**被控端（需要帮助的手机）：**
```
1. 安装 TeamViewer QuickSupport
2. 打开后会显示 ID
```

**控制端（你的手机）：**
```
1. 安装 TeamViewer
2. 输入对方 ID
3. 对方接受后即可控制
```

**限制：**
- iOS 无法被远程控制（系统限制），只能屏幕共享
- Android 可以完整远程控制（需开启无障碍服务）

#### AirDroid（文件+远程）

**功能：**
- 文件传输
- 短信管理
- 远程相机
- 屏幕镜像

#### Scrcpy（Android 投屏）

**电脑控制手机：**
```bash
# 需要电脑配合
scrcpy --tcpip=192.168.1.100:5555
```

**然后手机可以查看投屏**

---

## 四、命令行方案

在手机上运行完整的 Linux 环境。

### iOS：iSH（免费）

```
App Store 搜索 "iSH"

功能：
- 本地 Alpine Linux 环境
- 安装软件：apk add openssh
- 可以 SSH 连接其他服务器
- 支持 Python、Git 等
```

**安装 SSH 客户端：**
```bash
# 在 iSH 中
apk update
apk add openssh-client

# 连接服务器
ssh user@hostname
```

### Android：Termux（开源免费）

```
F-Droid 或 GitHub 下载（Google Play 版本较旧）

功能：
- 完整的 Linux 环境
- 包管理器：pkg/apt
- 支持 Python、Node.js、Git
- 可以安装 OpenSSH
```

**Termux SSH 连接：**
```bash
# 安装 OpenSSH
pkg install openssh

# 生成密钥
ssh-keygen -t ed25519

# 连接服务器
ssh user@hostname

# 使用密钥
ssh -i ~/.ssh/id_ed25519 user@hostname
```

**Termux 高级用法：**
```bash
# 安装完整开发环境
pkg install git python nodejs vim

# 安装远程操控工具
pkg install tmux mosh

# 使用 tmux 保持会话
tmux new -s mysession

# 使用 mosh 低延迟连接
mosh user@hostname
```

---

## 五、安全配置指南

### 🔐 基础安全

#### 1. 使用密钥认证（禁用密码）

**服务器端设置：**
```bash
# 编辑 SSH 配置
sudo nano /etc/ssh/sshd_config

# 修改以下配置
PasswordAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password

# 重启 SSH
sudo systemctl restart sshd
```

#### 2. 使用非标准端口

```bash
# 修改 SSH 端口为 2222
Port 2222

# 防火墙放行
sudo ufw allow 2222/tcp
```

#### 3. 使用 Fail2ban 防止暴力破解

```bash
# 安装
sudo apt install fail2ban

# 配置
sudo nano /etc/fail2ban/jail.local

[sshd]
enabled = true
port = 2222
maxretry = 3
bantime = 3600
```

### 🛡️ 高级安全

#### VPN 方案

**WireGuard（推荐）：**
```bash
# 安装 WireGuard
sudo apt install wireguard

# 生成密钥对
wg genkey | tee privatekey | wg pubkey > publickey

# 配置服务器
sudo nano /etc/wireguard/wg0.conf

[Interface]
PrivateKey = <服务器私钥>
Address = 10.0.0.1/24
ListenPort = 51820
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT

[Peer]
PublicKey = <手机公钥>
AllowedIPs = 10.0.0.2/32

# 启动
sudo wg-quick up wg0
```

**手机端：**
- iOS：App Store 搜索 "WireGuard"
- Android：Play Store 搜索 "WireGuard"
- 导入配置即可

#### 跳板机（Bastion Host）

```
场景：保护内网服务器

互联网 → 跳板机（公网 IP） → 内网服务器

手机只连接跳板机，跳板机再连接内网
```

**SSH 配置：**
```
# 在 Termius 中配置
1. 先添加跳板机
2. 添加内网服务器，设置：
   - Gateway/Jump Host: 选择跳板机
   
3. 连接内网服务器时，会自动通过跳板机
```

### 📱 手机端安全

#### 1. 开启应用锁
```
iOS：设置 → 屏幕使用时间 → App 限额
Android：设置 → 应用锁
```

#### 2. 使用密码管理器
- 1Password
- Bitwarden
- iOS 钥匙串

#### 3. 定期清理密钥
```
Termius：Settings → Keychain → 删除不再使用的密钥
```

---

## 六、App 对比推荐

### 📊 综合对比表

| App | 平台 | 协议 | 价格 | 推荐度 | 适用场景 |
|-----|------|------|------|--------|----------|
| **Termius** | iOS/Android | SSH | 免费/订阅 | ⭐⭐⭐⭐⭐ | SSH/SFTP 首选 |
| **Blink Shell** | iOS | SSH/Mosh | 付费 | ⭐⭐⭐⭐⭐ | 专业开发 |
| **JuiceSSH** | Android | SSH | 免费/Pro | ⭐⭐⭐⭐⭐ | Android SSH |
| **TeamViewer** | 全平台 | 专有 | 免费/商业 | ⭐⭐⭐⭐⭐ | 远程协助 |
| **AnyDesk** | 全平台 | 专有 | 免费/商业 | ⭐⭐⭐⭐ | 快速连接 |
| **RustDesk** | 全平台 | 专有 | 开源免费 | ⭐⭐⭐⭐ | 自托管 |
| **Microsoft RDP** | iOS/Android | RDP | 免费 | ⭐⭐⭐⭐⭐ | Windows 远程 |
| **VNC Viewer** | 全平台 | VNC | 免费 | ⭐⭐⭐⭐ | Linux/Mac 远程 |
| **iSH** | iOS | 本地 | 免费 | ⭐⭐⭐⭐⭐ | 本地 Linux |
| **Termux** | Android | 本地 | 开源 | ⭐⭐⭐⭐⭐ | 本地 Linux |

### 🎯 场景推荐

#### 系统管理员
- **主选**：Termius + WireGuard VPN
- **备份**：JuiceSSH（Android）+ Blink Shell（iOS）
- **应急**：Termux（离线文档查询）

#### 开发者
- **主选**：Blink Shell（iOS）/ Termux（Android）
- **配合**：GitHub App + Working Copy（Git 客户端）
- **调试**：iSH（本地测试）

#### 家庭用户
- **主选**：TeamViewer（简单易用）
- **替代**：RustDesk（免费无广告）
- **手机互控**：TeamViewer QuickSupport

#### 极客玩家
- **主选**：Termux（完整 Linux）+ Termius（SSH）
- **探索**：UserLAnd（完整 Linux 发行版）
- **自建**：RustDesk 服务器

---

## 七、实战示例

### 场景 1：紧急修复线上 Bug

```
时间：凌晨 2 点
地点：家中床上
设备：iPhone

操作步骤：
1. 收到告警短信，服务器 CPU 100%
2. 打开 Termius → 点击生产服务器
3. 自动连接（密钥认证）
4. 执行命令：
   $ top
   $ ps aux | grep python
   $ kill -9 <PID>
5. 查看日志：
   $ tail -f /var/log/app/error.log
6. 重启服务：
   $ sudo systemctl restart app
7. 验证：
   $ curl http://localhost:8080/health
8. 问题解决，总耗时 3 分钟
```

### 场景 2：远程协助父母修电脑

```
对象：父母的 Windows 电脑
问题：不会安装打印机驱动

操作步骤：
1. 让父母下载 TeamViewer QuickSupport
2. 打开后告诉 ID 和密码
3. 你的手机上打开 TeamViewer
4. 输入父母的 ID 连接
5. 远程操控安装驱动
6. 完成后断开连接

优点：
- 父母无需懂技术
- 可视化操作，简单直观
- 可以语音通话指导
```

### 场景 3：地铁上部署代码

```
场景：下班路上，需要紧急发布版本

操作步骤：
1. 地铁上打开 Termius
2. 连接跳板机 → 连接生产服务器
3. 执行部署脚本：
   $ cd /var/www/app
   $ git pull origin main
   $ ./deploy.sh
4. 查看部署日志：
   $ tail -f deploy.log
5. 健康检查：
   $ curl -s http://localhost/health
6. 发布完成 ✅
```

### 场景 4：手机上搭建开发环境

```
设备：Android 手机
目标：完整的 Python 开发环境

步骤：
1. 安装 Termux
2. 安装开发工具：
   $ pkg install git python vim
3. 克隆项目：
   $ git clone https://github.com/user/project.git
4. 安装依赖：
   $ cd project
   $ pip install -r requirements.txt
5. 编辑代码：
   $ vim main.py
6. 运行测试：
   $ python -m pytest
7. 推送到 GitHub：
   $ git add .
   $ git commit -m "fix: bug"
   $ git push
```

---

## 八、常见问题

### Q1: 为什么 iOS 无法像 Android 那样远程控制？
**A:** iOS 系统安全限制，不允许后台应用控制屏幕。只能实现屏幕共享。

### Q2: SSH 连接失败怎么办？
**排查步骤：**
```
1. 检查网络：ping 服务器 IP
2. 检查端口：nc -zv IP 22
3. 检查服务：服务器上 systemctl status sshd
4. 检查防火墙：ufw status / iptables -L
5. 检查日志：服务器 /var/log/auth.log
```

### Q3: 如何提高远程桌面的流畅度？
**优化方法：**
- 降低分辨率和色彩质量
- 使用有线网络代替 WiFi
- 关闭远程计算机的视觉效果
- 使用专线/VPN（避免公网延迟）

### Q4: 手机电池消耗快怎么办？
**建议：**
- 不使用时就断开连接
- 降低屏幕亮度
- 使用深色模式
- 携带充电宝

### Q5: 如何确保手机丢失后服务器安全？
**措施：**
1. 手机设置强密码/面容 ID
2. Termius 开启应用锁
3. 服务器使用密钥认证（非密码）
4. 服务器配置 Fail2ban
5. 定期更换密钥
6. 开启手机远程擦除功能

---

## 九、进阶技巧

### 使用快捷指令（iOS）

```
创建快捷指令实现一键连接：

1. 打开 "快捷指令" App
2. 新建快捷指令
3. 添加操作：
   - URL: ssh://user@hostname
   - 打开 URL
4. 添加到主屏幕
5. 点击图标直接打开 Termius 并连接
```

### 使用 Siri 语音连接

```
"嘿 Siri，连接我的服务器"

设置方法：
1. 创建快捷指令（如上）
2. 点击 "设置" → "添加到 Siri"
3. 录制语音指令
```

### 自动化脚本（Termux）

```bash
# 创建连接脚本
nano ~/connect.sh

#!/bin/bash
echo "正在连接服务器..."
ssh -i ~/.ssh/id_ed25519 user@hostname

# 添加到快捷方式
termux-shortcuts
# 然后在桌面创建快捷方式
```

---

## 结语

手机远程操控技术让"随时随地管理设备"成为可能。无论是处理紧急故障、协助家人朋友，还是单纯体验移动办公的便利，选择适合的工具都能事半功倍。

**记住三个原则：**
1. 🔐 安全第一 - 使用密钥、VPN、强密码
2. ⚡ 效率至上 - 选择低延迟、易操作的工具
3. 📱 场景适配 - 根据实际需求选择方案

---

## 贡献

欢迎提交 PR 补充更多方案和经验！

**需要补充的内容：**
- 更多 App 的详细评测
- 特定场景的配置教程
- 疑难问题的解决方案

---

**最后更新：** 2024年

**维护者：** Vae-Scrooge

**许可证：** MIT
