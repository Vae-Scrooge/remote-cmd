# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-08-26

This release contains two layers of changes: the main implementation changes for v2.1.0
(Paramiko reliability, async connection pooling, retry policy, exception model, security, and CLI);
entries marked **[Release Hardening]** record final hardening before release and are not
"documentation-only" changes.

### Added

- **AsyncConnectionPool now properly integrated into AsyncBatchExecutor** (previously the pool
  had zero production-path consumption; the async kernel created a new connection on every
  attempt): for multi-host or retry workloads, pools are now created per host, connections
  are reused across retries, and `close_all()` is called automatically after batch completion,
  fully aligning with the synchronous `BatchExecutor`'s `SyncConnectionPool` behavior.
- `AsyncBatchExecutor` / `BatchExecutor` new `pool_factory` constructor parameter
  (external pool injection): when provided, the executor obtains the pool from the factory
  and reuses connections, **never closing it** (ownership remains with the caller, suitable
  for long-lived services reusing pools across batches); internally created pools are still
  closed automatically after a single `execute()` call.
- New retry policy module `remote_cmd.service.retry_policy` (`is_retryable` /
  `compute_backoff_delay`): explicitly distinguishes transient (retryable) from permanent
  (never retry) errors; exponential backoff with full jitter.
- Exception hierarchy extended with granular types (preserving existing parent-class
  catch behavior for backward compatibility):
  `SSHAuthenticationError(SSHConnectionError)`, `SSHTimeoutError(SSHConnectionError)`,
  `SSHCommandTimeoutError(SSHCommandError)`, `CredentialError(RemoteCmdError)`,
  `ConfigurationError` (alias of `ConfigError`); `CredentialEncryptionError` now also
  inherits `CredentialError` (unified under `RemoteCmdError` hierarchy; existing imports
  unchanged).
- `AsyncConnectionPool` new `client_factory` parameter (aligned with `SyncConnectionPool`,
  enables test injection).
- CLI `run` command new `--timeout/-T` option (previously no command execution timeout was
  available; hanging remote commands would block the CLI indefinitely).
- `SSHClient` / `AsyncSSHClient` environment variable injection now validates key names
  (`validate_environment`).

### Changed

- **Retry semantics tightened** (`BatchExecutor` / `AsyncBatchExecutor`): authentication,
  credential, configuration, validation, and programming errors (`ValueError` /
  `TypeError` / `KeyError` / `RuntimeError`) now fail immediately without retry; unknown
  `Exception` subclasses retain historical retryable behavior (backward compatibility for
  callers injecting custom `client_factory`).
- **Retry wait changed to exponential backoff + full jitter**: `retry_delay` semantics
  changed from "fixed interval" to "base delay"; after the nth failure, wait a random
  value in `0` to `min(60s, retry_delay * 2^n)` (inclusive), avoiding thundering herd
  from synchronized multi-host retries.
