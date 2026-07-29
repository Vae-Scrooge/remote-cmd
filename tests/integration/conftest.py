"""集成测试 - Mock SSH Server

使用 paramiko.ServerInterface 启动本地 SSH 服务器，
无需 Docker/testcontainers 即可测试完整 SSH 连接流程。
"""

import socket
import threading

import paramiko
import pytest

from remote_cmd.core.host import Host


class MockSSHServerInterface(paramiko.ServerInterface):
    """模拟 SSH 服务器，接受所有认证"""

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username: str) -> str:
        return "password,publickey"

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        return True


@pytest.fixture(scope="session")
def rsa_key() -> paramiko.RSAKey:
    """生成临时 RSA 密钥对"""
    return paramiko.RSAKey.generate(2048)


@pytest.fixture(scope="session")
def mock_ssh_server(unused_tcp_port_factory, rsa_key: paramiko.RSAKey):
    """启动一个真实的 Mock SSH Server 线程"""
    port = unused_tcp_port_factory()
    host_key = rsa_key
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(5)

    stop_event = threading.Event()

    def server_loop():
        while not stop_event.is_set():
            sock.settimeout(1.0)
            try:
                client, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = paramiko.Transport(client)
            t.add_server_key(host_key)
            server = MockSSHServerInterface()
            try:
                t.start_server(server=server)
            except (EOFError, paramiko.SSHException):
                pass
            # NOTE: t.close() 不能在此处调用，否则会重置连接

    thread = threading.Thread(target=server_loop, daemon=True)
    thread.start()
    yield port
    stop_event.set()
    sock.close()


@pytest.fixture
def integration_host(mock_ssh_server: int) -> Host:
    """提供可直接连接 mock 服务器的 Host 对象"""
    return Host(
        name="integration-test-host",
        hostname="127.0.0.1",
        username="testuser",
        port=mock_ssh_server,
        password="testpass",
    )
