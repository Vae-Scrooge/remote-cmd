# 安全策略

<p align="center">
  <a href="./SECURITY.md"><img src="https://img.shields.io/badge/English-gray?style=flat-square" alt="English"></a> ·
  <img src="https://img.shields.io/badge/中文-blue?style=flat-square" alt="中文">
</p>

## 支持的版本

Remote CMD 目前处于 **稳定阶段**。对最新发布的版本积极修复安全漏洞。

| 版本 | 支持状态 |
|------|----------|
| 最新发布版 | ✅ 积极维护 |
| 更早版本 | ⚠️ 仅修复紧急安全漏洞 |

## 报告安全漏洞

我们非常重视 Remote CMD 的安全问题。如果发现安全漏洞，**请不要**在公共 Issue 或公开渠道中报告。

### 报告流程

1. **发送邮件**
   - 邮箱：`scroogevae@gmail.com`
   - 主题前缀：`[SECURITY]`

2. **邮件内容应包括：**
   - 漏洞类型（例如：认证绕过、命令注入、敏感信息泄露等）
   - 受影响版本
   - 详细复现步骤（如有，请附上 PoC）
   - 可能的修复建议
   - 您的联系方式（可选）

3. **响应时间**
   - 确认收到：24 小时内
   - 初步评估：72 小时内
   - 修复计划：1 周内

### 负责任地披露

- 请给予我们合理的时间来修复问题
- 在漏洞修复发布之前，请勿公开披露
- 修复完成后，我们会在 [CHANGELOG.md](./CHANGELOG.md) 中致谢报告者（经同意后）

## 安全最佳实践

### 1. SSH 密钥管理
- 优先使用 SSH 密钥而非密码
- 私钥文件权限设置为 `600`
- 定期轮换密钥
- 可使用密钥管理工具（如 `ssh-agent`）

### 2. 配置文件保护
- 不要将配置文件提交到版本控制
- 使用 `.gitignore` 排除敏感文件（默认会忽略 `*.yaml`、`*.json` 等）
- 配置文件权限设置为 `600`
- 敏感信息（如密码）优先通过环境变量注入

### 3. 最小权限原则
- 使用拥有最小必要权限的用户
- 避免直接使用 root 账户
- 通过 `sudo` 执行特权命令
- 记录特权操作

### 4. 网络安全
- 使用防火墙限制 SSH 访问来源
- 考虑使用 VPN 或跳板机
- 启用 SSH 审计日志
- 监控异常连接

## 已知安全问题

目前没有已知的公开安全漏洞。

## 安全更新发布渠道

- [GitHub Security Advisories](https://github.com/Vae-Scrooge/remote-cmd/security/advisories)
- [CHANGELOG.md](./CHANGELOG.md)

## 致谢

感谢所有关注并提升 Remote CMD 安全性的研究人员与用户。
