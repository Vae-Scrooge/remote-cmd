"""
Command-line interface module

Provides a complete command-line tool for managing remote hosts and
running SSH operations. Built on the Click framework, using the
HostService + Repository architecture.

Main commands:
- host: host management command group
  - add: add a new host
  - list: list all hosts
  - remove: remove a host
  - test: test host connection
- run: execute a command on a remote host
- upload: upload a file to a remote host
- download: download a file from a remote host
"""

import getpass
import os
from pathlib import Path
from typing import Any, Optional

import click
from click.exceptions import Exit

from remote_cmd import __version__
from remote_cmd.core.host import Host
from remote_cmd.service.batch_executor import BatchExecutor
from remote_cmd.service.credential_provider import (
    ChainCredentialProvider,
    EncryptedFileCredentialProvider,
    EnvCredentialProvider,
)
from remote_cmd.service.host_service import HostService
from remote_cmd.service.storage_factory import build_repository
from remote_cmd.utils.config import get_default_config_path, load_config
from remote_cmd.utils.crypto import CredentialEncryption


def _build_service(config_file: str, storage_backend: Optional[str] = None) -> HostService:
    """Build a HostService from a config file.

    Credential chain order: env var -> encrypted file storage.
    EncryptedFileCredentialProvider is used to fall back to decrypting
    encrypted passwords already persisted by add_host; together with the
    _encryption.decrypt fallback in HostService.resolve_host it ensures
    CLI-stored hosts can connect (see P0-A fix).

    The storage engine is selected by `storage_backend` when provided
    explicitly, otherwise inferred from the config file extension:
    .json -> JsonHostRepository; .db/.sqlite -> SqliteHostRepository.
    """
    repo = build_repository(
        filepath=config_file,
        storage_backend=storage_backend,
        encryption=CredentialEncryption(),
    )
    cred_provider = ChainCredentialProvider(
        [
            EnvCredentialProvider(),
            EncryptedFileCredentialProvider(repo),
        ]
    )
    return HostService(repository=repo, credential_provider=cred_provider)


@click.group()
@click.version_option(version=__version__, prog_name="remote-cmd")
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.option(
    "--hosts-file",
    "hosts_file_override",
    type=click.Path(),
    help="Override hosts storage file (e.g. hosts.db for SQLite). "
    "Takes precedence over the config's hosts_file; backend is inferred "
    "from the extension (.json/.db/.sqlite) unless storage_backend is set.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output mode")
@click.pass_context
def cli(
    ctx: click.Context,
    config: Optional[str],
    hosts_file_override: Optional[str],
    verbose: bool,
) -> None:
    """
    Remote CMD - SSH remote server management tool

    A powerful command-line tool for managing remote host configs,
    executing commands, and transferring files.

    Quick start:
        # Add a host
        remote-cmd host add my-server 192.168.1.100 admin -k ~/.ssh/id_rsa

        # List all hosts
        remote-cmd host list

        # Run a remote command
        remote-cmd run my-server "ls -la"

        # Upload a file
        remote-cmd upload my-server ./local.txt /remote/path.txt
    """
    ctx.ensure_object(dict)

    config_path = config or get_default_config_path()
    ctx.obj["config"] = load_config(config_path)
    ctx.obj["verbose"] = verbose

    # CLI --hosts-file 优先于配置文件中的 hosts_file；storage_backend 仍由配置提供
    # （扩展名推断覆盖 .json/.db/.sqlite 常见场景）
    hosts_file = hosts_file_override or ctx.obj["config"].get("hosts_file", "hosts.json")
    storage_backend = ctx.obj["config"].get("storage_backend")
    ctx.obj["service"] = _build_service(hosts_file, storage_backend)

    if verbose:
        click.echo(f"Using config file: {config_path}")
        click.echo(f"Using hosts file: {hosts_file}")


@cli.group()
def host() -> None:
    """
    Host management command group

    Add, remove, list, view, and test remote host connections.

    Commands:
        add     Add a new host
        list    List all hosts
        show    Show host details
        remove  Remove a host
        test    Test host connection
    """
    pass