- `SSHClient.execute` / `execute_sudo` `timeout` is now explicitly **wall-clock semantics**
  (aligned with `AsyncSSHClient`'s `conn.run(timeout=...)`): timeout closes the channel,
  terminates the remote command, and raises `SSHCommandTimeoutError`. Previously timeout
  had no effect on silent/hanging commands (permanent blocking).
- `AsyncSSHClient.execute` no longer re-injects environment variables via `conn.run(env=...)`
  (that path depends on server-side `AcceptEnv` and diverged from synchronous semantics);
  unified to command-prefix `export` injection only.
- **[Release Hardening] `BatchExecutor(use_async=True)` called within a running event loop**
  now raises an actionable project-level `RuntimeError` (advising use of
  `AsyncBatchExecutor.execute()` directly), replacing the generic `asyncio.run` Python
  error; other sources of `RuntimeError` are unaffected.

### Fixed

- **[Critical] Paramiko large-output deadlock**: `SSHClient.execute` / `execute_sudo`
  previously called `recv_exit_status()` before reading output streams—when command output
  exceeded the SSH channel window (default 2 MB), the remote end blocked on write, the
  command never exited, and the call hung permanently (a scenario explicitly warned in
  Paramiko documentation). Now stderr is drained by a background thread, stdout is read
  on the current thread, both streams are consumed concurrently before retrieving the exit
  status.
- **[High] Multi-host batch containing unknown host raised `KeyError` in `execute`**:
  `_prepare_pool` failed to resolve a host during pool preparation, causing the entire batch
  to fail; now returns `None` and the single-host path records a "host not found" error
  entry (consistent with single-host contract).
- **[Release Hardening] Connection pool close race**: `SyncConnectionPool.acquire` /
  `AsyncConnectionPool.acquire` blocked on semaphore while `close_all()` completed; after
  acquiring the slot, the closed state is now re-checked—slot is returned and existing
  `RuntimeError("connection pool is closed")` is raised, instead of dispensing a stray
  connection from a closed pool.
- **[Release Hardening] `SSHClient._read_output` stderr drain thread join is now bounded**
  (5 seconds): in extreme scenarios (timeout callback `channel.close()` failure, or main
  thread exits with exception while channel remains open), the drain thread could remain
  blocked—unbounded join would hang the caller indefinitely.

### Security

- Environment variable **key name** injection protection: values were escaped via
  `shlex.quote`, but keys were directly interpolated into `export {k}=...` command prefix;
  keys containing shell metacharacters (e.g., `A; malicious`) could lead to command
  injection; keys must now match `[A-Za-z_][A-Za-z0-9_]*`, otherwise `ValidationError` is
  raised (rejected before concatenation).
- `ConnectionConfig` documentation corrected: default host key policy is `RejectPolicy`
  (previously incorrectly documented as `WarningPolicy`).

### Migration Guide (v2.0 → v2.1)

- **Exceptions**: existing exception names and import paths unchanged; new SSH exceptions
  are all subclasses (or aliases) of existing types, so `except SSHConnectionError` /
  `except SSHCommandError` / `except Exception` etc. remain fully compatible; fine-grained
  handling can switch to catching new subclasses. Exception: `CredentialEncryptionError`
  now additionally inherits `CredentialError` to unify under the credential exception
  hierarchy; existing `except CredentialEncryptionError` catches still work.
- **Retry**: callers relying on "authentication failures also retry" (not recommended) will
  observe auth errors now execute only once; callers relying on fixed retry intervals will
  observe intervals changed to exponential backoff with random values; expected wait after
  nth failure is approximately `min(60s, retry_delay * 2^n) / 2`. Unknown `Exception`
  subclasses remain retryable for compatibility with custom `client_factory`; custom
  implementations should prefer throwing typed `remote_cmd` exceptions for precise
  classification.
- **Timeout**: `SSHClient.execute(cmd, timeout=N)` for silent/hanging commands changed
  from "permanent block" to "raise `SSHCommandTimeoutError` after N seconds"—this is the
  documented timeout semantics, previously a defect.
- **Environment variables**: `execute(..., environment={"bad key": v})` previously
  produced a broken shell command; now raises `ValidationError`.
- **Connection pools**: multi-host or retry batches now reuse `AsyncConnectionPool` /
  `SyncConnectionPool`; `pool_factory` external pools are caller-owned (executor never
  closes); internally created pools are closed automatically after `execute()`.
- **Event loop**: `BatchExecutor(use_async=True)` cannot be called within an active
  asyncio event loop; use `await AsyncBatchExecutor.execute()` directly in that context.

---

## [2.0.0] - 2026-08-13

### Added

- `BatchExecutor` new `use_async: bool = False` constructor parameter: when enabled,
  the synchronous `execute` internally switches to the existing `AsyncBatchExecutor`
  native async kernel (asyncssh) to reduce thread/CPU overhead at high concurrency.
  New CLI `--async` switch passes through. External `execute` signature and return types
  unchanged, enabling seamless upper-layer switching. Caller must ensure no asyncio event
  loop is running on the current thread when enabled.
- CLI `--async` / `use_async` passthrough covered by existing unit tests
  (`TestBatchExecutorUseAsyncSwitch`).

### Changed

- **Security**: `SSHClient` / `AsyncSSHClient` command execution no longer writes full
  command text to debug logs (now logs command-less execution events); command execution
  failure exception messages remove command plaintext, retaining only failure reason.
  Prevents sensitive parameters (passwords, tokens) from entering logs or exception chains.
- **Fix**: `AsyncSSHClient.execute_sudo` `timeout` now covers entire command execution
  wall-clock (`proc.wait(timeout=...)`), aligned with `execute`'s `conn.run(timeout=...)`;
  avoids indefinite wait on hanging sudo (e.g., waiting for password). Behavior unchanged
  when timeout not provided (indefinite wait).
- **Validation**: `BatchExecutor` / `AsyncBatchExecutor` constructor parameters add
  guards—`max_concurrency` must be `>= 1`, `command_timeout` must be `> 0`; `execute`'s
  `retry_count` must be `>= 0`, `retry_delay` must be `>= 0`; invalid values raise
  `ValueError` (previously invalid values surfaced at runtime as cryptic errors, e.g.,
  `ThreadPoolExecutor(max_workers=0)` or `Semaphore(0)` deadlock).
- **Security**: batch execution start log no longer includes full command text (consistent
  with command desensitization principle).
- **Version single source**: new lightweight no-side-effect module `remote_cmd/_version.py`
  as sole version source; `remote_cmd/__init__.py`'s `__version__` now imports from
  `_version`; `pyproject.toml` `version` changed to dynamic read (`[tool.setuptools.dynamic]`).
  Developers only modify `_version.py` once; packaging auto-picks it up; setuptools
  version parsing no longer triggers `remote_cmd` package-level import (avoids `ImportError`
  in clean build environments missing dependencies). Public API (`__all__` entry for
  `__version__`) remains unchanged.
- **Fix**: `SyncConnectionPool` / `AsyncConnectionPool` add lifecycle guards—`acquire`
  after `close_all()` raises `RuntimeError("connection pool is closed")`; `release` after
  close closes the connection directly instead of returning to idle queue (prevents stray
  connections and post-close pool "resurrection" leaks).

### Fixed

- **Fix**: `BatchExecutor.execute` / `AsyncBatchExecutor.execute` deduplicate `host_names`
  (preserving first-occurrence order)—duplicate host names execute only once. Previously
  duplicates caused `results` to be overwritten by later completions, skewing
  `total`/`success`/`failed` statistics (e.g., `["srv1","srv1"]` reported success=0 but
  actually succeeded on 1 host).

### Changed

- **Refactor**: 8 incremental steps (see `REFACTORING_PLAN.md`), pure structural/type
  tightening, existing behavior unchanged, tests 410→424:
  - `core/host.py`: `Host.tags` tightened to `list[str] = field(default_factory=list)`
    (constructor still accepts historical `None` data)
  - New `utils/credential_guard.PasswordGuard`: unifies JSON/SQLite repository password
    encryption/decryption strategy
  - New `service/_pool_policy` (`ConnectionMeta` + pure time judgment),
    `service/_host_runner`, `service/_types`: eliminate duplicate logic between sync/async
    connection pools and executors
  - CLI commands add `ctx: click.Context` and return types; modules add `__init__ ->
    None`
  - mypy full pass (31 source files, 0 errors)

### Breaking

- `SSHClient.list_remote_directory` and `AsyncSSHClient.list_remote_directory` return
  type changed from `list[dict]` to `list[RemoteFileEntry]` (new dataclass). External
  callers must change `entry["name"]` to `entry.name`. Recommended to ship with v2.0.

---

## [1.2.3] - 2026-08-11

### Added

- `storage_factory.build_repository` new `encryption` parameter, passed through to
  JSON/SQLite repositories: as defense-in-depth, even if caller bypasses `HostService`
  and directly `save()`s plaintext passwords, persisted data remains encrypted.
- CLI constructs `HostService` with `CredentialEncryption`, enabling repository-level
  encryption.

### Changed

- `EnvCredentialProvider._host_env_suffix` documentation added: non-alphanumeric
  characters normalized to underscores; `web-1` and `web_1` map to the same environment
  variable (requires consistent host naming).

### Fixed

- Missing `# noqa: ARG002` annotation in `tests/integration/conftest.py`.
- `ruff format` applied across entire repository.

---

## [1.2.2] - 2026-08-06

### Added

- `EnvCredentialProvider` supports host-specific environment variables
  (`REMOTE_CMD_PASSWORD_<HOST>`, takes precedence over global `REMOTE_CMD_PASSWORD`),
  preventing global variable from being misapplied to all hosts.
- `SqliteHostRepository` supports `encryption` parameter: when configured, passwords are
  automatically encrypted at rest and decrypted on read.

### Changed

- `AsyncConnectionPool._check_connection` adds idle fast-path: connections recently used
  (idle not timed out) skip health check, aligning with `SyncConnectionPool` behavior,
  reducing redundant round-trips under high concurrency.

### Fixed

- `BatchExecutor` / `AsyncBatchExecutor` retry-all-failed case: `BatchHostResult.duration`
  was always 0; now retains last attempt's actual duration.
- `JsonHostRepository` / `SqliteHostRepository` `save()` documentation warns: persisting
  plaintext passwords without encryption is caller's responsibility.

---

## [1.2.1] - 2026-08-06

### Fixed

- Restored `HostManager` public API accidentally removed in v1.2.0 (re-exported as
  backward-compatibility layer, internally delegates to `HostService` + `JsonHostRepository`),
  avoiding breakage of `from remote_cmd import HostManager` existing compatibility.
- Restored `tests/test_host_manager.py` test coverage.

---

## [1.1.1] - 2026-08-01

### Fixed

- Fixed `import remote_cmd` crash when asyncssh (`[async]` extra) not installed:
  `__init__.py` unconditionally imported native async modules at top level, causing base
  install to crash; changed to try/except graceful degradation—async symbols not exported
  when asyncssh absent, synchronous API unaffected.

---

## [1.1.0] - 2026-07-31

### Added

- Native async SSH client `AsyncSSHClient` (based on asyncssh), replacing thread-pool
  wrapper version.
- `AsyncConnectionPool` connection pool (semaphore concurrency control, metadata side
  table, idle/lifecycle recycling, health checks).
- `AsyncBatchExecutor` native async batch executor (asyncio.Semaphore concurrency
  scheduling).
- Integration test framework (paramiko `ServerInterface` mock SSH server).
- CI workflow (Python 3.9–3.12 matrix, uv + ruff + mypy + pytest).
- PyPI auto-publish workflow (triggered on release published).
- Documentation system: architecture docs, quickstart, advanced tutorial, security
  policy, troubleshooting.

### Changed

- Architecture merge: removed executor-wrapper `AsyncSSHClient`, unified to native
  asyncssh implementation.
- `ConnectionPool` unified to `AsyncConnectionPool` (`ConnectionPool` retained as
  backward-compatibility alias).
- `BatchExecutor` and `AsyncBatchExecutor` contract unified:
  - `HostService._resolve_host` promoted to public `resolve_host`
  - Progress callback type shared as `ProgressCallback`
  - Async version adds `KeyboardInterrupt` handling (aligned with synchronous semantics)
- Dependency injection types precise (`host_service: Any` → `HostService`).
- Installation method optimized: pip install becomes preferred installation method.
- README structure adjusted and internationalized (English intro, asciinema demo).

### Fixed

- **[P0 Security]** CLI password switched to `getpass` (avoids shell history leakage).
- **[P0 Security]** Credential encryption format triple-validation prevents collisions.
- **[P4]** CLI `click.exceptions.Exit` inherits `RuntimeError`, mistakenly caught by
  `except Exception`.
- **[P5]** `ConnectionPool.release` idle timeout dead code (refreshing `_last_used` first
  caused idle to always be 0).
- **[P5]** `ConnectionPool.release` `QueueFull` unreachable branch (`await put` →
  `put_nowait`).
- **[P5]** sqlite `_txn()` connection leak.
- **[P5]** Credential chain miss decryption fallback mechanism.
- **[P1]** 13 B904 exception chain breaks, 8 E501 line-length violations, multiple
  examples lint issues.

### Security

- Command-line password exposure risk fixed (switched to interactive getpass).
- Credential encryption format collision risk fixed (prefix + plaintext length + encrypted
  format triple validation).

---

## [1.0.0] - 2026-05-31

### Added

- Published to PyPI, supports `pip install remote_cmd_manager`.
- Added PyPI version and download count badges.
- README added English introduction for international users.
- `setup.py` added PyPI download link and project URLs.

---

## [0.1.0] - 2024-01-15 (Initial Development Release)

### Added

- Initial release
- ✅ SSH connection management (password and key authentication)
- ✅ Remote command execution (sync/async)
- ✅ File transfer (SFTP upload/download)
- ✅ Host management system (JSON persistence)
- ✅ Tag categorization system
- ✅ Complete CLI tool
- ✅ Python API
- ✅ Context manager support
- ✅ Sudo command execution
- ✅ Connection health checks
- ✅ Configuration management (YAML/JSON)
- ✅ Comprehensive error handling
- ✅ Logging system
- ✅ Unit tests

### Core Features

- `SSHClient` — SSH connection client
- `HostManager` — Host manager
- `ConnectionConfig` — Connection configuration
- `CommandResult` — Command execution result
- `Host` — Host configuration dataclass

### CLI Commands

- `host add` — Add a host
- `host list` — List hosts
- `host remove` — Remove a host
- `host test` — Test connection
- `run` — Execute remote command
- `upload` — Upload file
- `download` — Download file

### Documentation

- README.md
- API.md
- CONTRIBUTING.md
- TROUBLESHOOTING.md
- LICENSE

---

## Version Notes

### Semantic Versioning Rules

- **MAJOR** — Incompatible API changes
- **MINOR** — Backward-compatible functionality additions
- **PATCH** — Backward-compatible bug fixes

### Version Section Labels

- `[Unreleased]` — Unreleased changes
- `Added` — New features
- `Changed` — Changes in existing functionality
- `Deprecated` — Soon-to-be removed features
- `Removed` — Removed features
- `Fixed` — Bug fixes
- `Security` — Security improvements

---

**View full history:** [GitHub Releases](https://github.com/Vae-Scrooge/remote-cmd/releases)

[Unreleased]: https://github.com/Vae-Scrooge/remote-cmd/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/Vae-Scrooge/remote-cmd/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.2.3...v2.0.0
[1.2.3]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.2.0...v1.2.1
[1.1.1]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/Vae-Scrooge/remote-cmd/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Vae-Scrooge/remote-cmd/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/Vae-Scrooge/remote-cmd/releases/tag/v0.1.0