"""
示例：基本使用

展示如何使用 remote_cmd 进行基本的 SSH 操作
"""

from remote_cmd.core.host import Host
from remote_cmd.core.ssh_client import ConnectionConfig, SSHClient
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService


def example_basic_connection():
    """示例：基本 SSH 连接"""
    # 使用密码连接
    config = ConnectionConfig(
        hostname="192.168.1.100", username="admin", password="your_password", port=22
    )

    # 使用上下文管理器自动管理连接
    with SSHClient(config) as client:
        # 执行命令
        result = client.execute("ls -la")
        print(f"命令输出:\n{result.stdout}")
        print(f"退出码: {result.exit_code}")
        print(f"成功: {result.success}")


def example_key_auth():
    """示例：使用 SSH Key 连接"""
    config = ConnectionConfig(
        hostname="example.com", username="deploy", key_filename="~/.ssh/id_rsa", port=22
    )

    with SSHClient(config) as client:
        # 执行多个命令
        result = client.execute("whoami")
        print(f"当前用户: {result.stdout.strip()}")

        result = client.execute("uptime")
        print(f"系统运行时间: {result.stdout.strip()}")


def example_host_management():
    """示例：主机管理"""
    # 创建仓库与主机服务（Repository + Service 架构）
    repo = JsonHostRepository(filepath="my_hosts.json")
    manager = HostService(repository=repo)

    # 添加主机
    manager.add_host(
        Host(
            name="web-server",
            hostname="192.168.1.10",
            username="ubuntu",
            key_filename="~/.ssh/web_server_key",
            tags=["production", "web"],
            description="Production web server",
        )
    )

    manager.add_host(
        Host(
            name="db-server",
            hostname="192.168.1.11",
            username="admin",
            password="secure_password",
            tags=["production", "database"],
            description="Production database server",
        )
    )

    # 保存配置
    repo.flush()

    # 列出所有主机
    print("\n所有主机:")
    for host in manager.list_hosts():
        tags_str = ", ".join(host.tags) if host.tags else "无标签"
        print(f"  - {host.name}: {host.hostname} ({tags_str})")

    # 列出所有标签
    print(f"\n所有标签: {', '.join(manager.list_tags())}")

    # 按标签筛选
    print("\n生产环境主机:")
    for host in manager.list_hosts(tag="production"):
        print(f"  - {host.name}")


def example_file_transfer():
    """示例：文件传输"""
    config = ConnectionConfig(
        hostname="example.com", username="admin", key_filename="~/.ssh/id_rsa"
    )

    with SSHClient(config) as client:
        # 上传文件
        client.upload_file("./local_script.sh", "/tmp/remote_script.sh")
        print("文件上传成功")

        # 下载文件
        client.download_file("/var/log/syslog", "./downloaded_syslog")
        print("文件下载成功")

        # 列出远程目录
        entries = client.list_remote_directory("/tmp")
        print("\n/tmp 目录内容:")
        for entry in entries:
            type_str = "D" if entry["is_dir"] else "F"
            print(f"  [{type_str}] {entry['name']:30} {entry['size']:>10} bytes")


def example_sudo_command():
    """示例：执行 sudo 命令"""
    config = ConnectionConfig(
        hostname="example.com", username="ubuntu", key_filename="~/.ssh/id_rsa"
    )

    with SSHClient(config) as client:
        # 使用密码的 sudo
        result = client.execute_sudo("systemctl restart nginx", password="sudo_password")
        if result.success:
            print("Nginx 重启成功")
        else:
            print(f"重启失败: {result.stderr}")


def example_connection_test():
    """示例：连接测试"""
    repo = JsonHostRepository(filepath="hosts.json")
    manager = HostService(repository=repo)

    # 测试单个主机连接
    host_name = "web-server"
    print(f"\n测试连接到 {host_name}...")

    if manager.test_connection(host_name):
        print(f"✓ {host_name} 连接成功")
    else:
        print(f"✗ {host_name} 连接失败")

    # 测试所有主机连接
    print("\n测试所有主机连接:")
    results = manager.test_all_connections()
    for name, success in results.items():
        status = "✓" if success else "✗"
        print(f"  {status} {name}")


if __name__ == "__main__":
    print("=" * 50)
    print("Remote CMD 使用示例")
    print("=" * 50)

    print("\n1. 基本连接示例")
    print("-" * 30)
    # example_basic_connection()

    print("\n2. SSH Key 认证示例")
    print("-" * 30)
    # example_key_auth()

    print("\n3. 主机管理示例")
    print("-" * 30)
    example_host_management()

    print("\n4. 文件传输示例")
    print("-" * 30)
    # example_file_transfer()

    print("\n5. Sudo 命令示例")
    print("-" * 30)
    # example_sudo_command()

    print("\n6. 连接测试示例")
    print("-" * 30)
    # example_connection_test()

    print("\n" + "=" * 50)
    print("注意：取消注释相关函数调用以实际运行示例")
    print("=" * 50)
