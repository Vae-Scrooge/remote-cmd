"""
Example: Quick health check across all servers

Scenario: Run a health check across all production servers — disk usage,
memory, CPU load, and uptime. Flag any server that exceeds thresholds.

This example shows:
- Iterating over all hosts
- Running multiple commands per host
- Parsing and formatting results
- Alerting on threshold violations

Usage:
    python examples/system_health_check.py

Note: Real SSH connections are commented out — uncomment to run against
your own servers configured in hosts.json.
"""

from remote_cmd.core.host_manager import HostManager

# Alert thresholds
DISK_WARN_PCT = 85
LOAD_WARN = 5.0


def check_server(manager: HostManager, host_name: str) -> dict:
    """
    Run health checks on a single server.

    Returns dict with host name, metrics, alerts, and status.
    """
    metrics = {"disk_pct": 0, "mem_pct": 0, "load_1m": 0.0, "uptime": ""}
    alerts = []

    # with manager.connect_to_host(host_name) as client:
    #     # Disk usage (root partition)
    #     disk = client.execute("df -h / | awk 'NR==2 {print $5}'")
    #     metrics["disk_pct"] = int(disk.stdout.strip().rstrip("%"))
    #
    #     # Memory usage
    #     mem = client.execute(
    #         "free | grep Mem | awk '{printf \"%.0f\", $3/$2 * 100}'"
    #     )
    #     metrics["mem_pct"] = int(mem.stdout.strip())
    #
    #     # Load average (1 min)
    #     load = client.execute("uptime | awk -F'load average:' '{print $2}' | cut -d',' -f1")
    #     metrics["load_1m"] = float(load.stdout.strip())
    #
    #     # Uptime
    #     uptime = client.execute("uptime -p")
    #     metrics["uptime"] = uptime.stdout.strip().replace("up ", "")

    # For demo: simulate metrics
    import random
    metrics = {
        "disk_pct": random.randint(20, 95),
        "mem_pct": random.randint(30, 90),
        "load_1m": round(random.uniform(0.5, 8.0), 2),
        "uptime": "2 days, 6 hours",
    }

    # Check thresholds
    if metrics["disk_pct"] >= DISK_WARN_PCT:
        alerts.append(f"Disk at {metrics['disk_pct']}% (threshold: {DISK_WARN_PCT}%)")
    if metrics["mem_pct"] >= 90:
        alerts.append(f"Memory at {metrics['mem_pct']}%")
    if metrics["load_1m"] >= LOAD_WARN:
        alerts.append(f"Load {metrics['load_1m']} (threshold: {LOAD_WARN})")

    status = "alert" if alerts else "ok"
    return {"host": host_name, "status": status, "metrics": metrics, "alerts": alerts}


def run_health_check(config_file: str = "hosts.json") -> None:
    """Run health check on all hosts and print formatted report."""
    manager = HostManager(config_file)
    all_hosts = list(manager.list_hosts())

    if not all_hosts:
        print("No hosts configured. Add hosts to hosts.json first.")
        return

    print(f"\n{'='*70}")
    print(f"  System Health Check — {len(all_hosts)} host(s)")
    print(f"{'='*70}\n")

    results = []
    for host in all_hosts:
        print(f"  Checking {host.name} ({host.hostname})...")
        result = check_server(manager, host.name)
        results.append(result)

    # Print report
    print(f"\n{'='*70}")
    print("  REPORT")
    print(f"{'='*70}")
    print(f"  {'Host':<14} {'Disk':<8} {'Mem':<8} {'Load':<8} {'Status':<8}")
    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")

    alert_count = 0
    for r in results:
        m = r["metrics"]
        status_icon = "⚠️ " if r["status"] == "alert" else "✅"
        print(f"  {r['host']:<14} {m['disk_pct']:>3}%    {m['mem_pct']:>3}%    {m['load_1m']:<6} {status_icon:<8}")
        for alert in r["alerts"]:
            print(f"    ⚠️  {alert}")
            alert_count += 1

    print(f"{'='*70}")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    print(f"  Healthy: {ok_count}/{len(results)}  |  Alerts: {alert_count}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import sys

    config = sys.argv[1] if len(sys.argv) > 1 else "hosts.json"
    run_health_check(config)
