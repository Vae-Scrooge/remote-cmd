"""Host 数据模型测试（原 test_host_manager.py 中 Host 相关用例迁移）"""

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig


def test_host_dataclass_roundtrip_and_connection_config():
    host = Host(name="web-prod", hostname="192.0.2.10", username="admin", tags=["prod", "web"])
    # to_dict / from_dict round-trip
    d = host.to_dict()
    host_from = Host.from_dict(d)
    assert host == host_from
    # to_connection_config should return ConnectionConfig
    cfg = host.to_connection_config()
    assert isinstance(cfg, ConnectionConfig)
    assert cfg.hostname == "192.0.2.10"
    assert cfg.username == "admin"


def test_host_sanitized_dict_masks_secrets():
    """Regression: sanitized_dict must not leak password or private key path."""
    from remote_cmd.core.host import Host

    # plaintext password
    h1 = Host(
        name="h1",
        hostname="10.0.0.1",
        username="root",
        password="secret123",
        key_filename="/home/user/.ssh/id_rsa",
    )
    safe1 = h1.sanitized_dict()
    assert safe1["password"] == "***"
    assert safe1["key_filename"] == "id_rsa"
    # non-sensitive fields preserved
    assert safe1["name"] == "h1"
    assert safe1["hostname"] == "10.0.0.1"
    assert safe1["username"] == "root"

    # encrypted password (starts with $encrypted$)
    h2 = Host(
        name="h2",
        hostname="10.0.0.2",
        username="admin",
        password="$encrypted$gAAAAAB...",
        key_filename="/opt/keys/deploy_key",
    )
    safe2 = h2.sanitized_dict()
    assert safe2["password"] == "***encrypted***"
    assert safe2["key_filename"] == "deploy_key"

    # no password, no key
    h3 = Host(name="h3", hostname="10.0.0.3", username="user")
    safe3 = h3.sanitized_dict()
    assert safe3["password"] is None
    assert safe3["key_filename"] is None
