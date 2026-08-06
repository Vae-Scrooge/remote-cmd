"""
Example: Deploy latest code to staging servers

Scenario: Deploy application code to staging servers. Pull latest from git,
install dependencies, run DB migrations if needed, restart the service, and
verify the deployment.

This example shows:
- Sequential command execution with error handling
- Conditional execution (abort on failure)
- Deployment summary report
- Python API for integration into CI/CD pipelines

Usage:
    python examples/deploy_script.py

Note: Real SSH connections are commented out — uncomment to run against
your own servers configured in hosts.json.
"""

from remote_cmd.repository.json_host_repository import JsonHostRepository
from remote_cmd.service.host_service import HostService


def deploy_to_server(
    manager: "HostService",
    host_name: str,
    app_dir: str = "/var/www/app",
    service_name: str = "app",
) -> dict:
    """
    Deploy application to a single server.

    Deployment steps:
        1. Pull latest code from git
        2. Install/update Python dependencies
        3. Run database migrations
        4. Restart service
        5. Verify service health

    Returns dict with host, success (bool), and step_results (list).
    """
    step_results = []

    # with manager.connect_to_host(host_name) as client:
    #     steps = [
    #         ("git pull", f"cd {app_dir} && git pull origin main"),
    #         ("install deps", f"cd {app_dir} && pip install -r requirements.txt"),
    #         ("migrations", f"cd {app_dir} && python manage.py migrate"),
    #         ("restart", f"sudo systemctl restart {service_name}"),
    #         ("verify", f"systemctl is-active {service_name}"),
    #     ]
    #
    #     for step_name, command in steps:
    #         print(f"    [{step_name}] {command}")
    #
    #         if "sudo" in command:
    #             result = client.execute_sudo(command, password="sudopass")
    #         else:
    #             result = client.execute(command)
    #
    #         step_results.append({
    #             "step": step_name,
    #             "command": command,
    #             "success": result.success,
    #             "stdout": result.stdout.strip(),
    #             "stderr": result.stderr.strip() if result.stderr else "",
    #         })
    #
    #         if not result.success:
    #             print(f"    ✗ Failed at step '{step_name}'")
    #             break
    #
    #     # Final health check
    #     if step_results and step_results[-1]["success"]:
    #         health = client.execute("curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health")
    #         step_results.append({
    #             "step": "health check",
    #             "command": "HTTP health endpoint",
    #             "success": health.stdout.strip() == "200",
    #             "stdout": f"HTTP {health.stdout.strip()}",
    #             "stderr": "",
    #         })

    # For demo: simulate successful deployment
    step_results = [
        {"step": "git pull", "success": True, "stdout": "Already up to date."},
        {"step": "install deps", "success": True, "stdout": "All requirements satisfied."},
        {"step": "migrations", "success": True, "stdout": "No migrations to apply."},
        {"step": "restart", "success": True, "stdout": "Service restarted."},
        {"step": "verify", "success": True, "stdout": "active"},
        {"step": "health check", "success": True, "stdout": "HTTP 200"},
    ]

    all_ok = all(s["success"] for s in step_results)
    return {"host": host_name, "success": all_ok, "steps": step_results}


def deploy_staging(config_file: str = "hosts.json") -> None:
    """Deploy to all staging servers."""
    repo = JsonHostRepository(filepath=config_file)
    manager = HostService(repository=repo)

    servers = list(manager.list_hosts(tag="staging"))
    if not servers:
        print("No staging servers found. Add hosts with tag='staging' to hosts.json.")
        return

    print(f"\n{'='*65}")
    print(f"  Deploying to {len(servers)} staging server(s)")
    print(f"{'='*65}\n")

    results = []
    for server in servers:
        print(f"  ── {server.name} ({server.hostname}) ──")
        result = deploy_to_server(manager, server.name)
        results.append(result)
        status = "✅" if result["success"] else "❌"
        print(f"  {status} {server.name}: {'Deployed' if result['success'] else 'Failed'}\n")

    # Summary
    print(f"{'='*65}")
    ok = sum(1 for r in results if r["success"])
    print(f"  Deploy summary: {ok}/{len(results)} servers OK")
    for r in results:
        failed_steps = [s["step"] for s in r["steps"] if not s["success"]]
        if failed_steps:
            print(f"  ❌ {r['host']}: failed at {', '.join(failed_steps)}")
        else:
            print(f"  ✅ {r['host']}: all steps passed")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "hosts.json"
    deploy_staging(config)