@host.command("add")
@click.argument("name", required=True)
@click.argument("hostname", required=True)
@click.argument("username", required=True)
@click.option("--port", "-p", default=22, help="SSH port (default: 22)")
@click.option("--key", "-k", help="Path to SSH private key file")
@click.option("--tag", "-t", multiple=True, help="Host tag (may be repeated)")
@click.option("--description", "-d", default="", help="Host description")
@click.pass_context
def host_add(
    ctx: click.Context,
    name: str,
    hostname: str,
    username: str,
    port: int,
    key: Optional[str],
    tag: tuple,
    description: str,
) -> None:
    """
    Add a new host

    NAME: host name (unique identifier)
    HOSTNAME: host address (IP or domain)
    USERNAME: SSH login username

    Password input (in order of security):

      1. REMOTE_CMD_PASSWORD environment variable (safest: not in shell
         history or process list).
      2. Interactive prompt via getpass (no echo, not recorded).

    The insecure ``--password`` option has been removed to prevent passwords
    from leaking via shell history and ``ps``/``/proc``. Use one of the above.
    """
    service: HostService = ctx.obj["service"]

    # Resolve password: priority REMOTE_CMD_PASSWORD env var > interactive
    # getpass. Both are secure (not visible in command line/history).
    env_password = os.environ.get("REMOTE_CMD_PASSWORD")
    resolved_password: Optional[str] = None

    if env_password:
        resolved_password = env_password
    elif not key:
        # No key and no env var: prompt interactively and securely via getpass
        try:
            resolved_password = getpass.getpass("SSH password: ") or None
        except (EOFError, KeyboardInterrupt):
            click.echo(click.style("\n✗ cancelled", fg="red"), err=True)
            ctx.exit(1)

    host = Host(
        name=name,
        hostname=hostname,
        username=username,
        port=port,
        password=resolved_password,
        key_filename=key,
        tags=list(tag),
        description=description,
    )

    try:
        service.add_host(host)
        click.echo(f"✓ Host '{name}' added successfully")
    except (Exit, click.Abort):
        raise
    except ValueError as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@host.command("list")
@click.option("--tag", "-t", help="Filter hosts by tag")
@click.pass_context
def host_list(ctx: click.Context, tag: Optional[str]) -> None:
    """List all hosts"""
    service: HostService = ctx.obj["service"]
    hosts = service.list_hosts(tag=tag)

    if not hosts:
        if tag:
            click.echo(f"No hosts found with tag '{tag}'")
        else:
            click.echo("No hosts configured")
        return

    click.echo(f"\n{'Name':<20} {'Hostname':<25} {'Username':<15} {'Tags':<20}")
    click.echo("-" * 80)

    for host in hosts:
        tags_str = ", ".join(host.tags) if host.tags else "-"
        click.echo(f"{host.name:<20} {host.hostname:<25} {host.username:<15} {tags_str:<20}")

    click.echo()


@host.command("remove")
@click.argument("name", required=True)
@click.confirmation_option(prompt="Are you sure you want to remove this host?")
@click.pass_context
def host_remove(ctx: click.Context, name: str) -> None:
    """Remove a host"""
    service: HostService = ctx.obj["service"]

    try:
        service.remove_host(name)
        click.echo(f"✓ Host '{name}' removed")
    except (Exit, click.Abort):
        raise
    except KeyError as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@host.command("show")
@click.argument("name", required=True)
@click.pass_context
def host_show(ctx: click.Context, name: str) -> None:
    """Show host details"""
    service: HostService = ctx.obj["service"]

    try:
        host = service.get_host(name)
        click.echo(f"\n{'=' * 50}")
        click.echo(f"  Host details: {host.name}")
        click.echo(f"{'=' * 50}")
        click.echo(f"  Name:       {host.name}")
        click.echo(f"  Hostname:   {host.hostname}")
        click.echo(f"  Username:   {host.username}")
        click.echo(f"  Port:       {host.port}")
        if host.password:
            auth_type = "Password"
        elif host.key_filename:
            auth_type = "SSH key"
        else:
            auth_type = "SSH agent"
        click.echo(f"  Auth:       {auth_type}")
        if host.key_filename:
            # Sanitize key path: show only the filename, not full path
            key_name = Path(host.key_filename).name
            click.echo(f"  Key file:   {key_name}")
        tags_str = ", ".join(host.tags) if host.tags else "-"
        click.echo(f"  Tags:       {tags_str}")
        click.echo(f"  Desc:       {host.description or '-'}")
        click.echo(f"{'=' * 50}\n")
    except (Exit, click.Abort):
        raise
    except KeyError as e:
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@host.command("test")
@click.argument("name", required=True)
@click.pass_context
def host_test(ctx: click.Context, name: str) -> None:
    """Test host connection"""
    service: HostService = ctx.obj["service"]

    click.echo(f"Testing connection to '{name}'...")

    if service.test_connection(name):
        click.echo(f"✓ Host '{name}' connection successful")
    else:
        click.echo(f"✗ Host '{name}' connection failed", err=True)
        ctx.exit(1)


