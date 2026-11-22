# Phase 3 — SQLite + SQLAlchemy + Alembic 阶段报告

- 阶段：**Phase 3 / 数据持久化**
- 目标：把"JSON manifest 是唯一状态"升级为"SQLite 是任务业务状态的权威来源"，
  并提供**可验证、可回滚、幂等**的离线迁移工具。
- 结果：**完成**（数据层 + 迁移工具 + 38 条测试）。全量 **994 tests OK（4 skipped）**。

> 范围说明（必须先读）：本阶段交付**数据库层与离线导入工具**；
> `JobManager` 仍以 manifest 为准运行。原因见 §6，这是有意的排序，不是遗漏。

---

## 1. 问题

| 现状 | 风险 |
|---|---|
| 每个 job/batch 一个 JSON 文件（本机 743 + 69 个） | 没有跨记录事务：batch 与子 job 分文件写，只能靠"未提交子任务"检查兜底 |
| 没有唯一约束 | 重复提交、重复 job_id 只能靠代码判断 |
| 没有并发控制 | 两个写入者后写覆盖先写，没有任何检测 |
| 查询靠遍历内存 dict | 历史增长后 `/v1/jobs/latest`、批次统计成本线性上升 |
| 没有事件历史 | 状态变更只留最终值，无法回答"什么时候、为什么变成 failed" |
| 产物只有"存在且非空" | 没有 sha256，无法证明下载到的就是当时验证过的文件 |

## 2. 设计

### 2.1 表结构（8 张 + alembic_version）

| 表 | 关键列 | 说明 |
|---|---|---|
| `jobs` | `id`、`status`、`payload`、`result`、`version`、`assigned_worker_id`、`process_pid`/`process_creation_time`、`batch_id` | `payload`/`result` 是 JSON TEXT；`version` 是乐观锁 |
| `batches` | `id`、`status`、`total_jobs`/`completed_jobs`/`failed_jobs`/`cancelled_jobs` | 计数由 `recompute_counts` 在同一事务内重算 |
| `batch_jobs` | `(batch_id, job_id)` PK、`position` | `UNIQUE(batch_id, position)` |
| `job_attempts` | `attempt_id`、`job_id`、`attempt_index`、`process_id`、`process_creation_time`、`exit_code`、`result_status` | `UNIQUE(job_id, attempt_index)`；每次重试/接管一行 |
| `job_events` | `event_id`、`event_type`、`timestamp`、`payload`、`trace_id` | **追加式**，应用不 update/delete |
| `devices` | `id`、`worker_id`、`serial`（UNIQUE）、端口、`health_status`、`cooldown_until` | 配置仍以 `facebook_workers.json` 为准，这里存身份与观测状态 |
| `device_leases` | `device_id`、`lease_token`（UNIQUE）、`fencing_version`、`expires_at`、`released_at` | **部分唯一索引** `WHERE released_at IS NULL` 保证一台设备只有一个活跃租约 |
| `artifacts` | `job_id`+`artifact_name`（UNIQUE）、`artifact_path`、`size`、`sha256`、`verification_status` | 只存元数据；大文件留在文件系统 |

设计取舍：

- **时间戳用 ISO-8601 UTC 字符串**（与 manifest/API 完全一致）。SQLite 没有时区类型，
  存规范字符串既避免 tz 丢失，又能按字典序排序、与既有数据逐字节兼容。
- **`version` 乐观锁**：所有状态写入都是 `UPDATE ... WHERE id=? AND version=?`，
  `rowcount == 0` 抛 `ConcurrentUpdateError`，绝不静默覆盖。
- **租约过期 ≠ 可接管**：数据库只保证"同一设备只有一个活跃租约"；接管仍需
  进程身份校验 + 传输清理确认（Phase 4/5）。

### 2.2 SQLite 配置

`database.py` 在**每个连接**上执行：

```sql
PRAGMA journal_mode=WAL;    -- 读写不互斥
PRAGMA foreign_keys=ON;     -- SQLite 默认是 OFF
PRAGMA busy_timeout=30000;  -- 有界等待，而不是立刻 "database is locked"
PRAGMA synchronous=NORMAL;  -- WAL 下正确的持久性/速度平衡点
```

