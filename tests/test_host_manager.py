import json

import pytest

from remote_cmd.core.host_manager import Host, HostManager
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


def test_hostmanager_crud_and_magic_methods():
    m = HostManager()
    h1 = Host(name="srv1", hostname="10.0.0.1", username="root", tags=["prod"])
    h2 = Host(name="srv2", hostname="10.0.0.2", username="admin", port=2222, tags=["dev"])

    # add and duplicate detection
    m.add_host(h1)
    with pytest.raises(ValueError):
        m.add_host(h1)

    # list_tags before removal
    tags = m.list_tags()
    assert set(tags) == {"prod"}

    m.add_host(h2)
    assert len(m) == 2
    tags = m.list_tags()
    assert set(tags) == {"prod", "dev"}

    # get / remove / __contains__ / __len__
    assert m.get_host("srv1") == h1
    assert "srv1" in m
    m.remove_host("srv1")
    with pytest.raises(KeyError):
        m.get_host("srv1")
    assert len(m) == 1
    assert "srv1" not in m

    # list with/without tag filter
    listed_all = m.list_hosts()
    assert isinstance(listed_all, list)
    assert listed_all and all(isinstance(x, Host) for x in listed_all)
    listed_dev = m.list_hosts(tag="dev")
    assert listed_dev == [h2]
    # list_tags after removal
    tags = m.list_tags()
    assert set(tags) == {"dev"}


def test_persistence_save_and_load(tmp_path):
    m = HostManager()
    h1 = Host(name="srvA", hostname="203.0.113.1", username="root", tags=["alpha"])
    h2 = Host(name="srvB", hostname="203.0.113.2", username="admin", port=2222, tags=["beta"])
    m.add_host(h1)
    m.add_host(h2)

    path = tmp_path / "hosts.json"
    m.save_to_file(path)

    # verify JSON structure (versioned format)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert data["version"] == 2
    assert isinstance(data["hosts"], dict) and len(data["hosts"]) == 2
    assert "srvA" in data["hosts"] and "srvB" in data["hosts"]

    # load_from_file reconstructs hosts
    m2 = HostManager()
    m2.load_from_file(path)
    assert len(m2) == 2
    assert m2.get_host("srvA") == h1
    assert m2.get_host("srvB") == h2

    # missing file should be handled gracefully
    m3 = HostManager()
    missing = tmp_path / "does_not_exist.json"
    m3.load_from_file(missing)
    assert len(m3) == 0


def test_context_manager_usage():
    with HostManager() as mgr:
        h = Host(name="scoped", hostname="127.0.0.1", username="user", tags=[])
        mgr.add_host(h)
        assert len(mgr) == 1


def test_connection_methods_mock_sshclient(monkeypatch):
    class DummySSH:
        def __init__(self, config):
            self.config = config

        def connect(self):
            return self

        def is_connected(self):
            return True

        def __enter__(self):
            return self.connect()

        def __exit__(self, *args):
            pass

        def disconnect(self):
            pass

    def mock_connect(self, name):
        return DummySSH(self.get_host(name).to_connection_config())

    import remote_cmd.core.host_manager as hm

    monkeypatch.setattr(hm.HostManager, "connect_to_host", mock_connect)

    mgr = hm.HostManager()
    h = hm.Host(name="conn-host", hostname="192.0.2.5", username="root", tags=[])
    mgr.add_host(h)

    ok = mgr.test_connection("conn-host")
    assert ok is True
