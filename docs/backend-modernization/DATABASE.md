# 数据库与迁移说明

- 引擎：SQLite（SQLAlchemy 2.0 + Alembic），可平滑迁移到 PostgreSQL
- 当前版本：`0004_job_event_context`
- 代码：`facebook_backend/persistence/`、`facebook_backend/persistence/migrations/`

---

## 1. 表结构

| 表 | 关键列 | 约束 |
|---|---|---|
| `jobs` | `id`、`status`、`payload`(JSON)、`result`(JSON)、`version`、`assigned_worker_id`、`device_serial`、`pinned_device_id`、`process_pid`、`process_creation_time`、`process_state`、`transport_cleanup_state`、`recovery_state`、`batch_id`、`batch_index` | `version` 乐观锁；`batch_id` → `batches.id ON DELETE SET NULL`；索引：status、created_at+status、batch_id+batch_index、pinned_device_id |
| `batches` | `id`、`status`、`total_jobs`、`completed_jobs`、`failed_jobs`、`cancelled_jobs`、`run_dir`、`completion_scope` | 计数由 `BatchRepository.recompute_counts` 在同一事务内重算 |
| `batch_jobs` | `batch_id`、`job_id`、`position` | PK(batch_id, job_id)；`UNIQUE(batch_id, position)` |
| `job_attempts` | `attempt_id`、`job_id`、`attempt_index`、`worker_id`、`device_serial`、`process_id`、`process_creation_time`、`exit_code`、`result_status` | `UNIQUE(job_id, attempt_index)` |
| `job_events` | `event_id`、`job_id`、`batch_id`、`event_type`、`timestamp`、`payload`、`trace_id` | 追加式：应用不 update/delete |
| `devices` | `id`、`worker_id`、`serial`(UNIQUE)、`avd_name`、`capture_port`、`native_port`、`enabled`、`health_status`、`current_job_id`、`cooldown_until` | 配置仍以 `facebook_workers.json` 为准，这里存身份与观测状态 |
| `device_leases` | `device_id`、`job_id`、`lease_token`(UNIQUE)、`fencing_version`、`acquired_at`、`heartbeat_at`、`expires_at`、`released_at` | **部分唯一索引** `uq_device_leases_active_device (device_id) WHERE released_at IS NULL` |
| `artifacts` | `artifact_id`、`job_id`、`artifact_name`、`artifact_path`、`size`、`sha256`、`content_type`、`verification_status` | `UNIQUE(job_id, artifact_name)` |

时间戳统一是 ISO-8601 UTC 字符串（`2026-11-21T08:00:00Z`），与 manifest/API 一致；
SQLite 没有时区类型，存规范字符串既能排序又能与旧数据逐字节兼容。

## 2. 初始化、连接与事务

发行分支的新安装使用 `FACEBOOK_JOB_STORE=auto`。默认位置是项目根下的 `data/facebook_backend.db`。只有全新的默认库、空安装且没有旧清单时，启动器才会初始化并迁移。显式 `database`、自定义数据库路径和已有数据库均要求 schema 已迁移且与代码匹配。应用验证 Alembic 版本、表、主键、索引、外键和 SQLite 一致性。终端中运行 `python -m alembic heads` 读取本版本期望的 head。

使用 `FACEBOOK_DB_URL` 选择 SQLite 文件。每个连接开启 WAL、foreign_keys、30 秒 busy_timeout 与 synchronous=NORMAL。每个工作单元独立创建并关闭 Session。状态更新、尝试、租约及对应事件各由负责该操作的写事务提交。批次受理把父行、每个子任务、位置关系和创建事件放进一笔事务；任意子项写入失败时整批回滚。

## 3. 迁移既有安装

以下命令在**已停服务的安装根目录**运行。先保全当前状态，确认旧进程与传输清理证据，核对目标数据库的绝对路径。给已有 SQLite 做一致性备份时应使用 SQLite backup API；直接复制打开中的 `.db` 可能遗漏 WAL 中的提交。原始 JSON、运行目录及账号配置保留在现场。

```powershell
$env:FACEBOOK_DB_URL = 'sqlite+pysqlite:///C:/data/facebook_backend.db'
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --dry-run
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --apply --backup --report migration-apply-001.json
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --verify --report migration-verify-001.json
```

`--dry-run` 只统计源记录与问题。`--apply` 接受独立新备份目录，默认在 `backups/` 创建带时间戳的目录。持有状态所有者锁、确认服务停止后才进入写事务。孤儿、重复成员、顺序冲突、同 ID 不同内容、未决进程或租约使操作失败。成功时读回数据写入切换收据。`--verify` 只读比对源与目标记录，包括请求、错误、幂等键、进程信息、批次关系和产物摘要。退出码 0 表示通过；2 表示校验或迁移未通过；1 表示命令外围错误。每次报告使用新文件名。

新安装启动前核对切换收据及历史清单摘要，已迁入的标识要在库内存在。上线后的任务以 SQLite 中的新状态为准。状态根和数据库文件使用进程级互斥，API、兼容入口和离线工具协调占用。若状态目录仍有历史清单但不存在有效收据，按上述流程复核。真实数据需要操作者的停机窗口。

## 4. 回退验证

在停机状态，从已核对的源库导出到**全新目录**。回退包包括一致的数据库副本、任务清单与批次清单。工具仅在整个包验证成功后交付目标目录。未决执行、未完成的传输恢复、失配的结果、活动租约，或 v1 清单无法表达的设备绑定会阻止回退。

```powershell
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --rollback --rollback-dir C:/data/rollback-001 --report rollback-check-001.json
```

将导出的清单、对应的原始运行证据以及配置放到独立恢复安装里，用 `FACEBOOK_JOB_STORE=manifest` 做只读核对。在任务 ID、终态、批次顺序、完成范围与关键产物均核对后，仅启动一份执行服务。脚本不会自动切换当前实例的状态所有者。

## 5. 存储维护边界

原始业务证据按文件保存。离线导入时得到的文件摘要只描述读到的内容；对缺少可信历史基线的产物保留 `unknown`。SQLite 归档应在停机窗口保留整库快照和对应产物路径。对仍有租约、活动尝试或未完成迁移/回退的记录保持原样。

## 6. 迁移到 PostgreSQL 时需要什么

1. 把 `pysqlite` URL 换成 `postgresql+psycopg`，`connect_args` 与 PRAGMA 监听只在 sqlite 分支生效；
2. `sqlite_where=` 的部分唯一索引改成 PostgreSQL 的 `postgresql_where=`；
3. `AUTOINCREMENT` 列改为 `SERIAL/IDENTITY`（Alembic batch 模式会自动处理）；
4. `JobQueue.claim` 的 compare-and-swap 语义不变，可再加 `SELECT ... FOR UPDATE SKIP LOCKED` 提升并发；
5. 现有 SQL 没有方言专有函数，`TEXT/INTEGER/FLOAT` 直接映射。

## 7. 自检

```powershell
# 迁移产物与模型定义逐表比对（漂移即失败）
.venv\Scripts\python.exe -m unittest tests.test_backend_persistence -v
```
