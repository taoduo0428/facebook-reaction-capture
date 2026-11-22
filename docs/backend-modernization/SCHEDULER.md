# Scheduler 说明（设备感知调度）

- 代码：`facebook_backend/scheduler/`
- 测试：`tests/test_backend_dispatch_decision.py` 覆盖实际派发决策，`tests/test_backend_scheduler.py` 覆盖独立调度器。
- v1.0.1 运行路径：`JobManager._worker_loop()` 调用 `Dispatcher.plan()` 判断当前 active worker 能否接单。队列消费、执行、结果持久化和主备接管仍由 `JobManager` 负责。下图与 §2–6 描述独立的 `Scheduler.tick()` 组件，线上接入情况见 §7。

---

## 1. 组成

```
Dispatcher（纯决策）   JobQueue（CAS 认领）   LeaseManager（互斥 + fencing）
        └───────────────┬──────────────┴───────────────────┘
                        ▼
                    Scheduler ──► ExecutorPort（Mock / LegacyCaptureExecutor）
                        │
                        └──► SQLite：jobs / job_events / devices / device_leases
```

| 模块 | 职责 |
|---|---|
| `models.py` | `Device`、`ExecutionSlot`、`WorkerHealth`、`WorkerAssignment`、`SchedulerSnapshot`（全 frozen） |
| `dispatcher.py` | "现在哪台设备能接哪个任务"：忙、冷却、活跃租约、禁用、资源冲突、并发上限 |
| `queue.py` | 持久队列：`UPDATE ... WHERE version=? AND status='queued'` 的 compare-and-swap 认领 |
| `leases.py` | 设备独占租约、`fencing_version`、**接管规则** |
| `failures.py` | 失败分类 + 重试策略 |
| `manager.py` | 组合以上，`tick()` / `snapshot()` / `request_cancel()` / `shutdown()` |
| `recovery.py` | 启动恢复：重排、取消终态、释放租约 |

## 2. 一次 tick 做什么

1. 刷新设备视图（配置 + 健康 + 槽位 + 冷却）；
2. `Dispatcher.plan()` 选出本次可用的设备（默认 `max_concurrent_jobs=1`，即串行）；
3. 每台设备用 CAS 认领一个 `queued` 任务（钉在该设备上的任务只有该设备能认领）；
4. 获取设备租约（拿不到就放回队列并冷却该设备）；
5. 调执行器，测量执行时长与排队时长；
6. 按结果落库：完成 / 重试（同设备或换设备）/ 失败 / 取消；释放租约。

## 3. 设备抽象

| 字段 | 含义 |
|---|---|
| `device_id` / `worker_id` | 设备标识（当前 1 Worker = 1 AVD） |
| `serial` | ADB serial，必须全局唯一 |
| `avd_name` | 模拟器名称 |
| `capture_port` / `native_port` | mitmproxy 与 native 端口，必须互不冲突 |
| `enabled` | 配置开关 |
| `health_status` / `boot_completed` | 最近一次 ADB 观测 |
| `current_job_id` | 当前槽位占用的任务 |
| `cooldown_until` | 冷却截止（限流/失败后） |

## 4. 租约与接管（最关键的一条）

```python
takeover_allowed = 租约已过期 AND 进程已确认停止 AND 传输清理已确认
```

- **过期只是必要条件**。三者缺一 → `LeaseHeldError`，设备保持阻塞。
- 数据库用**部分唯一索引**保证一台设备只有一个活跃租约，不依赖应用约定。
- 每次重新获取 `fencing_version + 1`，可用于识别"第几次占用"。

## 5. 失败分类与重试

| 分类 | 触发 | 重试 | 换设备 |
|---|---|---|---|
| `rate-limit` | 429 / rate limit | 是 | **否**（任务钉在原设备，冷却后同一设备继续） |
| `device` | offline / not found / unauthorized | 是 | 是 |
| `network` | connection / proxy / dns / ssl | 是（退避 30/120/300s） | 否 |
| `executor` | 非零退出 / 崩溃 | 是（未超次数） | 是 |
| `timeout` | 捕获超时 | **否**（必须先验证停止） | — |
| `cancelled` | 操作者取消 | **否** | — |
| `unknown` | 其它 | 否 | — |

"限流不换账号/换设备"落库为 `jobs.pinned_device_id`，重启后依然有效。

## 6. 启动恢复

| 情况 | 动作 |
|---|---|
| `running` + 进程 `absent` | 重排（`requeued-after-restart`） |
| `running` + `match` | 阻塞（`restart-process-still-running`） |
| `running` + `unknown` | 阻塞（`restart-unverified`） |
| `cancelling` + `absent` | 落 `cancelled` |
| 活跃租约 + 所属任务已终态且进程消失 | 释放租约 |
| 活跃租约 + 进程仍在/未验证 | **保留**，设备继续占住 |

`plan()` 只读，`apply()` 幂等。

## 7. 线上接入现状

**已经接管**：派发决策与设备互斥。`JobManager._dispatch_plan_locked()` 每轮把运行态映射成
`Device` 快照（enabled / current_job_id / cooldown_until），连同"未验证的捕获租约"
（`active_leases={active: uncertain}`）交给 `Dispatcher.plan()`；阻塞原因
（`cooldown` / `busy` / `disabled` / `lease-held`）由调度器给出，并有 10 条测试直接断言。
主备切换仍由 `_activate_next_worker_locked` 负责，所以只把活跃 Worker 作为候选。

**尚未接管**：执行与结果落库。`_worker_loop` 仍调用既有的 `_assign_job_to_worker` /
`_execute_capture` / 结果物化 / 故障恢复路径——这部分被 200+ 条既有测试钉死，
重接的收益（当前串行、单设备下行为完全一致）远小于风险。多进程 `Scheduler.tick()`
循环保留，供跨进程协调启用时使用。

切换清单（剩余部分）：

1. `submit()` 改为写 `jobs` 表（`JobQueue`），manifest 退化为导出；
2. `_worker_loop` 替换为 `Scheduler.tick()`，`_assign_job_to_worker` 的字段映射搬进 `_run_one`；
3. 取消/清理/对账改走 `JobQueue + LeaseManager` 状态机（保持 `cancelling` → 验证停止 → `cancelled`）；
4. 恢复改读 `JobRepository` + `process_manager.identity_state`，规则不变；
5. 门禁：同一 job 不会被两台设备同时执行（CAS + 租约已由本阶段测试证明）。

## 8. 运维要点

- **默认串行**：`max_concurrent_jobs=1`。多设备并行必须先用 `tests/test_backend_scheduler.py`
  的隔离用例验证，再逐台放开。
- **多设备并行的收益在采集侧**：编排吞吐实测串行 96.6 jobs/s、2 设备并行 72.4 jobs/s
  （MockExecutor，SQLite 写入串行化）。真实采集是分钟级，编排开销可忽略。
- **冷却不是故障**：限流只冷却设备，不换设备、不换账号。
- **健康检查**：`GET /health` 里 `capture_lease.state=uncertain` 表示有未确认的进程/清理，
  此时不会派发新任务。

## 9. 自检

```powershell
.venv\Scripts\python.exe -m unittest tests.test_backend_scheduler -v
.venv\Scripts\python.exe perf\scheduler_bench.py --jobs 200 --devices 1
```
