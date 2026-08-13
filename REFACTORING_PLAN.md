# remote-cmd 项目重构规划报告

> 状态：规划（Blueprint）阶段，未修改任何业务代码。
> 目的：供 Claude / Codex 审阅、判断、修改指正。
>
> 审阅结论（Claude）：PASS ✅ — 规划可执行。
> 已采纳修正：① 探活逻辑非 I/O-free，_pool_policy 仅提取纯时间判断；② host_manager.py
> 为已弃用向后兼容层，剔除出重构工步；③ _host_runner 实施前先跑两 executor 专项测试锁 baseline。

---

## 1. 基准锁定（Baseline Lock）

**当前状态（已实测确认，v1.2.3）**
- 版本：`1.2.3`，git HEAD = `b493a4e`（working tree clean）
- 全量测试：**410 passed, 22 deselected**（benchmark + integration）
- ruff check：**All checks passed**；ruff format：**53 files already formatted**
- Python：`requires-python >= 3.9`，ruff target `py39`，mypy python_version `3.10`

**测试分布（19 个文件，410 用例）**

| 文件 | 用例数 |
|---|---|
| tests/test_ssh_client.py | 56 |
| tests/test_sqlite_repository.py | 38 |
| tests/test_cli.py | 38 |
| tests/test_async_batch_executor.py | 36 |
| tests/test_logging_utils.py | 33 |
| tests/test_sync_connection_pool.py | 25 |
| tests/test_host_service.py | 23 |
| tests/test_config.py | 23 |
| tests/test_task_runner.py | 19 |
| tests/test_batch_executor.py | 19 |
| tests/test_storage_factory.py | 18 |
| tests/test_repository.py | 18 |
| tests/test_async_ssh_client.py | 16 |
| tests/test_keyring_provider.py | 12 |
| tests/test_crypto.py | 10 |
| tests/test_credential_provider.py | 10 |
| tests/test_ssh_service.py | 9 |
| tests/test_host_manager.py | 5 |
| tests/test_host.py | 2 |

**锁定的不变量（重构期间不得破坏）**

| 不变量 | 位置 | 说明 |
|---|---|---|
| CLI 退出码语义 | cli/main.py | run → exit_code；batch-run → 失败时 exit 1 |
| 凭据解密链 | host_service.resolve_host | env → cred chain → encryption 兜底；绝不就地改 repo 内存对象 |
| 连接池 active 语义 | sync/async pool get_metrics | `len(_connections) - free.qsize()`，不得为负 |
| 原子写入 | json repo flush | temp file + os.replace |
| SQLite 事务 + fd 管理 | `with self._lock, self._txn()` | 连接每次用完 close，防 fd 泄漏 |
| 密码加密边界 | save/flush | 加密在 HostService 层或 repo 序列化层，明文不落盘 |
| 敏感字段脱敏 | Host.sanitized_dict / SensitiveDataFilter | password → `***`，key → 文件名 |

---

## 2. 边界识别（Boundary Identification）

### A. 强重复模式（高置信度去重候选）

**A1. 连接池：sync/async 几乎镜像（约 70% 相同）**
- `core/sync_connection_pool.py`（310 行）vs `core/async_connection_pool.py`（293 行）
- 完全重复：`get_metrics`、`acquire`/`release` 骨架、`_touch`、`_check_connection`（生命周期 + 空闲 + 探活）、`_cleanup_expired`、上下文管理器、监控循环骨架
- 差异点：queue 类型、semaphore 类型、lock 类型、`await`/`async with`、monitor 用 task vs thread、`_start_monitor` vs `start_monitor`
- 最大的重复源，但合并需谨慎——同步/异步 API 表面不同

**A2. 批量执行器：`_execute_on_host` 高度重复**
- `batch_executor.py:402` vs `async_batch_executor.py:176`
- host 解析（同样的 KeyError / RuntimeError 分支）、ConnectionConfig 构建（7 字段）、重试循环骨架、BatchHostResult 构造 —— 约 **85% 相同**

**A3. JSON / SQLite 仓库：密码加解密策略重复**
- `json_host_repository.py` `_load` / `_serialize_hosts` vs `sqlite_host_repository.py` `_row_to_host` / `save`
- 相同模式：`if pw and encryption.is_encrypted(pw): decrypt` / `if pw and not is_encrypted: encrypt`

**A4. HostService 内部凭据构建重复**
- `_decrypt_host`（217）与 `resolve_host`（238）的"构建带解密密码的新 Host"逻辑重叠
- `connect_to_host` / `test_connection` 重复传 5 个字段给 ssh_service

### B. 弱重复 / 可提取工具（中置信度）

