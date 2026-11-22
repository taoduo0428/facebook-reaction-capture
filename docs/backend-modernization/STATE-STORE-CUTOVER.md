# 状态所有者切换（manifest → SQLite）

> 本页保留 v1.0 状态存储接入时的历史行为。当前操作以[数据库迁移和独立回退流程](DATABASE.md)为准。

- 代码：`facebook_backend/persistence/job_store.py`、`manifest_export.py`
- 测试：`tests/test_backend_job_store.py`（22 条）
- 开关：`FACEBOOK_JOB_STORE=manifest|database`（默认 `manifest`）
- 结果：**两种 store 行为等价**，切换与回退都有实测路径

---

## 1. 问题

Phase 3 建好了库、仓库层和离线导入工具，但运行时仍以 JSON manifest 为权威状态。
任务书要求"迁移后 SQLite 成为任务业务状态的唯一权威来源"，而直接改 `JobManager`
的持久化代码风险极高：它同时承载恢复、取消、进程身份、租约与产物校验，
任何一处写错都会破坏已验证的行为。

## 2. 设计：把"存哪"与"存什么"分开

`JobManager` 只依赖四个方法：

```python
store.load_job_manifests()   # -> Iterator[(原始文档, 期望 id)]
store.save_job(job)          # 整份任务字典
store.load_batch_manifests()
store.save_batch(batch)
```

| 实现 | 落点 | 保证 |
|---|---|---|
| `ManifestJobStore` | `api-jobs/<id>.job.json`、`api-batches/<id>.batch.json` | 字段集与顺序与历史完全一致（不含 `result`、不含 `pinned_device_id`），原子替换 |
| `DatabaseJobStore` | `jobs` / `batches` / `batch_jobs` | 有列的字段进列，其余进 `jobs.payload`；`save_job` 是 upsert |

关键点：

- **恢复逻辑一行未改**。`_load_persisted_jobs` 只是把"遍历文件"换成"遍历 store 给的
  `(raw, expected_id)`"，进程身份判定、`cancelling` 处理、租约清理全部原样保留。
- **测试的注入点不变**。既有测试大量 `mock.patch.object(manager, "_persist_job", ...)`
  来模拟磁盘失败；`_persist_job` 仍是唯一写入口，只是内部改为 `self.store.save_job(job)`。
- **批次的写入顺序被显式处理**。旧流程先写子任务、后写批次清单（批次清单是"提交完成"
  的标记），而 `jobs.batch_id` 有外键。数据库实现因此在写子任务前先确保批次行存在
  （只填 id/status/created_at），随后 `save_batch` 再补 `run_dir`/`completion_scope`
  并建立 `batch_jobs` 关系——语义与旧流程一致，外键也满足。
- **失败要响**。库没迁移时启动直接失败并提示 `alembic upgrade head`，不会半可用地跑。

## 3. 切换与回退

```powershell
# 切到数据库
$env:FACEBOOK_DB_URL    = "sqlite+pysqlite:///C:/data/facebook_backend.db"
$env:FACEBOOK_JOB_STORE = "database"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --apply
.\start_facebook_capture_api.ps1

# 回退（先把库导回 manifest，否则切换后新建的任务会丢）
.venv\Scripts\python.exe -m facebook_backend.persistence.manifest_export --apply
Remove-Item Env:\FACEBOOK_JOB_STORE
```

`manifest_export` 默认 dry-run、默认**不覆盖**已存在的 manifest（跳过项列在
`skipped_jobs`/`skipped_batches`），要覆盖需显式 `--overwrite`。

## 4. 验证（真实跑过）

| 断言 | 测试 |
|---|---|
| manifest 字段集/顺序与历史一致 | `test_manifest_payload_keeps_the_historical_field_set` |
| 两种 store 的路径与旧 helper 一致 | `test_paths_match_the_legacy_helpers` |
| 数据库往返保留每个恢复需要的字段（PID、创建时间、传输清理、attempt_dirs、checkpoint、submission_key） | `test_round_trip_preserves_every_field` |
| 保存是 upsert，重复保存不产生第二行 | `test_save_is_an_upsert` |
| 错误对象以 JSON 往返 | `test_error_is_stored_as_json_and_read_back` |
| 批次关系保序 | `test_batch_relations_round_trip_in_order` |
| 引用不存在的任务会被拒绝 | `test_batch_with_unknown_job_is_rejected` |
| **JobManager 用数据库 store**：提交只写库、不写 manifest | `test_submit_writes_the_database_and_no_manifest` |
| **重启后从库恢复任务** | `test_restart_restores_jobs_from_the_database` |
| **取消写回库并可再次读出** | `test_cancel_updates_the_database` |
| **批次提交持久化关系并可恢复** | `test_batch_submission_persists_relations` |
| 未迁移的库启动即失败且提示可执行 | `test_unmigrated_database_fails_with_an_actionable_message` |
| 导出可逆（dry-run 不写、apply 可往返、默认不覆盖） | `ManifestExportTests`（3 条） |

## 5. 仍未做（如实说明）

- **默认值仍是 `manifest`**：切换需要操作者在停机、导入、核对之后显式打开。
  这是任务书"迁移必须在服务停止、旧执行进程状态确认之后进行"的直接落实，
  而不是技术上做不到。
- **调度器尚未接管派发**：本轮解决的是"状态存哪"，"谁来派发"仍在 `JobManager`
  的主备串行循环里（切换清单见 `PHASE-04-SCHEDULER.md` §6）。
- **数据库模式下没有并发的第二写者**：`save_job` 是普通 upsert，不向调用方要版本号；
  单进程是当前架构的前提。
