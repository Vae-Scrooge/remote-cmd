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
    PoolClosedError,
    RemoteCmdError,
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
            PoolClosedError("pool closed"),
        ],
    )
    def test_permanent_errors_not_retryable(self, exc):
        assert is_retryable(exc) is False

    def test_pool_closed_error_compat_hierarchy(self):
        """v2.2：PoolClosedError 可被既有 except RuntimeError 捕获，
        同时归入 RemoteCmdError 层级"""
        assert issubclass(PoolClosedError, RuntimeError)
        assert issubclass(PoolClosedError, RemoteCmdError)
        with pytest.raises(RuntimeError, match="connection pool is closed"):
            raise PoolClosedError("connection pool is closed")

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
            # v2.2：裸 RuntimeError 恢复 v2.0 可重试行为（如线程创建失败、
            # 自定义 client_factory 的瞬态错误）；池关闭由 PoolClosedError 承担
            RuntimeError("can't start new thread"),
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
        assert PoolClosedError in PERMANENT_ERRORS
        # v2.2 收窄：裸 RuntimeError 不再整体永久
        assert RuntimeError not in PERMANENT_ERRORS


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

    def test_integer_exponent_equivalence(self):
        """v2.10：``2.0**attempt`` 与整数 ``2**attempt`` 在实用范围内数值一致。

        背景：strict 迁移为规避 mypy 2.3 typeshed 把 int 幂判为 Any，
        改用浮点底数；此回归锁定数值等价（jitter=False 时逐值比较）。
        """
        for attempt in (0, 1, 10, 20):
            for base in (0.5, 1.0, 3.0):
                expected = min(DEFAULT_MAX_BACKOFF, base * float(2**attempt))
                got = compute_backoff_delay(attempt, base_delay=base, jitter=False)
                assert got == expected, (attempt, base, got, expected)

    def test_cap_boundary_equivalence(self):
        """跨过 cap 边界（2**attempt * base 低于/超过 max_delay）结果一致。"""
        for attempt, expected in ((1, 2.0), (2, 4.0), (3, 5.0), (4, 5.0)):
            got = compute_backoff_delay(
                attempt, base_delay=1.0, max_delay=5.0, jitter=False
            )
            assert got == expected, (attempt, got, expected)

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