- **B1.** `from pathlib import Path` 延迟导入出现在 host.py:127、host_service.py:275、cli/main.py:275 —— 应移到模块顶部。
  注：host_manager.py:97 同模式，但该模块为已弃用向后兼容层（v1.2.1 恢复，计划未来移除），
  不纳入重构工步（审阅修正）。
- **B2.** `execute_sudo` / `execute` 的 stdout/stderr 解码逻辑（async_ssh_client.py:209-218, 254-263）重复 3 次
- **B3.** CLI 错误处理模式 `except (Exit, click.Abort): raise / except X: echo + ctx.exit(1)` 重复 6 处（host_add/remove/show/run/upload/download + batch_run 中断分支）（审阅修正：5→6）
- **B4.** `test_all_connections` 的 ThreadPoolExecutor + as_completed 模式仅存在于 host_service.py（host_manager.py 为弃用兼容层，不纳入统计）

### C. 类型提示缺口（已用 AST 扫描确认）

**C1. CLI（最大缺口）**
- 全部 click 命令缺 `ctx` 注解与返回类型：main.py:80, 143, 210, 236, 253, 293, 310, 343, 369, 400, 491

**C2. `__init__` 缺 `-> None`**
- crypto.py:50, 61；ssh_client.py:169；host_service.py:55；ssh_service.py:28；task_runner.py:85；credential_provider.py:63, 96, 126, 154；json/sqlite repo；host_manager.py:35, 56；batch_executor.py:132

**C3. 私有辅助缺注解**
- ssh_client.py:582 `_makedirs`、610 `_rm_recursive`（无参无返回）
- batch_executor.py:322 `_process_future_result` 的 `future`
- sqlite_host_repository.py:126 `_txn`

**C4. 设计缺陷型类型问题**
- `_meta: dict[int, dict[str, Any]]` —— 应定义 `ConnectionMeta` dataclass（sync/async 池都如此）
- `Host.tags: Optional[list[str]]` 但语义上永远非 None（`__post_init__` 归零）→ 应用 `list[str] = field(default_factory=list)`
- `list_remote_directory -> list[dict[str, Any]]` 应定义 `RemoteFileEntry` dataclass
- 仓库 `list_tags` 用 `builtins.list[str]`（因模块已有 `builtins` import 避开与方法命名冲突）→ 应重命名方法冲突源

### D. 复杂度热点
- `cli/main.py:400 batch_run`：7 参数 + 40 行输出逻辑
- `sqlite_host_repository.py`：427 行，`list_paginated` 双分支 SQL
- `sync_connection_pool.py _check_connection` + `_cleanup_expired`：生命周期判断逻辑重复

---

## 3. 蓝图预设计（Blueprint）— 不写代码

### 模块架构调整方案（按"一次一个模块"的小步原则）

```
当前模块                            目标模块
─────────────────────────────────────────────────────────────────
core/sync_connection_pool.py       保持独立（同步/异步 API 表面不同，
core/async_connection_pool.py         强行抽象会牺牲可读性——符合原则 2）
                                    → 但提取"池策略"纯函数（仅时间判断，无 I/O）：
                                       service/_pool_policy.py:
                                         - lifetime_expired(created_at, max_lifetime) -> bool
                                         - idle_expired(last_used, idle_timeout) -> bool
                                         - should_close(meta, max_lifetime, idle_timeout, connected) -> bool
```

**关键决策：不合并 sync/async 池类。** 原因：
1. 同步用 `queue.Queue` / `Semaphore` / `threading.Thread`；异步用 `asyncio.Queue` / `Semaphore` / `Task`
2. 合并成泛型类会引入大量条件分支与泛型约束，可读性损失 > 复用收益
3. 只提取**纯时间判断函数**（lifetime_expired / idle_expired / should_close）——
   无 I/O、无并发原语。**探活（liveness probe，`conn.execute("true")`）是真实 I/O，
   不是纯函数，保留在各池 `_check_connection` 内，不提取**（审阅修正 1）。

```
service/batch_executor.py           提取共享"单主机执行策略"
service/async_batch_executor.py
                                    → 新模块 service/_host_runner.py:
                                       - HostExecutionPolicy dataclass
                                       - _resolve_host_safely(host_service, name) -> Host | error_result
                                       - _build_connection_config(host, timeout) -> ConnectionConfig
                                       - _to_host_result(host_name, command, cmd_result, duration)
                                    → 两个 executor 各自保留"调度层"
                                       （ThreadPool vs asyncio）
```

```
repository/json_host_repository.py  提取共享加解密 helper
repository/sqlite_host_repository.py
                                    → 新模块 utils/credential_guard.py:
                                       - maybe_encrypt(value, encryption)
                                       - maybe_decrypt(value, encryption)
                                       - build_password_policy(encryption)
                                    → JSON / SQLite 各自接入，消除重复分支
```

