"""
配置管理模块

提供应用程序配置的加载、保存和默认值管理功能。
支持 YAML 和 JSON 两种配置文件格式。

配置文件搜索顺序：
1. 当前目录下的 config.yaml / config.json
2. 用户主目录下的 ~/.remote_cmd/config.yaml
3. 使用默认配置

Author: Vae-Scrooge
"""

import json
from pathlib import Path
from typing import Any

import yaml

# ============================================================================
# 配置路径管理
# ============================================================================


def get_default_config_path() -> str:
    """
    获取默认配置文件路径

    按以下顺序搜索配置文件：
    1. 当前工作目录下的 config.yaml
    2. 当前工作目录下的 config.json
    3. 用户主目录下的 ~/.remote_cmd/config.yaml

    Returns:
        str: 配置文件路径。如果以上路径都不存在，返回 "config.yaml"

    Example:
        >>> config_path = get_default_config_path()
        >>> print(f"使用配置文件: {config_path}")
    """
    # 检查当前目录下的配置文件
    if Path("config.yaml").exists():
        return "config.yaml"
    elif Path("config.json").exists():
        return "config.json"

    # 检查用户主目录下的配置文件
    home_config = Path.home() / ".remote_cmd" / "config.yaml"
    if home_config.exists():
        return str(home_config)

    # 默认返回当前目录下的 config.yaml
    return "config.yaml"


# ============================================================================
# 配置加载和保存
# ============================================================================


def load_config(config_path: str) -> dict[str, Any]:
    """
    从文件加载配置

    支持 YAML 和 JSON 格式，根据文件扩展名自动识别。

    Args:
        config_path: 配置文件路径

    Returns:
        Dict[str, Any]: 配置字典。如果文件不存在，返回默认配置。

    Raises:
        ValueError: 不支持的配置文件格式

    Example:
        >>> config = load_config("config.yaml")
        >>> print(config["hosts_file"])
    """
    path = Path(config_path)

    # 如果文件不存在，返回默认配置
    if not path.exists():
        return get_default_config()

    # 根据文件扩展名选择解析方式
    with open(path, encoding="utf-8") as f:
        if path.suffix in [".yaml", ".yml"]:
            return yaml.safe_load(f) or {}
        elif path.suffix == ".json":
            return json.load(f)
        else:
            raise ValueError(f"unsupported config file format: {path.suffix}")


def save_config(config: dict[str, Any], config_path: str) -> None:
    """
    保存配置到文件

    支持 YAML 和 JSON 格式，根据文件扩展名自动选择。

    Args:
        config: 配置字典
        config_path: 目标文件路径

    Raises:
        ValueError: 不支持的配置文件格式

    Note:
        如果目标目录不存在，将自动创建

    Example:
        >>> config = {"hosts_file": "my_hosts.json"}
        >>> save_config(config, "config.yaml")
    """
    path = Path(config_path)

    # 确保目录存在
    path.parent.mkdir(parents=True, exist_ok=True)

    # 根据文件扩展名选择保存格式
    with open(path, "w", encoding="utf-8") as f:
        if path.suffix in [".yaml", ".yml"]:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        elif path.suffix == ".json":
            json.dump(config, f, indent=2, ensure_ascii=False)
        else:
            raise ValueError(f"unsupported config file format: {path.suffix}")


# ============================================================================
# 默认配置
# ============================================================================


def get_default_config() -> dict[str, Any]:
    """
    获取默认配置

    Returns:
        Dict[str, Any]: 默认配置字典

    默认配置项：
        - hosts_file: 主机配置文件路径（默认：hosts.json）
        - default_ssh_port: 默认 SSH 端口（默认：22）
        - default_timeout: 默认连接超时时间（默认：30 秒）
        - log_level: 日志级别（默认：INFO）

    Example:
        >>> config = get_default_config()
        >>> print(config)
        {'hosts_file': 'hosts.json', 'default_ssh_port': 22, ...}
    """
    return {
        "hosts_file": "hosts.json",
        "default_ssh_port": 22,
        "default_timeout": 30,
        "log_level": "INFO",
    }


# ============================================================================
# 配置验证（可选功能）
# ============================================================================


def validate_config(config: dict[str, Any]) -> bool:
    """
    验证配置的有效性

    Args:
        config: 配置字典

    Returns:
        bool: 配置有效返回 True，否则返回 False

    Note:
        这是一个可选的验证函数，可用于检查用户提供的配置是否合法

    Example:
        >>> config = {"default_ssh_port": 22}
        >>> if validate_config(config):
        ...     print("配置有效")
    """
    # 验证端口号范围
    if "default_ssh_port" in config:
        port = config["default_ssh_port"]
        if not isinstance(port, int) or not (1 <= port <= 65535):
            return False

    # 验证超时时间
    if "default_timeout" in config:
        timeout = config["default_timeout"]
        if not isinstance(timeout, int) or timeout <= 0:
            return False

    # 验证日志级别
    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    return not ("log_level" in config and config["log_level"].upper() not in valid_log_levels)
