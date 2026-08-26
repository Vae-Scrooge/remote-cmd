"""重试策略（service/retry_policy.py）单元测试

覆盖：
- is_retryable 的完整分类契约（永久性 / 瞬态 / BaseException）
- compute_backoff_delay 的指数增长、上限、full jitter 边界与确定性
"""

from __future__ import annotations

import asyncio
import random

import pytest

from remote_cmd.service.retry_policy import (
    DEFAULT_MAX_BACKOFF,
    PERMANENT_ERRORS,
    compute_backoff_delay,
    is_retryable,
)
from remote_cmd.utils.crypto import CredentialEncryptionError
from remote_cmd.utils.exceptions import (
    ConfigError,
    ConfigurationError,
    CredentialError,
    SSHAuthenticationError,
    SSHCommandError,
    SSHCommandTimeoutError,
    SSHConnectionError,
    SSHFileTransferError,
    SSHTimeoutError,
    ValidationError,
)


class TestIsRetryablePermanent:
    """永久性错误：绝不重试"""

    @pytest.mark.parametrize(
        "exc",
        [
            SSHAuthenticationError("denied"),
            CredentialError("decrypt failed"),
            CredentialEncryptionError("bad token"),
            ConfigError("bad config"),
            ValidationError("bad input"),
            ValueError("port out of range"),
            TypeError("wrong type"),
            KeyError("host not found"),
            RuntimeError("pool closed"),
        ],
    )
    def test_permanent_errors_not_retryable(self, exc):
        assert is_retryable(exc) is False

    def test_configuration_error_alias_not_retryable(self):
        # ConfigurationError 是 ConfigError 的别名
        assert ConfigurationError is ConfigError
        assert is_retryable(ConfigurationError("bad")) is False

    def test_base_exception_not_retryable(self):
        assert is_retryable(KeyboardInterrupt()) is False
        assert is_retryable(SystemExit(1)) is False
        assert is_retryable(asyncio.CancelledError()) is False


class TestIsRetryableTransient:
    """瞬态错误：可重试"""

    @pytest.mark.parametrize(
        "exc",
        [
            SSHTimeoutError("connect timeout"),
            SSHCommandTimeoutError("command timeout"),
            SSHConnectionError("connection reset"),
            SSHCommandError("channel broken"),
            SSHFileTransferError("transfer interrupted"),
            OSError("network unreachable"),
            Exception("connection reset"),  # 未识别异常保持历史可重试行为
        ],
    )
    def test_transient_errors_retryable(self, exc):
        assert is_retryable(exc) is True

    def test_permanent_errors_tuple_contents(self):
        # 契约快照：防止集合被意外改动
        assert SSHAuthenticationError in PERMANENT_ERRORS
        assert CredentialError in PERMANENT_ERRORS
        assert ConfigError in PERMANENT_ERRORS
        assert ValidationError in PERMANENT_ERRORS


class TestComputeBackoffDelay:
    """指数退避 + full jitter 计算"""

    def test_no_jitter_exponential_growth(self):
        assert compute_backoff_delay(0, base_delay=1.0, jitter=False) == 1.0
        assert compute_backoff_delay(1, base_delay=1.0, jitter=False) == 2.0
        assert compute_backoff_delay(2, base_delay=1.0, jitter=False) == 4.0
        assert compute_backoff_delay(3, base_delay=1.0, jitter=False) == 8.0

    def test_no_jitter_respects_base(self):
        assert compute_backoff_delay(4, base_delay=0.5, jitter=False) == 8.0

    def test_capped_at_max_delay(self):
        assert compute_backoff_delay(10, base_delay=1.0, max_delay=5.0, jitter=False) == 5.0
        assert compute_backoff_delay(100, base_delay=1.0, jitter=False) == DEFAULT_MAX_BACKOFF

    def test_zero_base_delay_is_zero(self):
        assert compute_backoff_delay(3, base_delay=0.0, jitter=False) == 0.0
        rng = random.Random(42)
        assert compute_backoff_delay(3, base_delay=0.0, rng=rng) == 0.0

    def test_full_jitter_bounds(self):
        rng = random.Random(1234)
        for attempt in range(6):
            capped = min(DEFAULT_MAX_BACKOFF, 1.0 * (2**attempt))
            for _ in range(20):
                delay = compute_backoff_delay(attempt, base_delay=1.0, rng=rng)
                assert 0.0 <= delay < capped or delay == 0.0
                assert delay <= capped

    def test_deterministic_with_seeded_rng(self):
        a = compute_backoff_delay(2, base_delay=1.0, rng=random.Random(42))
        b = compute_backoff_delay(2, base_delay=1.0, rng=random.Random(42))
        assert a == b

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"attempt": -1, "base_delay": 1.0},
            {"attempt": 0, "base_delay": -0.1},
            {"attempt": 0, "base_delay": 1.0, "max_delay": -1},
        ],
    )
    def test_invalid_args_raise(self, kwargs):
        with pytest.raises(ValueError):
            compute_backoff_delay(**kwargs)
