"""连接池策略纯函数测试"""

from remote_cmd.service._pool_policy import (
    ConnectionMeta,
    idle_expired,
    lifetime_expired,
    should_close,
)


def _meta(created_at: float = 1000.0, last_used: float = 1000.0) -> ConnectionMeta:
    return ConnectionMeta(created_at=created_at, last_used=last_used, conn_id="abc")


class TestLifetimeExpired:
    def test_not_expired(self):
        assert lifetime_expired(1000.0, max_lifetime=3600, now=2000.0) is False

    def test_expired(self):
        assert lifetime_expired(1000.0, max_lifetime=3600, now=5000.0) is True

    def test_boundary_equal_not_expired(self):
        # now - created == max_lifetime 时严格 > 才判定过期
        assert lifetime_expired(1000.0, max_lifetime=100, now=1100.0) is False

    def test_default_now(self):
        # 默认 now=time.time()：刚创建的连接（created_at 用 now）必然未过期
        import time

        assert lifetime_expired(time.time(), max_lifetime=3600) is False


class TestIdleExpired:
    def test_not_idle(self):
        assert idle_expired(1000.0, idle_timeout=300, now=1100.0) is False

    def test_idle(self):
        assert idle_expired(1000.0, idle_timeout=300, now=1500.0) is True

    def test_boundary_equal_is_idle(self):
        # idle == idle_timeout 精确相等时视为超时（>= 语义）
        assert idle_expired(1000.0, idle_timeout=100, now=1100.0) is True

    def test_default_now(self):
        import time

        assert idle_expired(time.time(), idle_timeout=300) is False


class TestShouldClose:
    def test_meta_none_closes(self):
        assert should_close(None, 3600, 300, connected=True) is True

    def test_disconnected_closes(self):
        assert should_close(_meta(), 3600, 300, connected=False) is True

    def test_lifetime_expired_closes(self):
        meta = _meta(created_at=1000.0)
        assert should_close(meta, 3600, 300, connected=True, now=5000.0) is True

    def test_idle_expired_closes(self):
        meta = _meta(created_at=1000.0, last_used=1000.0)
        assert should_close(meta, 3600, 300, connected=True, now=1400.0) is True

    def test_healthy_keeps(self):
        meta = _meta(created_at=1000.0, last_used=1000.0)
        assert should_close(meta, 3600, 300, connected=True, now=1100.0) is False

    def test_default_now_healthy(self):
        import time

        meta = _meta(created_at=time.time(), last_used=time.time())
        assert should_close(meta, 3600, 300, connected=True) is False
