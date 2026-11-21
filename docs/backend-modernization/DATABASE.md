# 数据库与迁移说明

- 引擎：SQLite（SQLAlchemy 2.0 + Alembic），可平滑迁移到 PostgreSQL
- 当前版本：`0002_job_pinning`
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

## 2. 创建与升级

```powershell
# 新库（生产/本地都走这条，不要用 create_all）
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///C:/data/facebook_backend.db"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current        # 0002_job_pinning (head)

# 回滚一个版本
.venv\Scripts\python.exe -m alembic downgrade -1
```

- URL 解析顺序：`FACEBOOK_DB_URL` → `alembic.ini` 的 `sqlalchemy.url` → `data/facebook_backend.db`。
- `env.py` 开了 `render_as_batch=True`：SQLite 不能原地 ALTER，后续迁移必须能在 batch 模式下跑。
- `data/` 在 `.gitignore` 里：数据库是机器状态，不入库。

## 3. SQLite 运行参数（每个连接都会设置）

```
PRAGMA journal_mode=WAL;      -- 读写不互斥
PRAGMA foreign_keys=ON;       -- SQLite 默认关闭
PRAGMA busy_timeout=30000;    -- 有界等待，而不是立刻 database is locked
PRAGMA synchronous=NORMAL;    -- WAL 下正确的持久性/速度平衡点
```

`check_same_thread=False` 是必需的（连接池会把连接交给别的线程），真正的隔离靠
"一个工作单元一个 Session"：`Database.session()` 成功提交、异常回滚、始终关闭。

## 4. 从 JSON manifest 迁移

```powershell
# 1) 先看（默认就是 dry-run，不写库、不备份）
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --dry-run

# 2) 服务必须停止：工具会探测 127.0.0.1:8765，还在响应就拒绝
# 3) 正式导入（先备份到 backups/manifest-import-<时间戳>/，再单事务写入）
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --apply --report migration-report.json
```

行为要点：

| 项 | 行为 |
|---|---|
| 幂等 | 已存在且内容一致 → `skipped`；不一致 → `conflicts`，**绝不覆盖** |
| 回滚 | 整个导入在一个事务里，异常全部回滚，数据库保持原样 |
| 进程身份 | `running`/`cancelling` 且进程 `match`/`unknown` 的任务导入后保持原状态并标记 `import-blocked-process-*`，**不标记可恢复** |
| 数量核对 | 事务内比对 `count()` 与"导入前 + 本次导入"，列出 `missing_jobs`/`missing_batches` |
| 产物核对 | 用 `checked_artifact_path` 校验路径边界，记录 size 与 sha256 |
| 顺序 | 先建 batch 再建 job（`jobs.batch_id` 是外键），最后写 `batch_jobs` 关系 |
| 退出码 | `0` = 全部 OK；`2` = 需要人工关注（无效 manifest、孤儿批次、冲突） |

真实语料 dry-run（复制到临时目录执行，未动仓库数据）：743 个文件里 668 个是 job
manifest（其余 73 个 `.log`、2 个连字符命名的文件本来就不匹配旧加载器的 glob），
69 个批次、112 个产物，0 无效、0 孤儿。

**迁移完成前不要删除 `api-jobs/`、`api-batches/`。**

## 5. 当前边界（重要）

数据库层与迁移工具已完成并测试，但**运行时仍以 manifest 为准**（`JobManager`）。
因此现在不存在双写：数据库由显式离线导入填充。状态所有者的切换与调度器重建绑定，
清单见 `PHASE-04-SCHEDULER.md` §6。

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
