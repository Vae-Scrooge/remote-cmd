# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.4.0] - 2026-09-12

v2.4 adds an opt-in retained-output cap for batch execution and cleans up logging handler
resources; default behavior and public result schemas are unchanged.

### Added

- `BatchExecutor` / `AsyncBatchExecutor` new optional `max_output_bytes` constructor
  parameter (default `None`) providing opt-in bounded output retention: when set to a
  positive integer, each host's retained `stdout` and `stderr` are deterministically
  truncated to at most that many UTF-8 bytes and a `[output truncated: N bytes omitted]`
  marker is appended. `None` preserves the previous behavior (full output retained).

### Fixed

- Output truncation is deterministic and UTF-8 byte-boundary safe: a multi-byte character
  split by the cap is dropped rather than mangled, and the reported omitted byte count
  reflects the bytes actually discarded.
- `setup_logging` now closes existing root-logger handlers before removing them, avoiding
  `unclosed file` resource warnings when logging is reconfigured (for example, rotating
  file handlers on repeated calls).
- Corrected the connection-pool lifetime wording in `docs/API.md`, `docs/architecture.md`,
  and `docs/tutorial-advanced.md`: internally created pools are created lazily per host
  and closed as soon as that host finishes (including its retries), not after the whole
  batch completes.

### Migration Guide (v2.3 → v2.4)

- **Default behavior is unchanged**: `max_output_bytes` defaults to `None`, which retains
  full `stdout`/`stderr` exactly as before.
- **Opt-in cap**: when configured, the value caps each host's retained `stdout` and
  `stderr` independently in the batch result.
- **Semantics preserved**: truncation appends a deterministic marker but does not change
  command success/failure, exit codes, or the `BatchResult` structure.
- **Scope of the bound**: the cap applies to retained `BatchResult` data. During execution
  the SSH client may still hold the full output transiently, so `max_output_bytes` is not
  a hard process-wide RSS limit.

## [2.3.0] - 2026-09-12

v2.3 makes blocking SFTP operations timeout-safe, hardens async cancellation cleanup,
and aligns the supported Python range and release tooling.

### Added

- SFTP inactivity timeout support: every blocking SFTP operation (`upload_file`,
  `download_file`, `list_remote_directory`, `create_remote_directory`,
  `remove_remote_file`, `remove_remote_directory`, `get_remote_file_info`) accepts an
  optional `timeout` argument. The effective timeout defaults to
  `ConnectionConfig.timeout` (30 s) and must be > 0 when provided explicitly.
- Synchronous `SSHClient` applies the timeout to the Paramiko SFTP channel
  (`Channel.settimeout`), so a stalled read/write raises `socket.timeout` for the
  actual operation and is translated into `SSHFileTransferError`.
- Asynchronous `AsyncSSHClient` bounds SFTP channel startup with `asyncio.wait_for`
  and uses a progress-based inactivity watchdog (asyncssh has no native timeout) to
  cancel stalled transfers.

### Changed

- Supported and tested Python range extended to 3.9–3.14 (classifiers and CI matrix);
  `Requires-Python` remains `>=3.9`.
- CI runs release-metadata validation for CHANGELOG.md-only changes (ordinary
  Markdown-only changes still skip the matrix).
- Publish builds pin `build==1.5.0` and `twine==7.0.0`.

### Reliability

- Timeout cleanup closes and discards the SFTP session so a stale or desynchronized
  channel can never be reused by a later operation.
- Async cancellation cleanup awaits the aborted operation and discards the session on
  outer cancellation, repeated cancellation, and cancellation during timeout cleanup;
  no watchdog task or stale session is left behind.
- The async timeout path preserves caller cancellation: an outer cancellation arriving
  while cleanup runs propagates `CancelledError` instead of being converted into
  `SSHFileTransferError`.

### Fixed

- SFTP uploads, downloads, and directory operations could block indefinitely when the
  remote side stopped responding; they now fail with `SSHFileTransferError` after the
  configured inactivity window.

### Migration Guide (v2.2 → v2.3)

- **SFTP timeouts**: blocking SFTP operations now honor timeout semantics. `timeout`
  defaults to `ConnectionConfig.timeout` (30 s) and can be overridden per call.
- **Inactivity, not total duration**: the timeout measures silence (no data progress),
  so long transfers that keep moving are not interrupted; only a stalled channel fails.
- **Failure mode**: a stalled transfer now raises `SSHFileTransferError` (after the
  inactivity window) instead of hanging indefinitely.
- **Python support**: officially supported and CI-tested on Python 3.9–3.14;
  `Requires-Python` remains `>=3.9`.

## [2.2.0] - 2026-09-12

