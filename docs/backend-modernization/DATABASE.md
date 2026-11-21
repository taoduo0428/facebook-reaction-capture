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

## 5. 状态所有者：manifest ↔ 数据库

运行时状态由一个 **store** 拥有，而不是一种文件格式。`JobManager` 只调用四个方法：

```python
store.load_job_manifests()    # -> (原始文档, 期望 id)
store.save_job(job)
store.load_batch_manifests()
store.save_batch(batch)
```

| 实现 | 位置 | 说明 |
|---|---|---|
| `ManifestJobStore` | `api-jobs/*.job.json`、`api-batches/*.batch.json` | 默认；字段集与顺序和历史完全一致（不含 `result`、不含 `pinned_device_id`） |
| `DatabaseJobStore` | `jobs` / `batches` / `batch_jobs` | 完整字典放 `payload` + 有列字段各归其列；`save_job` 是 upsert |

切换开关：

```powershell
$env:FACEBOOK_JOB_STORE = "database"     # 默认 "manifest"
$env:FACEBOOK_DB_URL    = "sqlite+pysqlite:///C:/data/facebook_backend.db"
```

**库没迁移会直接启动失败**，并给出可执行提示（`run alembic upgrade head ...`），
不会半可用地跑起来。

### 切换步骤（必须按顺序）

```powershell
# 1) 停服务（导入工具会探测 8765，还在响应就拒绝）
# 2) 建库/迁移
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///C:/data/facebook_backend.db"
.venv\Scripts\python.exe -m alembic upgrade head

# 3) 离线导入现有 manifest（先 dry-run）
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --dry-run
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --apply --report migration-report.json

# 4) 切状态所有者并启动
$env:FACEBOOK_JOB_STORE = "database"
.\start_facebook_capture_api.ps1

# 5) 验证
curl.exe http://127.0.0.1:8765/v1/jobs/latest
# 提交一个任务后确认库里有行：
#   sqlite3 data\facebook_backend.db "select id,status,version from jobs order by created_at desc limit 3;"
```

### 回退（database → manifest）

```powershell
# 先把库里的状态导出成 manifest（否则切换后新建的任务会丢）
.venv\Scripts\python.exe -m facebook_backend.persistence.manifest_export --dry-run
.venv\Scripts\python.exe -m facebook_backend.persistence.manifest_export --apply
# 再切回默认 store
Remove-Item Env:\FACEBOOK_JOB_STORE
```

导出**默认不覆盖**已存在的 manifest（会列进 `skipped_jobs`）；确实要覆盖加 `--overwrite`。

### 边界

- 数据库模式下**不再写 manifest**，因此不存在双写；`api-jobs/` 里旧文件原样保留，
  这正是回退的依据。
- 恢复逻辑（进程身份、租约、取消）对两种 store 完全相同：它只读字典、只调 `save_*`。
- 同一时刻只应有一个进程使用该库（API 自己拥有状态），`save_job` 因此是普通 upsert，
  不向调用方要版本号。

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
