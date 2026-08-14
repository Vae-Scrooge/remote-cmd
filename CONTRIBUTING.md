<p align="center">
  <img src="https://img.shields.io/badge/English-blue?style=flat-square" alt="English"> ·
  <a href="./CONTRIBUTING.zh-CN.md"><img src="https://img.shields.io/badge/中文-gray?style=flat-square" alt="中文"></a>
</p>

# Contributing

Thank you for your interest in Remote CMD! We welcome all kinds of contributions, whether it is a bug report, a feature suggestion, a code contribution, or a documentation improvement.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How to Contribute](#how-to-contribute)
- [Setting Up the Development Environment](#setting-up-the-development-environment)
- [Code Standards](#code-standards)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

---

## Code of Conduct

By participating in this project you agree to abide by the following guidelines:

- Respect all participants and keep a friendly, constructive attitude
- Welcome newcomers and patiently answer their questions
- Stay focused on technical discussion and avoid personal attacks
- Accept differing viewpoints and experiences

## How to Contribute

### 1. Report a Bug

If you find a bug, please report it through [GitHub Issues](https://github.com/Vae-Scrooge/remote-cmd/issues).

**When reporting a bug, please include:**

- A clear title
- A detailed description
- Steps to reproduce
- Expected behavior vs. actual behavior
- Environment information (operating system, Python version, etc.)
- Error logs or screenshots

**Bug report template:**

```markdown
**Description**
Briefly describe the bug.

**Steps to Reproduce**
1. Run '...'
2. Enter '...'
3. See the error

**Expected Behavior**
Describe what should happen.

**Actual Behavior**
Describe what actually happened.

**Environment**
- OS: [e.g. Windows 10, Ubuntu 20.04]
- Python: [e.g. 3.9.0]
- Version: [e.g. 1.0.0]

**Error Log**
```python
paste error log here
```
```

### 2. Suggest a Feature

Have a feature suggestion? Please submit it through GitHub Issues using the `enhancement` label.

**Feature suggestion template:**

```markdown
**Description**
Briefly describe the feature you want.

**Use Case**
Describe the scenario in which this feature is useful.

**Expected Behavior**
Describe how the feature should work.

**Alternatives**
Other solutions you have considered.

**Additional Information**
Screenshots, examples, or other relevant information.
```

### 3. Improve Documentation

Documentation improvements are just as important! You can:

- Fix typos or grammatical errors
- Improve example code
- Add more usage scenarios
- Translate documentation

### 4. Submit Code

See the [Pull Request Process](#pull-request-process) below.

---

## Setting Up the Development Environment

### Prerequisites

- Python 3.9
- Git
- (Optional) A virtual environment tool

### Setup Steps

```bash
# 1. Fork the repository
# Click the Fork button on GitHub

# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/remote-cmd.git
cd remote-cmd

# 3. Add the upstream repository
git remote add upstream https://github.com/Vae-Scrooge/remote-cmd.git

# 4. Create a virtual environment
python -m venv venv

# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 5. Install development dependencies
pip install -e ".[dev]"

# 6. Verify the installation
pytest tests/ -v
```

### Syncing with Upstream

```bash
# Fetch upstream updates
git fetch upstream

# Switch to the main branch
git checkout main

# Merge upstream changes
git merge upstream/main

# Push to your fork
git push origin main
```

---

## Code Standards

### Python Code Style

We use the following tools to keep the code style consistent:

#### 1. Ruff - Formatting and Linting

```bash
# Format code
ruff format remote_cmd/ tests/

# Check formatting
ruff format --check remote_cmd/ tests/

# Lint
ruff check remote_cmd/ tests/
```

#### 4. MyPy - Type Checking

```bash
# Type check
mypy remote_cmd/
```

### Code Standards Highlights

#### Naming Conventions

```python
# Modules: lowercase, underscore-separated
my_module.py

# Classes: CamelCase
class MyClass:
    pass

# Functions: lowercase, underscore-separated
def my_function():
    pass

# Constants: all uppercase
MAX_CONNECTIONS = 100

# Private variables: underscore prefix
_private_var = 10
```

#### Docstrings

All public APIs require docstrings:

```python
def execute_command(
    self,
    command: str,
    timeout: Optional[int] = None
) -> CommandResult:
    """
    Execute a command on a remote server.

    Args:
        command: The command string to execute
        timeout: Timeout in seconds, defaults to no timeout

    Returns:
        A CommandResult object containing stdout, stderr, and the exit code

    Raises:
        SSHConnectionError: When the SSH connection fails
        SSHCommandError: When the command execution fails
        TimeoutError: When the command execution times out

    Example:
        >>> result = client.execute("ls -la")
        >>> if result.success:
        ...     print(result.stdout)
        ... else:
        ...     print(result.stderr)
    """
    pass
```

#### Type Annotations

Use type annotations to improve readability:

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

## Commit Conventions

### Commit Message Format

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type Reference

| Type | Description |
|------|-------------|
| `feat` | A new feature |
| `fix` | A bug fix |
| `docs` | Documentation updates |
| `style` | Code formatting (no functional change) |
| `refactor` | A code refactor |
| `perf` | A performance improvement |
| `test` | Test changes |
| `chore` | Build process or auxiliary tool changes |

### Examples

```bash
# New feature
feat(ssh): add support for SSH agent authentication

# Bug fix
fix(core): fix connection timeout handling

# Docs
docs(readme): add installation instructions for Windows

# Refactor
refactor(host_manager): simplify host validation logic

# Test
test(ssh_client): add tests for file transfer
```

---

## Pull Request Process

### 1. Create a Branch

```bash
# Start from the latest main
git checkout main
git pull upstream main

# Create a feature branch
git checkout -b feat/my-new-feature

# Or a fix branch
git checkout -b fix/bug-description
```

**Branch naming conventions:**

- `feat/` - New feature
- `fix/` - Bug fix
- `docs/` - Documentation
- `refactor/` - Code refactor
- `test/` - Test changes

### 2. Develop and Commit

```bash
# Develop your feature
# ...

# Stage and commit your changes
git add .
git commit -m "feat(scope): description"

# Keep in sync with upstream
git fetch upstream
git rebase upstream/main
```

### 3. Ensure Code Quality

Before submitting a PR, please make sure:

```bash
# 1. Format and lint
ruff format remote_cmd/ tests/
ruff check remote_cmd/ tests/

# 2. Type check
mypy remote_cmd/

# 3. Run tests
pytest tests/ -v --cov=remote_cmd

# 4. Make sure all tests pass
# Coverage should not fall below 80%
```

### 4. Push the Branch

```bash
git push origin feat/my-new-feature
```

### 5. Create a Pull Request

1. Visit your fork page
2. Click "Compare & pull request"
3. Fill in the PR description:

**PR description template:**

```markdown
## Description
Briefly describe what this PR does.

## Type
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Code refactor
- [ ] Performance improvement
- [ ] Tests

## Checklist
- [ ] Code follows the project style guidelines
- [ ] All tests pass
- [ ] Necessary tests were added
- [ ] Related documentation was updated
- [ ] Code was self-tested

## Related Issues
Fixes #(issue number)
Closes #(issue number)

## Screenshots (optional)
```

### 6. Code Review

- Maintainers will review your code
- Make changes based on feedback
- Pass all CI checks

### 7. Merge

Once the review passes, a maintainer will merge your PR.

---

## Reporting Issues

### Security Vulnerabilities

If you find a security vulnerability, **do not** report it in a public issue. Please email us at:

📧 `scroogevae@gmail.com`

We will handle it as soon as possible.

### General Issues

For usage questions, please first:

1. Check the [documentation](./README.md)
2. Search [Issues](https://github.com/Vae-Scrooge/remote-cmd/issues)
3. Check the [troubleshooting guide](docs/TROUBLESHOOTING.md)

If you still have questions, feel free to create an issue or use GitHub Discussions.

---

## Development Tips

### Debugging

```python
# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Use breakpoints
import pdb; pdb.set_trace()
```

### Common Commands

```bash
# Run a single test
pytest tests/test_ssh_client.py::TestSSHClient::test_connect -v

# View the coverage report
pytest --cov=remote_cmd --cov-report=html

# Auto-format
ruff format remote_cmd/ && ruff check --select I remote_cmd/
```

---

## Contributors

Thanks to everyone who has contributed to this project!

<a href="https://github.com/Vae-Scrooge/remote-cmd/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Vae-Scrooge/remote-cmd" />
</a>

---

## License

By contributing, you agree that your contributions will be released under the [MIT License](LICENSE).

---

**Thank you again for your contribution!** 🎉