This release focuses on bounded resource usage in batch execution, concurrent-writer
robustness for the SQLite host store, retry classification precision, and release/CI
hardening. See the migration notes before upgrading automated callers.

### Added

- `PoolClosedError` (inherits both `RemoteCmdError` and `RuntimeError`): connection-pool
  acquisition after `close_all()` now raises a dedicated type; existing
  `except RuntimeError` handlers keep working, and retry classification can distinguish
  "pool permanently closed" from unrelated `RuntimeError`s.
- `SqliteHostRepository` new optional `busy_timeout_ms` constructor parameter (default
  5000) controlling how long a writer waits for a concurrent writer's transaction.
- CI documentation drift gate (`scripts/check_docs_drift.py`): regenerates pdoc output and
  fails if tracked `docs/api/` is stale; `pdoc` is now pinned (`pdoc==15.0.4`) and docs are
  generated on the same Python version as CI (3.12).
- Release metadata gate (`scripts/check_release_metadata.py`): validates the single-source
  version, `pyproject.toml` dynamic-version attribute, `## [Unreleased]` section, the
  matching `## [x.y.z]` CHANGELOG heading, and (on release) that the git tag matches the
  package version.

### Changed

- **[Reliability] Internal connection-pool lifetime (resource cap)**: `BatchExecutor` /
  `AsyncBatchExecutor` now create internal pools lazily per host and close them as soon as
  that host (including all retries) finishes; each internal pool is capped at
  `max_connections=1`. Previously all N pools were created up front and kept their
  connections idle until the whole batch ended, retaining ~N connections/file descriptors
  even at low concurrency. The batch-wide number of live internal connections is now bounded
  by `max_concurrency`. External `pool_factory` pools remain caller-owned and are never
  closed; the per-host pool architecture is unchanged.
- **Retry classification narrowed to `PoolClosedError`**: v2.1 classified bare `RuntimeError`
  as permanent, which also disabled retries for transient `RuntimeError`s (e.g. thread
  creation failure, custom `client_factory` errors). v2.2 restores the v2.0 behavior for
  unknown exceptions (including bare `RuntimeError`); pool-closed remains non-retryable via
  the new `PoolClosedError`. Typed permanent (`SSHAuthenticationError`, `CredentialError`,
  `ConfigError`, `ValidationError`) and typed transient errors are unchanged.
- **[Reliability] SQLite concurrent-writer hardening**: connections now set `busy_timeout`
  (default 5000 ms) and write transactions use `BEGIN IMMEDIATE`; the initial
  `journal_mode=WAL` switch — which bypasses the busy handler in SQLite — is retried within
  the busy-timeout window. Concurrent CLI processes writing the same `hosts.db` no longer
  fail with `database is locked` or lose updates. Schema and the metadata `db_version` are
  unchanged.
- **Publish workflow hardening**: artifact upload/download actions aligned (`v7`), the artifact
  name is run-scoped, duplicate version publication now fails instead of being silently
  skipped (`skip-existing` removed), `twine check` validates distributions, and the release
  tag/package version are verified before publishing; Trusted Publishing and the
  GitHub Release → PyPI flow are unchanged.

### Fixed

- Concurrent writers to the same SQLite `hosts.db` (separate `remote-cmd` invocations) failed
  with `database is locked` during the first WAL switch and under write contention; 5 of 6
  concurrent processes could die and updates were lost. All writers now wait and commit.
- Large batches retained one idle connection per host until the entire batch completed
  (e.g. 1000 hosts → 1000 open sockets at once), risking fd/port exhaustion; internal
  connections are now released when each host finishes.

### Migration Guide (v2.1 → v2.2)

- **Exceptions**: `PoolClosedError` is a new subclass of both `RemoteCmdError` and
  `RuntimeError`; existing `except RuntimeError` / `except RemoteCmdError` handlers keep
  working. No existing exception names, imports, or catch paths were removed.
- **Retry**: bare `RuntimeError` is retryable again (v2.0 behavior) instead of being treated
  as permanent as in v2.1; connection-pool closure remains non-retryable via the typed
  `PoolClosedError`. Typed permanent and transient classifications are unchanged, and unknown
  `Exception` subclasses remain retryable.
- **Connection pools**: internally created batch pools are now lazy per-host pools capped at
  one connection and are closed when each host finishes, not after the whole `execute()`
  call. The caller-visible contract is unchanged: `pool_factory` pools are caller-owned and
  never closed. The batch-wide number of live internal connections is bounded by
  `max_concurrency`.
- **SQLite**: write transactions now wait up to `busy_timeout_ms` (default 5000 ms) and use
  `BEGIN IMMEDIATE`; no configuration or schema migration is required and the on-disk format
  and `db_version` are unchanged.

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