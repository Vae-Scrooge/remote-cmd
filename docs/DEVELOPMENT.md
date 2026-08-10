# 开发指南

本文档面向希望参与 Remote CMD 开发或扩展其功能的开发者。

## 目录

- [开发环境搭建](#开发环境搭建)
- [项目结构](#项目结构)
- [代码规范](#代码规范)
- [测试](#测试)
- [调试技巧](#调试技巧)
- [发布流程](#发布流程)

---

## 开发环境搭建

### 1. 克隆仓库

```bash
git clone https://github.com/Vae-Scrooge/remote-cmd.git
cd remote-cmd
```

### 2. 创建虚拟环境

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

### 3. 安装开发依赖

```bash
# 安装开发模式
pip install -e ".[dev]"

# 或者手动安装
pip install -r requirements.txt
pip install pytest pytest-cov ruff mypy
```

### 4. 验证环境

```bash
# 运行测试
pytest tests/ -v

# 检查代码风格
ruff format --check remote_cmd/ tests/
ruff check remote_cmd/ tests/

# 类型检查
mypy remote_cmd/
```

---

## 项目结构

```
remote-cmd/
├── remote_cmd/                 # 主代码包
│   ├── __init__.py            # 包初始化
│   ├── core/                  # 核心功能
│   │   ├── __init__.py
│   │   ├── ssh_client.py      # SSH 客户端
│   │   └── host.py            # 主机数据模型
│   ├── repository/            # 存储仓库
│   │   ├── host_repository.py # 仓库抽象接口
│   │   ├── json_host_repository.py
│   │   └── sqlite_host_repository.py
│   ├── service/                   # 业务服务
│   │   ├── host_service.py    # 主机服务
│   │   ├── batch_executor.py
│   │   ├── async_batch_executor.py
│   │   ├── storage_factory.py
│   │   ├── credential_provider.py
│   │   └── ssh_service.py
│   ├── cli/                   # 命令行接口
│   │   ├── __init__.py
│   │   └── main.py            # CLI 入口
│   └── utils/                 # 工具模块
│       ├── __init__.py
│       ├── config.py          # 配置管理
│       └── exceptions.py      # 异常定义
├── tests/                      # 测试代码
│   ├── test_ssh_client.py
│   ├── test_host_service.py
│   ├── test_repository.py
│   ├── test_sqlite_repository.py
│   └── test_storage_factory.py
├── examples/                   # 示例代码
│   └── basic_usage.py
├── docs/                       # 文档
│   ├── architecture.md
│   ├── tutorial-quickstart.md
│   └── tutorial-advanced.md
├── .github/                    # GitHub 配置
│   └── workflows/
│       └── ci.yml              # CI 配置
├── README.md                   # 项目说明
├── CONTRIBUTING.md             # 贡献指南
├── CHANGELOG.md               # 更新日志
├── LICENSE                    # 许可证
├── pyproject.toml             # 安装与打包配置
├── requirements.txt           # 依赖列表
└── config.example.yaml        # 配置示例
```

### 模块说明

#### Core 模块

**ssh_client.py**
- `SSHClient` 类：管理 SSH 连接
- `ConnectionConfig` 类：连接配置
- `CommandResult` 类：命令执行结果

**host.py**
- `Host` 类：主机配置数据类

**host_service.py**
- `HostService` 类：主机业务逻辑（CRUD、凭据解析、连接测试）

#### Repository 模块

**host_repository.py**
- `HostRepository` 抽象接口：定义主机持久化契约

**json_host_repository.py**
- `JsonHostRepository` 类：JSON 文件存储（原子写入、可选加密）

**sqlite_host_repository.py**
- `SqliteHostRepository` 类：SQLite 数据库存储（索引、分页、搜索）

**storage_factory.py**
- `build_repository` 函数：按扩展名/显式配置自动选择存储引擎

#### CLI 模块

**main.py**
- 使用 Click 框架构建命令行界面
- 定义命令组和子命令
- 处理参数解析和验证

#### Utils 模块

**config.py**
- 配置文件的加载和保存
- 支持 YAML 和 JSON 格式

**exceptions.py**
- 定义自定义异常层次结构
- 统一的错误处理

---

## 代码规范

### Python 代码风格

我们使用以下工具保持代码风格一致：

#### Ruff - 代码格式化

```bash
# 格式化代码
ruff format remote_cmd/ tests/

# 检查代码格式
black --check remote_cmd/ tests/
```

#### Ruff - 导入排序检查

```bash
# 排序导入
ruff check --select I remote_cmd/ tests/
```

#### Ruff - 代码检查

```bash
# 检查代码
ruff check remote_cmd/ tests/ --max-line-length=100
```

#### MyPy - 类型检查

```bash
# 类型检查
mypy remote_cmd/
```

### 命名规范

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

### 文档字符串

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

### 类型注解

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

## 测试

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/test_ssh_client.py -v

# 运行特定测试类
pytest tests/test_ssh_client.py::TestSSHClient -v

# 运行特定测试方法
pytest tests/test_ssh_client.py::TestSSHClient::test_connect_with_password -v

# 生成覆盖率报告
pytest --cov=remote_cmd --cov-report=html
pytest --cov=remote_cmd --cov-report=term-missing
```

### 测试结构

```python
# test_example.py
import pytest
from unittest.mock import Mock, patch

class TestClassName:
    """测试类"""
    
    def test_method_name(self):
        """测试方法"""
        # Arrange
        input_data = "test"
        
        # Act
        result = function_under_test(input_data)
        
        # Assert
        assert result == expected_value
    
    @patch('module.name')
    def test_with_mock(self, mock_obj):
        """使用 Mock 的测试"""
        mock_obj.return_value = "mocked"
        result = function_under_test()
        assert result == "mocked"
```

### Mock 最佳实践

```python
from unittest.mock import Mock, patch, MagicMock

# Mock Paramiko SSHClient
@patch('remote_cmd.core.ssh_client.paramiko.SSHClient')
def test_ssh_operations(self, mock_ssh_class):
    # 设置 Mock
    mock_ssh = MagicMock()
    mock_ssh_class.return_value = mock_ssh
    
    # 配置返回值
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_ssh.get_transport.return_value = mock_transport
    
    # 执行测试
    client = SSHClient(config)
    client.connect()
    
    # 验证
    mock_ssh.connect.assert_called_once()
    assert client.is_connected() is True
```

### 测试覆盖率

目标：核心模块覆盖率 > 80%

```bash
# 查看覆盖率
pytest --cov=remote_cmd --cov-report=html

# 打开报告
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows
```

---

## 调试技巧

### 启用调试日志

```python
import logging

# 启用调试日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置 Remote CMD 日志级别
logger = logging.getLogger('remote_cmd')
logger.setLevel(logging.DEBUG)
```

### 使用断点

```python
import pdb

def some_function():
    client = SSHClient(config)
    pdb.set_trace()  # 设置断点
    client.connect()
    result = client.execute("ls -la")
```

### IDE 调试

**VS Code 配置：**

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

### 常见问题调试

**连接问题：**

```python
# 检查连接详情
with SSHClient(config) as client:
    print(f"Connected: {client.is_connected()}")
    print(f"Transport: {client._client.get_transport()}")
    
    # 执行简单命令测试
    result = client.execute("echo 'Connection test'")
    print(f"Result: {result}")
```

---

## 发布流程

### 版本号管理

遵循 [语义化版本](https://semver.org/lang/zh-CN/)：

- MAJOR：不兼容的 API 修改
- MINOR：向下兼容的功能新增
- PATCH：向下兼容的问题修复

### 发布步骤

1. **更新版本号**

```python
# remote_cmd/__init__.py
__version__ = "1.1.0"

# pyproject.toml
version = "1.1.0"
```

2. **更新 CHANGELOG**

```markdown
## [1.1.0] - 2024-01-20

### Added
- 新功能描述

### Changed
- 变更描述

### Fixed
- Bug 修复描述
```

3. **创建 Git 标签**

```bash
git add .
git commit -m "chore(release): bump version to 1.1.0"
git tag v1.1.0
git push origin main --tags
```

4. **构建分发包**

```bash
# 安装构建工具
pip install build twine

# 构建
python -m build

# 检查
python -m twine check dist/*

# 测试发布到 TestPyPI
python -m twine upload --repository testpypi dist/*

# 正式发布到 PyPI
python -m twine upload dist/*
```

---

## 扩展开发

### 添加新命令

在 `remote_cmd/cli/main.py` 中添加：

```python
@cli.command()
@click.argument("host_name")
@click.option("--option", "-o", help="选项说明")
@click.pass_context
def new_command(ctx, host_name, option):
    """新命令描述"""
    service: HostService = ctx.obj["service"]

    try:
        with service.connect_to_host(host_name) as client:
            # 实现命令逻辑
            result = client.execute("some command")
            click.echo(result.stdout)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
```

### 添加新功能到 SSHClient

```python
# remote_cmd/core/ssh_client.py

class SSHClient:
    def new_feature(self, param: str) -> Result:
        """
        新功能描述。
        
        Args:
            param: 参数说明
            
        Returns:
            Result: 结果说明
            
        Raises:
            SSHConnectionError: 连接错误
        """
        if not self._client:
            raise SSHConnectionError("Not connected")
        
        # 实现功能
        pass
```

---

## 常用命令

```bash
# 格式化代码
ruff format remote_cmd/ tests/
ruff check --select I remote_cmd/ tests/

# 代码检查
ruff check remote_cmd/ tests/
mypy remote_cmd/

# 运行测试
pytest tests/ -v
pytest tests/ -v --cov=remote_cmd

# 本地安装测试
pip install -e .
remote-cmd --version

# 构建包
python -m build

# 清理构建文件
rm -rf build/ dist/ *.egg-info
```

---

## 获取帮助

- 查看 [API 文档](./API.md)
- 阅读 [架构文档](./architecture.md)
- 参考 [贡献指南](../CONTRIBUTING.md)
- 提交 [Issue](https://github.com/Vae-Scrooge/remote-cmd/issues)

---

**祝你开发愉快！** 🚀
