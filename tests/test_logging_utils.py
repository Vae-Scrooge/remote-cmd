"""统一日志配置与敏感数据脱敏测试"""

import logging

from remote_cmd.utils.logging_utils import (
    LoggerAdapter,
    SensitiveDataFilter,
    get_logger,
    redact_sensitive_data,
    setup_logging,
)


class TestRedactSensitiveData:
    """敏感数据脱敏函数测试"""

    def test_redacts_equals_style(self):
        """测试：password=xxx 形式脱敏"""
        assert redact_sensitive_data("password=secret123") == "password=[REDACTED]"

    def test_redacts_colon_style(self):
        """测试：password: xxx 形式脱敏"""
        assert redact_sensitive_data("password: secret123") == "password: [REDACTED]"

    def test_redacts_quoted_value(self):
        """测试：带引号的值脱敏且不泄漏明文"""
        result = redact_sensitive_data("passwd='abc'")
        assert "abc" not in result
        assert "[REDACTED]" in result

    def test_redacts_double_quoted_value(self):
        """测试：双引号包裹的值脱敏"""
        result = redact_sensitive_data('passwd="abc"')
        assert "abc" not in result
        assert "[REDACTED]" in result

    def test_redacts_secret_field(self):
        """测试：secret 字段脱敏"""
        assert redact_sensitive_data("secret=abc") == "secret=[REDACTED]"

    def test_redacts_api_key(self):
        """测试：api_key 与 api-key 形式脱敏"""
        assert redact_sensitive_data("api_key=xyz") == "api_key=[REDACTED]"
        assert redact_sensitive_data("api-key=xyz") == "api-key=[REDACTED]"

    def test_redacts_token_case_insensitive(self):
        """测试：大小写不敏感（Token=xxx）"""
        assert redact_sensitive_data("Token=abc") == "Token=[REDACTED]"

    def test_leaves_plain_message_unchanged(self):
        """测试：无敏感信息时原文不变"""
        msg = "连接主机 web-1 成功"
        assert redact_sensitive_data(msg) == msg

    def test_leaves_non_sensitive_value(self):
        """测试：非敏感键名不被替换"""
        assert redact_sensitive_data("user=admin") == "user=admin"

    def test_empty_string(self):
        """测试：空字符串返回空字符串"""
        assert redact_sensitive_data("") == ""


class TestSensitiveDataFilter:
    """敏感数据过滤器测试"""

    def test_filters_message(self):
        """测试：过滤记录消息中的密码"""
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "连接 password=hunter2", (), None
        )
        assert SensitiveDataFilter().filter(record) is True
        assert "password=[REDACTED]" in record.msg
        assert "hunter2" not in record.msg

    def test_filters_args(self):
        """测试：过滤 %s 参数中的敏感信息"""
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "认证 %s", ("password=abc",), None
        )
        assert SensitiveDataFilter().filter(record) is True
        assert record.args == ("password=[REDACTED]",)

    def test_keeps_non_string_args(self):
        """测试：非字符串参数原样保留"""
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "结果 %d", (42,), None)
        assert SensitiveDataFilter().filter(record) is True
        assert record.args == (42,)

    def test_keeps_empty_args(self):
        """测试：无参数时不改动"""
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "普通消息", (), None)
        assert SensitiveDataFilter().filter(record) is True
        assert record.msg == "普通消息"

    def test_non_string_msg_untouched(self):
        """测试：非字符串消息不处理"""
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "格式 %s", ("a",), None)
        record.msg = None
        assert SensitiveDataFilter().filter(record) is True
        assert record.msg is None


class TestRedactDict:
    """字典递归脱敏测试"""

    def test_redacts_top_level_sensitive(self):
        """测试：顶层敏感字段脱敏"""
        result = SensitiveDataFilter.redact_dict({"password": "x", "user": "admin"})
        assert result == {"password": "[REDACTED]", "user": "admin"}

    def test_redacts_nested_dict(self):
        """测试：嵌套字典中的敏感字段递归脱敏"""
        result = SensitiveDataFilter.redact_dict({"conn": {"password": "x", "port": 22}})
        assert result == {"conn": {"password": "[REDACTED]", "port": 22}}

    def test_key_case_insensitive(self):
        """测试：键名大小写不敏感"""
        assert SensitiveDataFilter.redact_dict({"PASSWORD": "x"}) == {"PASSWORD": "[REDACTED]"}

    def test_preserves_other_values(self):
        """测试：普通值原样保留"""
        data = {"name": "srv", "port": 22, "tags": ["a", "b"]}
        assert SensitiveDataFilter.redact_dict(data) == data

    def test_empty_dict(self):
        """测试：空字典返回空字典"""
        assert SensitiveDataFilter.redact_dict({}) == {}