@cli.command()
@click.argument("host_name", required=True)
@click.argument("command", required=True)
@click.option(
    "--timeout",
    "-T",
    default=None,
    type=int,
    help="Command timeout in seconds (default: no timeout)",
)
@click.pass_context
def run(ctx: click.Context, host_name: str, command: str, timeout: Optional[int]) -> None:
    """
    Execute a command on a remote host

    HOST_NAME: host name

    COMMAND: command to execute
    """
    service: HostService = ctx.obj["service"]

    try:
        with service.connect_to_host(host_name) as client:
            result = client.execute(command, timeout=timeout)

            if result.stdout:
                click.echo(result.stdout)

            if result.stderr:
                click.echo(result.stderr, err=True)

            ctx.exit(result.exit_code)

    except (Exit, click.Abort):
        raise
    except Exception as e:  # noqa: BLE001
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument("host_name", required=True)
@click.argument("local_path", required=True)
@click.argument("remote_path", required=True)
@click.pass_context
def upload(ctx: click.Context, host_name: str, local_path: str, remote_path: str) -> None:
    """
    Upload a file to a remote host

    HOST_NAME: host name
    LOCAL_PATH: local file path
    REMOTE_PATH: remote target path
    """
    service: HostService = ctx.obj["service"]

    try:
        with service.connect_to_host(host_name) as client:
            client.upload_file(local_path, remote_path)
            click.echo(f"✓ Uploaded: {local_path} -> {host_name}:{remote_path}")
    except (Exit, click.Abort):
        raise
    except Exception as e:  # noqa: BLE001
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument("host_name", required=True)
@click.argument("local_path", required=True)
@click.argument("remote_path", required=True)
@click.pass_context
def download(ctx: click.Context, host_name: str, local_path: str, remote_path: str) -> None:
    """
    Download a file from a remote host

    HOST_NAME: host name
    LOCAL_PATH: local file path
    REMOTE_PATH: remote target path
    """
    service: HostService = ctx.obj["service"]

    try:
        with service.connect_to_host(host_name) as client:
            client.download_file(remote_path, local_path)
            click.echo(f"✓ Downloaded: {host_name}:{remote_path} -> {local_path}")
    except (Exit, click.Abort):
        raise
    except Exception as e:  # noqa: BLE001
        click.echo(f"✗ Error: {e}", err=True)
        ctx.exit(1)


@cli.command()
@click.argument("host_names", nargs=-1, required=True)
@click.argument("command", required=True)
@click.option("--concurrency", "-C", default=10, help="Max concurrency (default: 10)")
@click.option("--timeout", "-T", default=30, help="Command timeout in seconds (default: 30)")
@click.option("--retry", "-r", default=0, help="Failure retry count (default: 0)")
@click.option(
    "--retry-delay",
    default=1.0,
    help="Base retry delay in seconds; actual wait is exponential "
    "backoff with jitter (default: 1.0)",
)
@click.option("--async", "use_async", is_flag=True, help="Use async execution engine (asyncssh)")
@click.option("--show-failures", is_flag=True, help="Show only failed hosts")
@click.pass_context
def batch_run(
    ctx: click.Context,
    host_names: tuple[str, ...],
    command: str,
    concurrency: int,
    timeout: int,
    retry: int,
    retry_delay: float,
    use_async: bool,
    show_failures: bool,
) -> None:
    """
    Execute a command on multiple hosts in batch

    HOST_NAMES: list of host names (multiple allowed)
    COMMAND: command to execute

    Examples:

        remote-cmd batch-run web-1 web-2 db-1 "uptime"

        remote-cmd batch-run web-1 web-2 "df -h" -C 5 -r 2

        remote-cmd batch-run --async web-1 web-2 db-1 "uptime"
    """
    service: HostService = ctx.obj["service"]
    executor = BatchExecutor(
        host_service=service,
        max_concurrency=concurrency,
        command_timeout=timeout,
        use_async=use_async,
    )

    click.echo(
        f"Batch running on {len(host_names)} hosts, command='{command}', concurrency={concurrency}"
    )
    click.echo()

    bar: Any
    with click.progressbar(
        length=len(host_names),
        label="Progress",
        show_eta=True,
        show_percent=True,
    ) as bar:

        def progress(_completed: int, _total: int, _host_name: str) -> None:
            bar.update(1)

        result = executor.execute(
            host_names=list(host_names),
            command=command,
            retry_count=retry,
            retry_delay=retry_delay,
            progress_callback=progress,
        )

    click.echo()
    click.echo("=" * 50)
    click.echo("  Batch result summary")
    click.echo("=" * 50)
    click.echo(f"  Total:    {result.total}")
    click.echo(f"  Succeeded: {result.success}")
    click.echo(f"  Failed:   {result.failed}")
    click.echo(f"  Duration: {result.duration:.1f}s")
    click.echo(f"  Success:  {result.success_rate:.1%}")
    click.echo("=" * 50)

    if result.failed_hosts:
        click.echo()
        click.echo(click.style("Failed hosts:", fg="red"))
        for host in result.failed_hosts:
            host_result = result.results[host]
            error_msg = host_result.error or f"exit_code={host_result.exit_code}"
            click.echo(
                click.style(
                    f"  ✗ {host}: {error_msg}",
                    fg="red",
                )
            )

    if not show_failures and result.success_hosts:
        click.echo()
        click.echo(click.style("Successful hosts:", fg="green"))
        for host in result.success_hosts:
            click.echo(click.style(f"  ✓ {host}", fg="green"))

    if result.failed > 0:
        ctx.exit(1)


def main() -> None:
    """CLI entry point"""
    cli()


if __name__ == "__main__":
    main()
