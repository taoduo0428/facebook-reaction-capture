# Phase 4 — Device-aware Scheduler 阶段报告

- 阶段：**Phase 4 / 设备感知调度**
- 目标：把"一个活跃 Worker + 其余备用 + 串行执行"升级为**设备感知**的调度组件：
  每台设备一条执行槽、数据库租约互斥、失败分类与重试策略、可并行（默认仍串行）。
- 结果：**调度组件完成并用 Mock Executor 验证**（39 条测试）；
  全量 **1033 tests OK（4 skipped）**。
- 边界：**尚未替换线上派发路径**（`JobManager` 仍是运行时调度器）。原因与切换清单见 §6。

---

## 1. 问题

| 现状 | 风险 |
|---|---|
| 调度决策散在 `JobManager._worker_loop` / `_assign_job_to_worker` | 设备可用性、冷却、租约、失败分类混在一个 5000 行文件里，无法单独测试 |
| 队列是 `queue.Queue`（内存） | 重启后只能靠 manifest 重建，"谁先谁后"没有持久依据 |
| 设备互斥靠单进程 + 单活跃 Worker | 跨进程/跨实例没有任何互斥保证 |
| 失败处理只有一条特例（`"rate-limit" in str(exc)` 走冷却） | Rate Limit / 设备故障 / 网络异常 / 执行器崩溃 没有分开的、可测的策略 |
| 租约只有内存标志 `_capture_lease_uncertain` | 没有 fencing token，无法证明"第几次占用" |

## 2. 设计

### 2.1 组件

```
Dispatcher（纯决策）        JobQueue（CAS 认领）      LeaseManager（互斥+fencing）
        └──────────────┬──────────────┴──────────────────────┘
                       ▼
                   Scheduler  ──►  ExecutorPort（Mock / Legacy）
                       │
                       └──►  SQLite（jobs / job_events / devices / device_leases）
```

| 模块 | 职责 |
|---|---|
| `scheduler/models.py` | `Device`、`ExecutionSlot`、`WorkerHealth`、`WorkerAssignment`、`SchedulerSnapshot`（全 frozen，纯值） |
| `scheduler/dispatcher.py` | 只回答"现在哪台设备可以接哪个任务"，不启动任何东西 |
| `scheduler/queue.py` | 持久队列：`SELECT queued → UPDATE ... WHERE version=?` 的 compare-and-swap 认领 |
| `scheduler/leases.py` | 设备独占租约 + fencing + **接管规则** |
| `scheduler/failures.py` | 失败分类 + 重试策略（显式、可测） |
| `scheduler/manager.py` | 组合以上 + 执行器，提供 `tick()` / `snapshot()` / `shutdown()` |
| `executors/base.py` | `ExecutorPort` / `ExecutionOutcome` 契约 |
| `executors/mock.py` | 脚本化执行器（测试用，不碰设备） |
| `executors/legacy.py` | 适配现有 `run_capture_job`（真实执行路径） |

### 2.2 十条隔离保证怎么落实

| # | 保证 | 落实方式 | 测试 |
|---|---|---|---|
| 1 | 同一设备同时只能跑一个任务 | 设备有槽位就不派发 + 数据库**部分唯一索引**（一个活跃租约） | `test_second_tick_does_not_restart_a_running_job` |
| 2 | 不同设备可独立执行 | 每设备一条 `ExecutionSlot`，并行 tick | `test_one_job_per_device_and_devices_run_independently` |
| 3 | 每台设备独立 ADB Serial | `WorkerConfig` 校验 serial 唯一 + `Device.conflicts_with` | `test_resource_conflict...` |
| 4 | Capture Port 不冲突 | 同上（端口唯一/交叉检查） | 同上 |
| 5 | Native Port 不冲突 | 同上 | 同上 |
| 6 | 运行目录隔离 | 每个 job 自带 `run_dir`；`attempt-NNN` 由执行器维护 | `test_claim_moves_the_oldest_job_to_running`（run_dir 断言） |
| 7 | 传输状态隔离 | 传输清理仍是执行器/`JobManager` 的既有逻辑，调度器不共享任何传输状态 | 既有 `test_facebook_cancel_transport` 等 |
| 8 | Process Identity 独立 | 租约记录 `job_id`/`worker_id`/`fencing_version`；进程身份仍由 `process_manager` 判定 | `test_expired_lease_without_evidence_is_not_taken_over` |
| 9 | Cleanup 不影响其他设备 | 清理按 job/设备执行，租约按设备释放 | `test_one_device_failure_does_not_affect_the_other` |
| 10 | 一台设备失败不影响其他设备 | 失败只冷却/释放该设备，其他槽位继续 | 同上 |