class TestSetupLogging:
    """日志系统配置测试"""

    def test_sets_root_level(self):
        """测试：设置根日志器级别"""
        setup_logging(level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("remote_cmd").level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self):
        """测试：非法级别回退到 INFO"""
        setup_logging(level="VERBOSE")
        assert logging.getLogger().level == logging.INFO

    def test_console_handler_added(self):
        """测试：控制台处理器已添加且带脱敏过滤器"""
        setup_logging()
        handlers = logging.getLogger().handlers
        stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
        assert stream_handlers
        assert any(isinstance(f, SensitiveDataFilter) for f in stream_handlers[0].filters)

    def test_file_handler_added_with_rotation(self, tmp_path):
        """测试：文件处理器支持轮转"""
        log_file = tmp_path / "sub" / "app.log"
        setup_logging(log_file=str(log_file))
        assert log_file.parent.exists()
        handlers = logging.getLogger().handlers
        file_handlers = [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
        assert file_handlers
        assert file_handlers[0].maxBytes == 10 * 1024 * 1024
        assert file_handlers[0].backupCount == 5
        assert any(isinstance(f, SensitiveDataFilter) for f in file_handlers[0].filters)

    def test_structured_format(self):
        """测试：结构化格式使用 JSON 模板"""
        setup_logging(structured=True)
        handlers = logging.getLogger().handlers
        stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
        assert stream_handlers
        assert "message" in stream_handlers[0].formatter._fmt

    def test_verbose_format(self):
        """测试：verbose 格式使用非结构化模板"""
        setup_logging(verbose=True)
        handlers = logging.getLogger().handlers
        stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
        assert stream_handlers
        assert "{" not in stream_handlers[0].formatter._fmt

    def test_default_format(self):
        """测试：默认格式为非结构化模板"""
        setup_logging()
        handlers = logging.getLogger().handlers
        stream_handlers = [h for h in handlers if isinstance(h, logging.StreamHandler)]
        assert stream_handlers
        assert "{" not in stream_handlers[0].formatter._fmt

    def test_clears_existing_handlers(self):
        """测试：重复调用会清除旧处理器"""
        setup_logging()
        first = set(logging.getLogger().handlers)
        setup_logging()
        second = set(logging.getLogger().handlers)
        assert not (first & second)


class TestLoggerAdapter:
    """带上下文日志适配器测试"""

    def test_adds_context_prefix(self):
        """测试：消息带上下文前缀"""
        logger = logging.getLogger("test.adapter")
        adapter = LoggerAdapter(logger, {"host": "web-1"})
        msg, kwargs = adapter.process("连接成功", {})
        assert msg == "[host=web-1] 连接成功"
        assert kwargs == {}

    def test_multiple_context_keys(self):
        """测试：多个上下文键"""
        adapter = LoggerAdapter(logging.getLogger("t"), {"host": "h", "id": "1"})
        msg, _ = adapter.process("msg", {})
        assert "[host=h]" in msg and "[id=1]" in msg

    def test_empty_context(self):
        """测试：无上下文时消息不变"""
        adapter = LoggerAdapter(logging.getLogger("t"), {})
        msg, _ = adapter.process("msg", {})
        assert msg == "msg"


class TestGetLogger:
    """日志器快捷函数测试"""

    def test_returns_plain_logger_without_context(self):
        """测试：无上下文返回原生 Logger"""
        logger = get_logger("test.plain")
        assert isinstance(logger, logging.Logger)
        assert not isinstance(logger, LoggerAdapter)

    def test_returns_adapter_with_context(self):
        """测试：带上下文返回 LoggerAdapter"""
        logger = get_logger("test.ctx", host="web-1")
        assert isinstance(logger, LoggerAdapter)
