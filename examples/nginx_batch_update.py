"""
Example: Batch nginx config update across production web servers

Scenario: You manage 10 web servers. Update nginx config on all production
servers, validate syntax, reload, and verify the service is running.

This example shows:
- Adding hosts with tags
- Tag-filtered batch operations
- Error handling per server with a summary report

Usage:
    python examples/nginx_batch_update.py

Note: Real SSH connections are commented out. Set up your own hosts.json
and uncomment the connection calls to run against actual servers.
"""

from remote_cmd.core.host import Host
from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService


def add_demo_hosts(repo: JsonHostRepository, manager: HostService) -> None:
    """Add demo hosts with tags."""
    hosts = [
        Host(
            name="web-01",
            hostname="192.168.1.10",
            username="ubuntu",
            key_filename="~/.ssh/id_rsa",
            tags=["production", "web"],
            description="Production web server 1",
        ),
        Host(
            name="web-02",
            hostname="192.168.1.11",
            username="ubuntu",
            key_filename="~/.ssh/id_rsa",
            tags=["production", "web"],
            description="Production web server 2",
        ),
        Host(
            name="web-03",
            hostname="192.168.1.12",
            username="ubuntu",
            password="your_password",
            tags=["production", "web"],
            description="Production web server 3",
        ),
        Host(
            name="db-01",
            hostname="192.168.1.20",
            username="admin",
            key_filename="~/.ssh/db_key",
            tags=["production", "database"],
            description="Production database server",
        ),
    ]
    for host in hosts:
        manager.add_host(host)
    repo.flush()
    print(f"Added {len(hosts)} hosts to hosts.json")


def update_nginx_web(manager: HostService, host_name: str, config_path: str) -> dict:
    """
    Update nginx config on a single web server.

    Steps:
        1. Upload new nginx config
        2. Test nginx syntax (nginx -t)
        3. Reload nginx if syntax is valid
        4. Verify service is running

    Returns status dict with host, success, and error message.
    """
    result = {"host": host_name, "success": False, "error": None}

    # with manager.connect_to_host(host_name) as client:
    #     try:
    #         # Upload config
    #         client.upload_file(config_path, "/tmp/nginx.conf.new")
    #
    #         # Test syntax
    #         test = client.execute_sudo(
    #             "nginx -t -c /tmp/nginx.conf.new",
    #             password="sudopass",
    #         )
    #         if not test.success:
    #             result["error"] = f"nginx config test failed: {test.stderr}"
    #             return result
    #
    #         # Apply and reload
    #         client.execute_sudo(
    #             "cp /tmp/nginx.conf.new /etc/nginx/nginx.conf "
    #             "&& systemctl reload nginx",
    #             password="sudopass",
    #         )
    #
    #         # Verify
    #         verify = client.execute("systemctl is-active nginx")
    #         if verify.stdout.strip() != "active":
    #             result["error"] = f"nginx not active after reload: {verify.stdout}"
    #             return result
    #
    #         result["success"] = True
    #
    #     except Exception as e:
    #         result["error"] = str(e)

    # For demo: simulate success
    result["success"] = True
    return result


def batch_update_nginx(config_path: str) -> None:
    """Update nginx on all production web servers and print summary."""
    repo = JsonHostRepository(filepath="hosts.json")
    manager = HostService(repository=repo)

    # Find all web servers
    web_servers = list(manager.list_hosts(tag="web"))
    if not web_servers:
        print("No web servers found. Run with --setup first.")
        return

    print(f"\n{'='*60}")
    print(f"Batch nginx update on {len(web_servers)} web servers")
    print(f"Config: {config_path}")
    print(f"{'='*60}\n")

    results = []
    for host in web_servers:
        print(f"  Processing {host.name}...")
        status = update_nginx_web(manager, host.name, config_path)
        results.append(status)

    # Summary table
    print(f"\n{'─'*60}")
    print(f"{'Host':<12} {'Status':<10} {'Detail'}")
    print(f"{'─'*60}")
    ok_count = 0
    for r in results:
        if r["success"]:
            print(f"  {r['host']:<10} {'✅ OK':<10} nginx reloaded")
            ok_count += 1
        else:
            print(f"  {r['host']:<10} {'❌ FAIL':<10} {r['error']}")
    print(f"{'─'*60}")
    print(f"  Result: {ok_count}/{len(results)} servers updated successfully")
    print(f"{'─'*60}")


if __name__ == "__main__":
    import sys

    if "--setup" in sys.argv:
        repo = JsonHostRepository(filepath="hosts.json")
        manager = HostService(repository=repo)
        add_demo_hosts(repo, manager)
    elif len(sys.argv) > 1:
        batch_update_nginx(sys.argv[1])
    else:
        print("Usage:")
        print("  python examples/nginx_batch_update.py --setup           Create demo hosts")
        print("  python examples/nginx_batch_update.py nginx.conf        Run batch update")
