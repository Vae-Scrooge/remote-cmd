# Development Guide

This document is for developers who want to contribute to Remote CMD or extend its functionality.

## Table of Contents

- [Setting Up the Dev Environment](#setting-up-the-dev-environment)
- [Project Structure](#project-structure)
- [Code Standards](#code-standards)
- [Testing](#testing)
- [Debugging Tips](#debugging-tips)
- [Release Process](#release-process)

---

## Setting Up the Dev Environment

### 1. Clone the Repository

```bash
git clone https://github.com/Vae-Scrooge/remote-cmd.git
cd remote-cmd
```

### 2. Create a Virtual Environment

```bash
# Create the virtual environment
python -m venv venv

# Activate the virtual environment
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

### 3. Install Dev Dependencies

```bash
# Install in development mode
pip install -e ".[dev]"

# Or install manually
pip install -r requirements.txt
pip install pytest pytest-cov ruff mypy
```

### 4. Verify the Environment

```bash
# Run the tests
pytest tests/ -v

# Check code style
ruff format --check remote_cmd/ tests/
ruff check remote_cmd/ tests/

# Type checking
mypy remote_cmd/
```

---

## Project Structure

```
remote-cmd/
├── remote_cmd/                 # Main code package
│   ├── __init__.py            # Package init
│   ├── core/                  # Core functionality
│   │   ├── __init__.py
│   │   ├── ssh_client.py      # SSH client
│   │   └── host.py            # Host data model
│   ├── repository/            # Storage repositories
│   │   ├── host_repository.py # Repository abstract interface
│   │   ├── json_host_repository.py
│   │   └── sqlite_host_repository.py
│   ├── service/               # Business services
│   │   ├── host_service.py    # Host service
│   │   ├── batch_executor.py
│   │   ├── async_batch_executor.py
│   │   ├── storage_factory.py
│   │   ├── credential_provider.py
│   │   └── ssh_service.py
│   ├── cli/                   # Command-line interface
│   │   ├── __init__.py
│   │   └── main.py            # CLI entry point
│   └── utils/                 # Utility modules
│       ├── __init__.py
│       ├── config.py          # Config management
│       └── exceptions.py      # Exception definitions
├── tests/                      # Test code
│   ├── test_ssh_client.py
│   ├── test_host_service.py
│   ├── test_repository.py
│   ├── test_sqlite_repository.py
│   └── test_storage_factory.py
├── examples/                   # Example code
│   └── basic_usage.py
├── docs/                       # Documentation
│   ├── architecture.md
│   ├── tutorial-quickstart.md
│   └── tutorial-advanced.md
├── .github/                    # GitHub config
│   └── workflows/
│       └── ci.yml              # CI config
├── README.md                   # Project description
├── CONTRIBUTING.md             # Contributing guide
├── CHANGELOG.md               # Changelog
├── LICENSE                    # License
├── pyproject.toml             # Install and packaging config
├── requirements.txt           # Dependencies
└── config.example.yaml        # Example config
```

### Module Descriptions

#### Core Module

**ssh_client.py**

- `SSHClient` class: manages SSH connections
- `ConnectionConfig` class: connection config
- `CommandResult` class: command execution result

**host.py**

- `Host` class: host configuration dataclass

**host_service.py**

- `HostService` class: host business logic (CRUD, credential resolution, connection tests)

#### Repository Module

**host_repository.py**

- `HostRepository` abstract interface: defines the host persistence contract

**json_host_repository.py**

- `JsonHostRepository` class: JSON file storage (atomic write, optional encryption)

**sqlite_host_repository.py**

- `SqliteHostRepository` class: SQLite database storage (indexing, pagination, search)

**storage_factory.py**

- `build_repository` function: auto-selects the storage engine by extension/explicit config

#### CLI Module

**main.py**

- Builds the command-line interface with the Click framework
- Defines command groups and subcommands
- Handles argument parsing and validation

#### Utils Module

**config.py**

- Loads and saves config files
- Supports YAML and JSON formats

**exceptions.py**

- Defines the custom exception hierarchy
- Unified error handling

---

## Code Standards

### Python Code Style

We use the following tools to keep code style consistent:

#### Ruff - Code Formatting

```bash
# Format code
ruff format remote_cmd/ tests/

# Check code format
black --check remote_cmd/ tests/
```

#### Ruff - Import Sorting

```bash
# Sort imports
ruff check --select I remote_cmd/ tests/
```

#### Ruff - Linting

```bash
# Lint code
ruff check remote_cmd/ tests/ --max-line-length=100
```

#### MyPy - Type Checking

```bash
# Type check
mypy remote_cmd/
```

### Naming Conventions

```python
# Module names: lowercase, underscore-separated
my_module.py

# Class names: PascalCase
class MyClass:
    pass

# Function names: lowercase, underscore-separated
def my_function():
    pass

# Constants: all uppercase
MAX_CONNECTIONS = 100

# Private variables: underscore prefix
_private_var = 10
```

### Docstrings

All public APIs require docstrings:

```python
def execute_command(
    self,
    command: str,
    timeout: Optional[int] = None
) -> CommandResult:
    """
    Execute a command on the remote server.

    Args:
        command: The command string to execute
        timeout: Command execution timeout (seconds), no timeout by default

    Returns:
        A CommandResult object containing stdout, stderr, and exit code

    Raises:
        SSHConnectionError: When the SSH connection fails
        SSHCommandError: When command execution fails
        SSHCommandTimeoutError: When the command exceeds the wall-clock timeout

    Example:
        >>> result = client.execute("ls -la")
        >>> if result.success:
        ...     print(result.stdout)
        ... else:
        ...     print(result.stderr)
    """
    pass
```

### Type Annotations

Use type annotations to improve code readability:

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

## Testing

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_ssh_client.py -v

# Run a specific test class
pytest tests/test_ssh_client.py::TestSSHClient -v

# Run a specific test method
pytest tests/test_ssh_client.py::TestSSHClient::test_connect_with_password -v

# Generate a coverage report
pytest --cov=remote_cmd --cov-report=html
pytest --cov=remote_cmd --cov-report=term-missing
```

### Test Structure

```python
# test_example.py
import pytest
from unittest.mock import Mock, patch

class TestClassName:
    """Test class"""

    def test_method_name(self):
        """Test method"""
        # Arrange
        input_data = "test"

        # Act
        result = function_under_test(input_data)

        # Assert
        assert result == expected_value

    @patch('module.name')
    def test_with_mock(self, mock_obj):
        """Test using a Mock"""
        mock_obj.return_value = "mocked"
        result = function_under_test()
        assert result == "mocked"
```

### Mock Best Practices

```python
from unittest.mock import Mock, patch, MagicMock

# Mock Paramiko SSHClient
@patch('remote_cmd.core.ssh_client.paramiko.SSHClient')
def test_ssh_operations(self, mock_ssh_class):
    # Set up the Mock
    mock_ssh = MagicMock()
    mock_ssh_class.return_value = mock_ssh

    # Configure return values
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_ssh.get_transport.return_value = mock_transport

    # Run the test
    client = SSHClient(config)
    client.connect()

    # Verify
    mock_ssh.connect.assert_called_once()
    assert client.is_connected() is True
```

### Test Coverage

Target: core module coverage > 80%

```bash
# View coverage
pytest --cov=remote_cmd --cov-report=html

# Open the report
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows
```

---

## Debugging Tips

### Enable Debug Logging

```python
import logging

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Set the Remote CMD log level
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### Use Breakpoints

```python
import pdb

def some_function():
    client = SSHClient(config)
    pdb.set_trace()  # Set a breakpoint
    client.connect()
    result = client.execute("ls -la")
```

### IDE Debugging

**VS Code configuration:**

```json
// .vscode/launch.json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Debug",
            "type": "python",
            "request": "launch",
            "module": "remote_cmd.cli.main",
            "args": ["host", "list"],
            "console": "integratedTerminal"
        }
    ]
}
```

### Debugging Common Problems

**Connection issues:**

```python
# Inspect connection details
with SSHClient(config) as client:
    print(f"Connected: {client.is_connected()}")
    print(f"Transport: {client._client.get_transport()}")

    # Run a simple command to test
    result = client.execute("echo 'Connection test'")
    print(f"Result: {result}")
```

---

## Release Process

### Version Number Management

Follow [Semantic Versioning](https://semver.org/):

- MAJOR: incompatible API changes
- MINOR: backward-compatible functionality additions
- PATCH: backward-compatible bug fixes

### Release Steps

1. **Bump the version**

```python
# remote_cmd/__init__.py
__version__ = "1.1.0"

# pyproject.toml
version = "1.1.0"
```

2. **Update the CHANGELOG**

```markdown
## [1.1.0] - 2024-01-20

### Added
- Description of new feature

### Changed
- Description of change

### Fixed
- Description of bug fix
```

3. **Create a Git tag**

```bash
git add .
git commit -m "chore(release): bump version to 1.1.0"
git tag v1.1.0
git push origin main --tags
```

4. **Build the distribution**

```bash
# Install build tools
pip install build twine

# Build
python -m build

# Check
python -m twine check dist/*

# Test publishing to TestPyPI
python -m twine upload --repository testpypi dist/*

# Publish to PyPI
python -m twine upload dist/*
```

---

## Extension Development

### Adding a New Command

Add to `remote_cmd/cli/main.py`:

```python
@cli.command()
@click.argument("host_name")
@click.option("--option", "-o", help="Option description")
@click.pass_context
def new_command(ctx, host_name, option):
    """New command description"""
    service: HostService = ctx.obj["service"]

    try:
        with service.connect_to_host(host_name) as client:
            # Implement command logic
            result = client.execute("some command")
            click.echo(result.stdout)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
```

### Adding a New Feature to SSHClient

```python
# remote_cmd/core/ssh_client.py

class SSHClient:
    def new_feature(self, param: str) -> Result:
        """
        New feature description.

        Args:
            param: Parameter description

        Returns:
            Result: Result description

        Raises:
            SSHConnectionError: Connection error
        """
        if not self._client:
            raise SSHConnectionError("Not connected")

        # Implement the feature
        pass
```

---

## Common Commands

```bash
# Format code
ruff format remote_cmd/ tests/
ruff check --select I remote_cmd/ tests/

# Lint
ruff check remote_cmd/ tests/
mypy remote_cmd/

# Run tests
pytest tests/ -v
pytest tests/ -v --cov=remote_cmd

# Local install test
pip install -e .
remote-cmd --version

# Build the package
python -m build

# Clean build artifacts
rm -rf build/ dist/ *.egg-info
```

---

## Getting Help

- View the [API Documentation](./API.md)
- Read the [Architecture Document](./architecture.md)
- Refer to the [Contributing Guide](../CONTRIBUTING.md)
- Open an [Issue](https://github.com/Vae-Scrooge/remote-cmd/issues)

---

**Happy developing!** 🚀