`check_same_thread=False` 是必需的（连接池会把连接交给别的线程），
真正防串用靠的是**一个工作单元一个 Session**：

```python
with database.session() as session:   # commit on success / rollback on error / always close
    ...
```

### 2.3 迁移工具（`legacy_manifest_import.py`）

默认 **dry-run**，只有 `--apply` 才写库。

| 要求 | 实现 |
|---|---|
| dry-run | 默认模式，只扫描 + 出报告，不建备份、不写库 |
| 备份 | `backups/manifest-import-<时间戳>/api-jobs|api-batches`，`copy2` 复制；**原文件不删不改** |
| 幂等 | 已存在且内容一致 → `skipped`；不一致 → `conflicts`，**绝不覆盖** |
| 重复检测 | 同名不同 id、batch 内重复 job_id、同一 job_id 两个文件 |
| 数量核对 | 事务内 `count()` 与"导入前 + 本次导入"逐一比对，并列出 `missing_jobs`/`missing_batches` |
| Artifact 路径核对 | 用 `checked_artifact_path` 校验落在 run 目录内且非空，顺带算 sha256 |
| Process Identity 核对 | `process_manager.identity_state`：`match`/`unknown`/`mismatch` → 标记 blocked，**不改状态、不标记可恢复** |
| 服务停止校验 | 探测 `127.0.0.1:8765` 是否响应；响应则拒绝，`--force` 可覆盖并在报告里记 `forced_while_running` |
| 失败回滚 | 整个导入在**一个事务**里，异常 → 全回滚（测试用注入失败验证计数归零） |
| 迁移报告 | JSON（`--report`）+ 人读摘要；`ok` 为假时 CLI 退出码 2 |

补充规则：

- **先建 batch 再建 job**：`jobs.batch_id` 是外键，顺序不能反。
- job manifest 缺 `batch_id` 时，从 batch manifest **派生**并回填（旧 manifest 的常见形态）。
- job 的 `batch_id` 指向不存在/无效的 batch → 丢弃该外键、保留在 `payload.legacy_batch_id`，
  并在报告里记 `dropped job→batch links`。
- 有孤儿 batch 时 `ok=false`（旧加载器是静默跳过，迁移不能悄悄丢一个批次）。

## 3. 实际修改

| 文件 | 行数 | 内容 |
|---|---|---|
| `alembic.ini` | 60 | `script_location` 指向包内；URL 留空由 `env.py` 解析 |
| `persistence/models.py` | 384 | 8 张表 + 命名约定 + 部分唯一索引 |
| `persistence/database.py` | 160 | engine/pragma/Session 生命周期 |
| `persistence/repositories.py` | 800 | Job/Batch/Artifact/Event/Device/Lease 仓储 |
| `persistence/legacy_manifest_import.py` | 560 | 扫描 / 计划 / 备份 / 导入 / 报告 / CLI |
| `persistence/migrations/env.py` | 66 | 离线/在线迁移，`render_as_batch` |
| `persistence/migrations/script.py.mako` | 22 | 模板 |
| `persistence/migrations/versions/0001_initial.py` | 300 | 初始 schema（autogenerate 后人工整理） |
| `tests/test_backend_persistence.py` | 640 | 38 条测试 |

修改：`requirements.txt`（+SQLAlchemy 2.0.44、alembic 1.16.5）、
`.github/workflows/check.yml`（+3 个 backend 测试模块）、`tests/test_layout.py`（必跑清单）、
`.gitignore`（`/data/`、`__pycache__/`、`*.pyc`）、`facebook_capture_api.py`
（`_process_identity_state` 改为委托 `process_manager.identity_state`，补丁目标不变）。

