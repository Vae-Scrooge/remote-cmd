<p align="center">
  <img src="https://img.shields.io/badge/English-blue?style=flat-square" alt="English"> ·
  <a href="./SECURITY.zh-CN.md"><img src="https://img.shields.io/badge/中文-gray?style=flat-square" alt="中文"></a>
</p>

# Security Policy

## Supported Versions

Remote CMD is **Stable**. Security vulnerabilities in the latest released version are actively fixed.

| Version | Support Status |
|---------|----------------|
| Latest release | ✅ Actively maintained |
| Earlier versions | ⚠️ Critical security fixes only |

## Reporting a Vulnerability

We take the security of Remote CMD very seriously. If you discover a security vulnerability, **please do NOT** report it in a public Issue or any public channel.

### Reporting Process

1. **Send an email**
   - Address: `scroogevae@gmail.com`
   - Subject prefix: `[SECURITY]`

2. **The email should include:**
   - Vulnerability type (e.g., authentication bypass, command injection, sensitive information disclosure, etc.)
   - Affected versions
   - Detailed reproduction steps (attach a PoC if possible)
   - Possible fix suggestions
   - Your contact information (optional)

3. **Response time**
   - Acknowledgment: within 24 hours
   - Initial assessment: within 72 hours
   - Fix plan: within 1 week

### Responsible Disclosure

- Please give us reasonable time to fix the issue
- Do not disclose publicly before a fix is released
- After the fix is released, we will credit reporters in the [CHANGELOG.md](./CHANGELOG.md) (with consent)

## Security Best Practices

### 1. SSH Key Management
- Prefer SSH keys over passwords
- Set private key file permissions to `600`
- Rotate keys periodically
- You may use a key management tool (e.g., `ssh-agent`)

### 2. Configuration File Protection
- Do not commit configuration files to version control
- Use `.gitignore` to exclude sensitive files (defaults ignore `*.yaml`, `*.json`, etc.)
- Set configuration file permissions to `600`
- Sensitive information (such as passwords) should preferably be injected via environment variables

### 3. Principle of Least Privilege
- Use accounts with the minimum necessary privileges
- Avoid using the root account directly
- Execute privileged commands via `sudo`
- Log privileged operations

### 4. Network Security
- Use a firewall to restrict SSH access sources
- Consider using a VPN or a jump host
- Enable SSH audit logging
- Monitor unusual connections

## Known Security Issues

There are currently no known public security vulnerabilities.

## Security Update Release Channels

- [GitHub Security Advisories](https://github.com/Vae-Scrooge/remote-cmd/security/advisories)
- [CHANGELOG.md](./CHANGELOG.md)

## Acknowledgements

We thank all researchers and users who help improve the security of Remote CMD.
