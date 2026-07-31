"""
统一日志配置模块

提供结构化日志配置、敏感数据脱敏、日志轮转等功能。

用法:
    >>> from remote_cmd.utils.logging_utils import setup_logging
    >>> setup_logging(level="INFO", log_file="remote_cmd.log")

安全:
    - 自动过滤日志中的密码字段（key: password, passwd, secret）
    - 错误消息中的密码使用 [REDACTED] 替换
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ============================================================================
# 敏感数据脱敏过滤器
# ============================================================================

SENSITIVE_FIELDS = {"password", "passwd", "secret", "token", "key"}
SENSITIVE_PATTERN = re.compile(
    r'(?:password|passwd|secret|api[_-]?key|token)\s*[=:]\s*["\']?([^"\'&\s,;]+)',
    re.IGNORECASE,
)


def redact_sensitive_data(message: str) -> str:
    """
    过滤日志消息中敏感信息

    替换密码等字段的值为 [REDACTED]。
    """

    def _redact(m: re.Match) -> str:
        text = m.group(0)
        if "=" in text:
            return f"{text.split('=')[0]}=[REDACTED]"
        return f"{text.split(':')[0]}: [REDACTED]"

    return SENSITIVE_PATTERN.sub(_redact, message)


class SensitiveDataFilter(logging.Filter):
    """
    日志过滤器：自动脱敏敏感数据

    重写 filter 方法，在日志记录发出前替换敏感字段。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_data(record.msg)
        if record.args:
            # 处理 %s 格式的参数
            cleaned_args = tuple(
                redact_sensitive_data(str(a)) if isinstance(a, str) else a for a in record.args
            )
            record.args = cleaned_args
        return True

    @staticmethod
    def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
        """递归脱敏字典中的敏感字段"""
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key.lower() in SENSITIVE_FIELDS:
                result[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = SensitiveDataFilter.redact_dict(value)
            else:
                result[key] = value
        return result


# ============================================================================
# 日志格式
# ============================================================================

DEFAULT_FORMAT = "[%(asctime)s] %(levelname)-8s %(name)s:%(lineno)d - %(message)s"
VERBOSE_FORMAT = "[%(asctime)s] %(levelname)-8s %(name)s:%(lineno)d - %(message)s"
STRUCTURED_FORMAT = (
    '{"time":"%(asctime)s","level":"%(levelname)s",'
    '"module":"%(name)s","line":%(lineno)d,'
    '"message":"%(message)s"}'
)


# ============================================================================
# 日志配置
# ============================================================================


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    structured: bool = False,
    verbose: bool = False,
) -> None:
    """
    配置统一日志系统

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        log_file: 日志文件路径（可选，不指定时只输出到控制台）
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的轮转文件数
        structured: 是否使用 JSON 结构化格式
        verbose: 是否启用详细格式
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有处理器
    root_logger.handlers.clear()

    # 创建格式化器
    if structured:
        formatter = logging.Formatter(STRUCTURED_FORMAT)
    elif verbose:
        formatter = logging.Formatter(VERBOSE_FORMAT)
    else:
        formatter = logging.Formatter(DEFAULT_FORMAT)

    # 添加敏感数据过滤器
    sensitive_filter = SensitiveDataFilter()

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # 文件输出（可选）
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)

    # 配置 remote_cmd 包日志
    logging.getLogger("remote_cmd").setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("remote_cmd").debug("日志系统已初始化")


class LoggerAdapter(logging.LoggerAdapter):
    """
    带上下文的日志适配器

    为日志消息自动添加上下文信息（如主机名、请求ID等）。

    用法:
        >>> log = LoggerAdapter(logger, {"host": "web-server"})
        >>> log.info("连接成功")
        # 输出: [host=web-server] 连接成功
    """

    def process(self, msg: str, kwargs: MutableMapping[str, Any]) -> tuple:
        extra = self.extra or {}
        ctx = " ".join(f"[{k}={v}]" for k, v in extra.items())
        return f"{ctx} {msg}" if ctx else msg, kwargs


# ============================================================================
# 快捷函数
# ============================================================================


def get_logger(name: str, **context) -> "logging.Logger | logging.LoggerAdapter":
    """
    获取带可选上下文的日志器

    用法:
        >>> log = get_logger(__name__, host="web-server")
        >>> log.info("测试连接")
    """
    logger = logging.getLogger(name)
    if context:
        return LoggerAdapter(logger, context)
    return logger