## 4. 测试

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_persistence
Ran 38 tests in 2.272s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 994 tests in 134.669s
OK (skipped=4)
```

| 测试类 | 覆盖 |
|---|---|
| `SchemaAndMigrationTests` | `alembic upgrade head` 建全 8 表 + 版本号；**迁移与模型零漂移**（逐表比对列/索引/外键名）；四个 PRAGMA 生效；外键真的生效；一台设备只能一个活跃租约 |
| `SessionLifecycleTests` | 成功提交、异常回滚、单元结束即关闭、批次创建原子性 |
| `JobRepositoryTests` | 读写/latest/count；重复创建；**乐观锁拒绝陈旧写入**；attempt 往返；事件追加式 |
| `BatchRepositoryTests` | 关系写入、计数与状态重算、latest |
| `ArtifactRepositoryTests` | 同名 upsert 幂等、`UNIQUE(job_id, artifact_name)` |
| `DeviceLeaseTests` | 获取/心跳/释放、冲突、**fencing_version 递增**、**过期租约仍然阻止接管** |
| `LegacyImportTests` | dry-run 不写库、全量导入（job/batch/artifact/event）、幂等重跑、活进程/未验证进程被 blocked、进程已消失可安全恢复、无效 manifest、孤儿批次、缺失 batch 的 job、服务运行中拒绝、`--force`、备份、冲突不覆盖、失败全回滚、CLI 退出码 |

## 5. 真实语料 dry-run（不改动仓库数据）

把仓库里的 `api-jobs/`（743 个文件）与 `api-batches/`（69 个文件）**复制**到临时目录后跑
`--dry-run`（数据库也是临时文件）：

```
manifest jobs: 668  batches: 69
imported jobs: 668  skipped: 0  conflicts: 0
imported batches: 69  skipped: 0
artifacts: 112  events: 0
blocked jobs (not safe to resume): 0
invalid job manifests: 0  invalid batch manifests: 0  orphan batches: 0
result: OK
```

743 个文件里只有 **668** 个是 job manifest：另外 73 个是 `fb_*.log`，
还有 2 个是 `fb-linux-pending.job.json` / `fb-restored-error.job.json`（连字符命名）。
后两类同样**不匹配旧加载器的 `fb_*.job.json` glob**，所以迁移工具与线上行为一致，
不是漏导。69 个批次全部可导入，112 个产物在真实 run 目录中被找到并计算了 sha256。

## 6. 旧功能检查

| 检查项 | 结果 |
|---|---|
| `facebook_capture_api.py` 仍以 manifest 为准 | 未改持久化路径；`_persist_job`/`_load_persisted_jobs` 原样保留 |
| `_process_identity_state` | 改为委托，签名与返回值不变；既有 60 处 patch 目标仍生效（`test_backend_modularization` 专门覆盖） |
| 既有测试 | 994 条通过，没有删除或弱化 |
| 运行数据 | 测试全部使用临时目录；仓库内**没有**生成 `data/*.db` |

## 7. 未做：为什么现在不切换状态所有者

任务书要求"迁移后 SQLite 成为唯一权威来源，不允许长期双写"。本阶段的定位是**离线迁移**，
因此现在不存在双写：manifest 仍是运行时唯一来源，数据库由显式离线导入填充。

不在本阶段切换 `JobManager` 的原因：

1. `_load_persisted_jobs` 的 270 行恢复逻辑（进程身份、租约阻塞、批次未提交、传输清理）
   与**调度器**耦合；只换存储不换调度器，会制造两份互相矛盾的"谁在跑"判断。
2. Phase 4 要重写调度（队列、租约、主备接管、设备隔离）。状态与调度必须一起迁移，
   否则每个中间态都要写一遍适配层。
3. 现有 200+ 条测试断言 manifest 文件被写入（`_job_manifest_path` 等）。先切换会迫使
   大批测试改写，与"不破坏已验证能力"冲突。

Phase 4 的切换顺序（已定）：设备注册表 → 队列/租约 → 状态写入 → 恢复读取，
每一步都以"同一 job 不会被两个设备同时执行"为门槛。

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 迁移与模型漂移 | `test_migration_and_models_do_not_drift` 逐表比对，漂移即失败 |
| 迁移期间服务仍在跑 | 端口探测拒绝 + `--force` 留痕 |
| 迁移丢数据 | 备份 + 单事务回滚 + 数量/路径/进程身份核对 |
| 覆盖已有数据 | 内容不一致一律 `conflict`，不覆盖 |
| 回滚 | 数据库是新增文件，删除 `data/facebook_backend.db` 即回到纯 manifest 状态；manifest 未被修改 |

## 9. 提交

- `feat(persistence): SQLite schema, repositories and legacy manifest import`
