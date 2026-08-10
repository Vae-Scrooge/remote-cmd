# 贡献指南

感谢您对 Remote CMD 项目的关注！我们欢迎任何形式的贡献，无论是提交 Bug 报告、功能建议、代码贡献还是文档改进。

## 目录

- [行为准则](#行为准则)
- [如何贡献](#如何贡献)
- [开发环境搭建](#开发环境搭建)
- [代码规范](#代码规范)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)
- [报告问题](#报告问题)

---

## 行为准则

参与本项目即表示您同意遵守以下准则：

- 尊重所有参与者，保持友善和建设性的态度
- 欢迎新手，耐心解答问题
- 专注于技术讨论，避免人身攻击
- 接受不同的观点和经验

## 如何贡献

### 1. 报告 Bug

如果您发现了 Bug，请通过 [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues) 提交。

**提交 Bug 时请包含：**

- 清晰的标题
- 详细的描述
- 复现步骤
- 期望行为 vs 实际行为
- 环境信息（操作系统、Python 版本等）
- 错误日志或截图

**Bug 报告模板：**

```markdown
**问题描述**
简明扼要地描述 Bug

**复现步骤**
1. 执行 '...'
2. 输入 '...'
3. 出现错误

**期望行为**
描述应该发生什么

**实际行为**
描述实际发生了什么

**环境信息**
- OS: [例如 Windows 10, Ubuntu 20.04]
- Python: [例如 3.9.0]
- 版本: [例如 1.0.0]

**错误日志**
```
粘贴错误日志
```
```

### 2. 建议新功能

有新功能建议？请通过 GitHub Issues 提交，使用标签 `enhancement`。

**功能建议模板：**

```markdown
**功能描述**
简明描述您想要的功能

**使用场景**
描述这个功能在什么情况下有用

**期望行为**
描述该功能应该如何工作

**替代方案**
您考虑过的其他解决方案

**其他信息**
截图、示例或其他相关信息
```

### 3. 改进文档

文档改进同样重要！您可以：

- 修复拼写错误或语法错误
- 改进示例代码
- 添加更多使用场景
- 翻译文档

### 4. 提交代码

见下方的 [Pull Request 流程](#pull-request-流程)

---

## 开发环境搭建

### 前置要求

- Python 3.9
- Git
- （可选）虚拟环境工具

### 设置步骤

```bash
# 1. Fork 仓库
# 在 GitHub 上点击 Fork 按钮

# 2. 克隆您的 Fork
git clone https://github.com/YOUR_USERNAME/remote-cmd.git
cd remote-cmd

# 3. 添加上游仓库
git remote add upstream https://github.com/Vae-Scrooge/remote-cmd.git

# 4. 创建虚拟环境
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 5. 安装开发依赖
pip install -e ".[dev]"

# 6. 验证安装
pytest tests/ -v
```

### 同步上游更新

```bash
# 获取上游更新
git fetch upstream

# 切换到 main 分支
git checkout main

# 合并上游更新
git merge upstream/main

# 推送到您的 Fork
git push origin main
```

---

## 代码规范

### Python 代码风格

我们使用以下工具保持代码风格一致：

#### 1. Ruff - 代码格式化与 Lint

```bash
# 格式化代码
ruff format remote_cmd/ tests/

# 检查代码格式
ruff format --check remote_cmd/ tests/

# Lint 检查
ruff check remote_cmd/ tests/
```

#### 4. MyPy - 类型检查

```bash
# 类型检查
mypy remote_cmd/
```

### 代码规范要点

#### 命名规范

```python
# 模块名：小写，下划线分隔
my_module.py

# 类名：大驼峰
class MyClass:
    pass

# 函数名：小写，下划线分隔
def my_function():
    pass

# 常量：全大写
MAX_CONNECTIONS = 100

# 私有变量：下划线前缀
_private_var = 10
```

#### 文档字符串

所有公共 API 都需要文档字符串：

```python
def execute_command(
    self,
    command: str,
    timeout: Optional[int] = None
) -> CommandResult:
    """
    在远程服务器上执行命令。

    Args:
        command: 要执行的命令字符串
        timeout: 命令执行超时时间（秒），默认无超时

    Returns:
        CommandResult 对象，包含 stdout、stderr 和退出码

    Raises:
        SSHConnectionError: 当 SSH 连接失败时
        SSHCommandError: 当命令执行失败时
        TimeoutError: 当命令执行超时时

    Example:
        >>> result = client.execute("ls -la")
        >>> if result.success:
        ...     print(result.stdout)
        ... else:
        ...     print(result.stderr)
    """
    pass
```

#### 类型注解

使用类型注解提高代码可读性：

```python
from typing import Optional, List, Dict, Any

def process_hosts(
    hosts: List[Host],
    timeout: Optional[int] = None
) -> Dict[str, Any]:
    """Process a list of hosts."""
    results: Dict[str, Any] = {}
    for host in hosts:
        results[host.name] = process_single_host(host, timeout)
    return results
```

---

## 提交规范

### 提交信息格式

我们使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 类型说明

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档更新 |
| `style` | 代码格式（不影响代码功能）|
| `refactor` | 代码重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建过程或辅助工具的变动 |

### 示例

```bash
# 新功能
feat(ssh): add support for SSH agent authentication

# Bug 修复
fix(core): fix connection timeout handling

# 文档
docs(readme): add installation instructions for Windows

# 重构
refactor(host_manager): simplify host validation logic

# 测试
test(ssh_client): add tests for file transfer
```

---

## Pull Request 流程

### 1. 创建分支

```bash
# 从最新的 main 分支创建
git checkout main
git pull upstream main

# 创建功能分支
git checkout -b feat/my-new-feature

# 或修复分支
git checkout -b fix/bug-description
```

**分支命名规范：**

- `feat/` - 新功能
- `fix/` - Bug 修复
- `docs/` - 文档更新
- `refactor/` - 代码重构
- `test/` - 测试相关

### 2. 开发和提交

```bash
# 开发您的功能
# ...

# 提交更改
git add .
git commit -m "feat(scope): description"

# 保持与上游同步
git fetch upstream
git rebase upstream/main
```

### 3. 确保代码质量

在提交 PR 前，请确保：

```bash
# 1. 代码格式化与 Lint
ruff format remote_cmd/ tests/
ruff check remote_cmd/ tests/

# 2. 类型检查
mypy remote_cmd/

# 3. 运行测试
pytest tests/ -v --cov=remote_cmd

# 4. 确保所有测试通过
# 覆盖率不低于 80%
```

### 4. 推送分支

```bash
git push origin feat/my-new-feature
```

### 5. 创建 Pull Request

1. 访问您的 Fork 页面
2. 点击 "Compare & pull request"
3. 填写 PR 描述：

**PR 描述模板：**

```markdown
## 描述
简明描述这个 PR 做了什么

## 类型
- [ ] Bug 修复
- [ ] 新功能
- [ ] 文档更新
- [ ] 代码重构
- [ ] 性能优化
- [ ] 测试

## 检查清单
- [ ] 代码遵循项目风格指南
- [ ] 所有测试通过
- [ ] 添加了必要的测试
- [ ] 更新了相关文档
- [ ] 代码经过自测

## 相关 Issue
Fixes #(issue 编号)
Closes #(issue 编号)

## 截图（如有必要）
```

### 6. 代码审查

- 维护者会审查您的代码
- 根据反馈进行修改
- 通过所有 CI 检查

### 7. 合并

审查通过后，维护者会合并您的 PR。

---

## 报告问题

### 安全漏洞

如果您发现了安全漏洞，请**不要**在公共 Issue 中报告。请发送邮件到：

📧 `scroogevae@gmail.com`

我们会尽快处理。

### 一般问题

对于使用问题，请先：

1. 查看 [文档](./README.zh-CN.md)
2. 搜索 [Issues](https://github.com/Vae-Scrooge/remote-cmd/issues)
3. 查看 [故障排查指南](docs/TROUBLESHOOTING.md)

如果仍有问题，欢迎创建 Issue 或使用 GitHub Discussions。

---

## 开发提示

### 调试技巧

```python
# 启用调试日志
import logging
logging.basicConfig(level=logging.DEBUG)

# 使用断点
import pdb; pdb.set_trace()
```

### 常用命令

```bash
# 运行单个测试
pytest tests/test_ssh_client.py::TestSSHClient::test_connect -v

# 查看覆盖率报告
pytest --cov=remote_cmd --cov-report=html

# 自动格式化
ruff format remote_cmd/ && ruff check --select I remote_cmd/
```

---

## 贡献者

感谢所有为这个项目做出贡献的人！

<a href="https://github.com/Vae-Scrooge/remote-cmd/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Vae-Scrooge/remote-cmd" />
</a>

---

## 许可证

通过贡献代码，您同意您的贡献将在 [MIT License](LICENSE) 下发布。

---

**再次感谢您的贡献！** 🎉