### 2.3 接管规则（本阶段最关键的一条）

```python
takeover_allowed(lease, evidence) =
    lease 已过期
    AND evidence.process_stopped      # 旧捕获进程已确认停止
    AND evidence.cleanup_confirmed    # 传输清理已确认完成
```

**过期只是必要条件**。三者缺一 → `LeaseHeldError`，设备保持阻塞。
测试逐条覆盖：未过期拒绝、过期但无证据拒绝、只有"进程停了"仍拒绝、三者齐备才接管且
`fencing_version + 1`。

### 2.4 失败分类与重试（显式策略，不再靠字符串特例）

| 分类 | 触发 | 重试 | 是否换设备 |
|---|---|---|---|
| `rate-limit` | 429 / rate limit 标记 | 是 | **否**（job 被 `pinned_device_id` 钉在该设备，冷却后同一设备继续） |
| `device` | offline / not found / unauthorized | 是（未超次数） | 是 |
| `network` | connection / proxy / dns / ssl | 是（指数退避） | 否 |
| `executor` | 非零退出 / 崩溃 | 是（未超次数） | 是 |
| `timeout` | 捕获超时 | **否**（必须先验证停止） | — |
| `cancelled` | 操作者取消 | **否** | — |
| `unknown` | 其它 | 否 | — |

"限流不换账号/换设备"是硬约束，因此新增了 `jobs.pinned_device_id` 列（迁移
`0002_job_pinning`）：限流后 job 只能被同一设备认领，重启后依然有效。

## 3. 实际修改

| 文件 | 行数 | 内容 |
|---|---|---|
| `scheduler/models.py` | 180 | 设备/槽位/健康/分配值对象 |
| `scheduler/dispatcher.py` | 100 | 派发决策（串行上限、冷却、租约、资源冲突） |
| `scheduler/queue.py` | 170 | CAS 认领、释放、完成、钉住/解钉 |
| `scheduler/leases.py` | 168 | 租约获取/心跳/释放 + 接管规则 |
| `scheduler/failures.py` | 124 | 失败分类 + 重试策略 |
| `scheduler/manager.py` | 422 | 调度器主体 + 设备表同步 + 事件记录 |
| `executors/{base,mock,legacy}.py` | 265 | 执行器契约 / Mock / 真实适配 |
| `persistence/migrations/versions/0002_job_pinning.py` | 30 | `jobs.pinned_device_id` |
| `tests/test_backend_scheduler.py` | 572 | 39 条测试 |

修改：`persistence/models.py`（+`pinned_device_id`）、`persistence/repositories.py`
（create/to_dict 支持该列）、`scheduler/__init__.py`（改为**不**急切导入子模块，避免
`executors ↔ scheduler` 循环导入）。