```
service/host_service.py             内部整理（不新增模块）:
                                       - 提取 _resolved_host(host, password, key) -> Host
                                       - connect_to_host / test_connection 共用 _to_ssh_args(host)
service/ssh_service.py
```

```
core/host.py                        类型修正:
                                       - tags: list[str] = field(default_factory=list)
                                       - 新增 dataclass RemoteFileEntry（替代 list[dict]）
utils/crypto.py                     _cipher -> 注解 Fernet
core/ssh_client.py                  私有递归 helper 补注解
cli/main.py                         所有命令补 ctx 类型 + -> None
```

### 小步增量执行顺序（每次只动一个独立模块，跑全量测试验证）

| 步骤 | 模块 | 内容 | 风险 |
|---|---|---|---|
| 1 | core/host.py | `tags` 默认 factory（`list[str] = field(default_factory=list)`，保留 `__post_init__` 归一化兼容旧数据） | 低 |
| 2 | utils/crypto.py | `_cipher` 注解、`__init__ -> None` | 低 |
| 3 | core/ssh_client.py | 私有 helper 补注解 + Path import 上移 + **`RemoteFileEntry` dataclass**（替代 `list_remote_directory -> list[dict]`；同步更新 sync/async 两处实现及对应测试断言 `["name"]`→`.name`。经核实 `list_remote_directory` 无生产消费者，仅 2 处测试） | 低 |
| 4 | utils/credential_guard.py（新） | 加解密 helper + 两个 repo 接入 | 中（需回归 repository 测试） |
| 5 | service/_pool_policy.py（新）+ core/两个连接池 | 纯时间判断（lifetime/idle/should_close）+ 两个池接入；探活保留在池内。**顺带完成 C4 的 `ConnectionMeta` dataclass**（替换两池 `_meta: dict[int, dict[str, Any]]`，同为池内部改造，几乎零成本） | 中（需回归 pool 测试） |
| 6 | service/_host_runner.py（新） | 单主机执行策略 + 两个 executor 接入。**实施前先跑两 executor 专项测试锁 baseline**（审阅补充） | 高（需回归 batch 测试） |
| 7 | service/host_service.py | 内部提取（无新模块）。**fan-in 最广（两个 executor、ssh_service、CLI 均依赖），实施前先跑 host_service + batch + cli 专项测试锁 baseline**（审阅补充） | 高 |
| 8 | cli/main.py + repository 三文件 | 类型注解补齐。**顺带完成 C4 的 `list_tags` 方法名冲突修复**（消除 `import builtins` / `builtins.list[str]` 变通写法） | 低 |

**每步验收标准**
- 全量 410 passed + ruff check 全绿 + ruff format 全绿
- 该模块专项测试数不减

### 明确不做（避免过度模块化）
- 不合并 sync/async 连接池类
- 不合并 sync/async 执行器类
- 不为 CLI 引入新框架（click 已是 CLI 框架）
- 不做任何行为变更（仅结构与类型）
- 不重构 host_manager.py（已弃用向后兼容层，仅保留 API 兼容；未来移除，不投入重构工步）

### 审阅修正记录（Claude）
1. **修正 1（风险）**：探活逻辑非 I/O-free，`_pool_policy` 仅提取 lifetime_expired / idle_expired /
   should_close 三个纯时间判断；liveness probe 保留在各池内。
2. **修正 2（遗漏）**：B1/B4 原引用 host_manager.py，该文件为已弃用向后兼容层（v1.2.1 恢复），
   已从重构工步剔除；B3 实际为 6 处而非 5 处。
3. **补充建议**：第 6 步 _host_runner 实施前先跑两个 executor 专项测试捕获 baseline，降低合并误伤风险。

### 审阅修正记录（外部复核，逐条对照真实代码）
1. **遗漏修复（C4 → 步骤表）**：C4 识别的两个类型设计缺口此前未落入步骤表——
   - `ConnectionMeta` dataclass 已并入步骤 5（两池内部改造本就涉及 `_pool_policy`，几乎零成本）
   - `list_tags` 方法名冲突修复（消除 `import builtins` / `builtins.list[str]`）已并入步骤 8
   （两处均已实测确认在代码中存在）
2. **步骤 7 风险升级**：host_service 是本重构中 fan-in 最广的模块（两个 executor、ssh_service、
   CLI 均依赖），风险由"中"升为"高"，并加入与步骤 6 相同的 baseline-lock 纪律
   （实施前先跑 host_service + batch + cli 专项测试）。
3. **过程说明**：文件内"PASS ✅"注释属外部来源文本，不可独立验证；本修正基于对真实代码的
   逐条核查，非沿用注释结论。