## 4. 测试

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_scheduler
Ran 39 tests in 2.284s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 1033 tests in ...s
OK (skipped=4)
```

| 测试类 | 覆盖 |
|---|---|
| `DispatcherTests`（8） | 串行默认、忙/冷却/租约/禁用/不健康跳过、端口冲突、空队列 |
| `QueueTests`（5） | 最老优先、钉住跳过/命中、释放保留或清除钉、**3 线程并发认领不重复** |
| `LeaseManagerTests`（4） | 有效租约拒绝、过期无证据拒绝、证据齐备接管且 fencing+1、心跳/释放 |
| `FailureClassificationTests`（4） | 标记识别、限流不换设备、超时/取消不重试、设备失败次数上限 |
| `SchedulerTests`（14） | 串行每 tick 一个、双设备并行互不干扰、限流钉住、冷却后同设备续跑、设备失败换设备、超时失败、取消终态、执行器崩溃重试、单设备故障隔离、shutdown、snapshot、事件、租约释放、运行中不重启 |
| `LegacyExecutorTests`（3） | 成功→completed+verified stop、失败→**不伪造** stopped、限流消息分类 |

全部使用 **Mock Executor + 真实 SQLite**，不依赖模拟器/ADB。

### 已知的既有 flaky 用例（本轮未修改）

一次全量运行中
`tests.test_facebook_navigation_latency.test_detail_reveal_requires_new_snapshot_and_keeps_budget_on_miss`
失败过一次：`sum(sleeps)=4013 > 4000`。

原因：该用例断言 `explore_facebook_readonly.ps1` 里 `Invoke-ReactionDetailReveal` 的等待预算，
而该函数记录的是"固定 250ms + 剩余预算"，剩余预算来自真实 `Get-Date` 差值。毫秒级抖动会让
总和比 4000 多出十几毫秒。

证据：单独运行 `tests.test_facebook_navigation_latency`（31 条）全通过；随后重跑全量
**1033 条全通过**。本轮**没有修改**该脚本或该测试 —— 这是既有的时间敏感用例，在高负载下
更容易触发。已记入 Phase 8 的测试稳定性清单（要么在 fixture 里固定 `Get-Date`，要么把
断言改成"预算 + 抖动上限"），本轮不动它以免削弱既有导航延迟保证。

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| `JobManager` 调度/取消/清理 | **未改动**；既有 200+ 条测试全部通过 |
| manifest 持久化 | 未改动 |
| 数据库 | 新增迁移 `0002`；`test_migration_and_models_do_not_drift` 覆盖列漂移 |
| FastAPI 契约 | 未改动；`tests.test_backend_fastapi` 通过 |
| 依赖 | 未新增第三方库 |

## 6. 未做：为什么还没替换线上派发

任务书要求"保留现有主备可靠性"和"多设备并发只在隔离验证后启用"。本阶段把
**调度能力**做成独立、可测的组件，但**没有**把它接成默认派发路径，因为：

1. `JobManager._worker_loop` 里除了派发，还绑着取消、传输清理、进程身份校验、
   结果持久化对账、failover 事件与 manifest 写入；一次性替换会同时改动 5 类语义。
2. 现有 200+ 条测试直接断言这些路径（`_assign_job_to_worker`、`_recover_after_worker_failure`、
   `_worker_runtime` 等）。先替换会让测试大面积重写，违背"不破坏已验证能力"。
3. 新的 `Scheduler` 需要 `JobManager` 让出"队列 + 设备状态 + 租约"三项所有权，
   否则会出现两个调度器同时派发——正是任务书禁止的双写。

**切换清单（Phase 5 执行）**：

1. `JobManager.submit()` 改为写 `jobs` 表（`JobQueue.enqueue`），manifest 只做导出。
2. `_worker_loop` 替换为 `Scheduler.tick()`，`_assign_job_to_worker` 的字段映射搬进
   `Scheduler._run_one`。
3. 取消/清理/对账改为通过 `JobQueue` + `LeaseManager` 的状态机（保持 `cancelling` →
   验证停止 → `cancelled` 的既有语义）。
4. 恢复逻辑从 manifest 读取改为 `JobRepository.list_by_status({"queued","running","cancelling"})`
   + `process_manager.identity_state` 判定，规则不变。
5. 门禁：同一 job 不会被两台设备同时执行（租约 + CAS 已由本阶段测试证明）。

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 新调度器与旧调度器同时跑 | 本阶段**未接线**，不可能同时派发 |
| 限流被误当成设备故障 | `FailureClass.RATE_LIMIT` 独立分支 + `pinned_device_id` + 测试 |
| 租约被"过期即接管" | `TakeoverEvidence` 强制两项确认 + 三条拒绝测试 |
| 伪造"进程已停" | 执行器只在确认退出码 0 时报告 `process_state="stopped"`；失败一律 `None` |
| 迁移漂移 | 已有漂移测试覆盖 0002 |
| 回滚 | 新模块是纯增量；删除 `scheduler/{manager,dispatcher,queue,leases,failures}.py` 与 `executors/` 即回到 Phase 3 状态 |

## 8. 提交

- `feat(scheduler): device-aware scheduler with leases, CAS queue and failure policy`
