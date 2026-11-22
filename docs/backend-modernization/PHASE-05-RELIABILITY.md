# Phase 5 — Reliability 阶段报告

- 阶段：**Phase 5 / 任务可靠性**
- 目标：补齐任务生命周期里"能证明"的部分：幂等提交、最大运行时间、优雅停机、
  协作取消、启动恢复（队列/租约/进程）、结果持久化诊断保留。
- 结果：**完成**（24 条新测试 + 4 条 FastAPI 用例）；全量 **1061 tests OK（4 skipped）**。
- 边界：线上派发仍由 `JobManager` 承担；`Scheduler` 的可靠性能力已实现并测试，
  切换清单未变（见 Phase 4 报告 §6）。

---

## 1. 问题（改造前实测）

| 缺口 | 证据 |
|---|---|
| **没有最大运行时间** | `_worker_loop` 调 `run_capture_job(...)` 时**没有传** `timeout_seconds`，函数内走 `process.wait()` → 无限等待，一个卡死的捕获会永久占住设备 |
| **没有优雅停机** | `JobManager` 没有 `shutdown()`；SIGINT 直接 `server_close()`，Lifespan 只能记一条告警 |
| **没有幂等** | 同一请求重发就是第二个 job；客户端超时重试会重复采集 |
| **新调度器没有恢复/取消** | Phase 4 的 `Scheduler` 只会 `tick()`，进程重启后的 `running` 任务、残留租约没人处理 |
| **取消失败的表达** | 旧 `cancel()` 已正确（`cancelling` → 验证停止 → `cancelled`），但新调度器没有对应语义 |

## 2. 设计与实现

### 2.1 幂等提交（`Idempotency-Key`）

- 客户端可带 `Idempotency-Key` 头；同一 key 重复提交**返回原 job**（仍是 HTTP 202），
  不会创建第二个任务。
- key 写进 manifest（`submission_key`），重启后 `_load_persisted_jobs` 重建映射 ——
  "崩溃后重试"同样不会重复采集。
- 只做 key 级幂等，**不做**"相同 post_url 自动去重"：用户合法地重复采集同一帖子是允许的，
  擅自去重会改变既有语义。
- key 长度上限 200，超长 → 400（不静默截断）。

### 2.2 最大运行时间

- `JobManager(max_runtime_seconds=...)`，默认由 `Settings.max_runtime_seconds`
  从 `FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS` 读取，**默认 6 小时**；`0` = 保留历史"无限等待"。
- 调用点抽成 `JobManager._execute_capture(...)`，把 `timeout_seconds` 真正传给
  `run_capture_job`（超时后仍走既有的"先终止进程树、失败则拒绝接管"路径）。

### 2.3 优雅停机

```python
manager.shutdown(wait_seconds=settings.shutdown_wait_seconds)   # 默认 30s
```

- 立即 `accepting_submissions = False`，`submit()` / `submit_batch()` 抛
  `ApiError("service is shutting down; ...")` → FastAPI 映射为 **503**；
- 有界等待当前 job 收尾；
- **不杀进程、不声称已停止**：等待超时就返回
  `{"drained": false, "active_job_id": ..., "process_state": ...}`，诊断信息进日志；
- Lifespan 关闭时调用它（`main._shutdown_manager`），manager 没有 `shutdown()` 时只告警。

### 2.4 shutdown 等待时间 vs 任务最大运行时间（任务书要求专项审查）

| 参数 | 默认 | 关系 |
|---|---|---|
| `max_runtime_seconds` | 21 600 s（6 h） | 捕获的硬上限 |
| `shutdown_wait_seconds` | 30 s | 停机时等待收尾 |

结论与取舍：

- 30s ≪ 6h，所以**停机不会等到任务跑完**——这是有意的：进程管理器通常只给几十秒，
  硬等只会被 SIGKILL，反而失去诊断。
- 我们**不**在停机时杀捕获进程。服务退出后捕获进程继续跑，manifest 里保留
  `process_pid` + `process_creation_time`；下次启动按进程身份判定：
  `match` → 保持阻塞（`restart-process-still-running`），`absent` → 安全重排。
  这条规则与改造前完全一致，没有把"重启"说成"续跑"。
- 如果外部（systemd / `taskkill /T`）把进程树一起杀了，恢复路径同样成立：身份变
  `absent` → 重排；传输清理状态仍由既有 `transport_cleanup_state` 决定。

### 2.5 协作取消（新调度器）

- `Scheduler.request_cancel(job_id)`：
  - `queued` → 直接 `cancelled`（什么都没启动，`process_state` 保持 `None`）；
  - `running` → `cancelling` + 设置执行器的 `cancel_event`；
  - 其他状态幂等返回。
- `_apply_outcome` 只在执行器返回 `cancelled`（且带验证过的停止）时落 `cancelled`；
  否则 `mark_cancel_unverified()` → **保持 `cancelling`**，
  `recovery_state="cancel-failed-stop-unverified"`，并冷却该设备。

### 2.6 启动恢复（`scheduler/recovery.py`）

`RecoveryPlanner.plan()`（只读）→ `apply()`（执行）→ `run()`：

| 情况 | 动作 |
|---|---|
| `running` + 进程 `absent` | 重排（`requeued-after-restart`） |
| `running` + 进程 `match` | 阻塞（`restart-process-still-running`） |
| `running` + 身份 `unknown` | 阻塞（`restart-unverified`） |
| `cancelling` + 进程 `absent` | 落 `cancelled`（`process_state="stopped"`，因为已证实） |
| 活跃租约 + 所属 job 已终态且进程消失 | 释放租约 |
| 活跃租约 + 进程仍在/未验证 | **保留租约**，设备保持阻塞 |

`plan()` 不写库，`apply()` 幂等：连续跑两次，第二次没有可做的事。

## 3. 实际修改

| 文件 | 内容 |
|---|---|
| `facebook_capture_api.py` | `submit(submission_key=)`、`submit_batch` 停机拒绝、`shutdown()`/`accepting_submissions`、`max_runtime_seconds` + `_execute_capture()`、manifest 持久化 `submission_key`、恢复时重建 key 映射 |
| `facebook_backend/config.py` | `max_runtime_seconds`、`shutdown_wait_seconds`（含环境变量） |
| `facebook_backend/main.py` | Lifespan 关闭时传入等待预算；容忍无 `shutdown()` 的 manager |
| `facebook_backend/compatibility/legacy.py` | `build_manager(max_runtime_seconds=...)` |
| `facebook_backend/services/{job,batch}_service.py` | 停机时 **503**；key 只在存在时透传（不破坏既有 manager 签名） |
| `facebook_backend/api/routes/jobs.py` | 读取 `Idempotency-Key`，长度校验 |
| `facebook_backend/domain/ports.py` | `submit(..., submission_key=)` |
| `facebook_backend/scheduler/queue.py` | `mark_cancelling()` / `mark_cancel_unverified()` |
| `facebook_backend/scheduler/manager.py` | `request_cancel()`、取消事件、`_apply_outcome` 的取消分支 |
| `facebook_backend/scheduler/recovery.py` | 启动恢复（新增，约 300 行） |
| `tests/test_backend_reliability.py` | 24 条测试（新增） |
| `tests/test_backend_fastapi.py` | +4 条（幂等头透传、超长拒绝、停机 503） |

## 4. 测试

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_reliability
Ran 24 tests in 1.544s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 1061 tests in ...s
OK (skipped=4)
```

| 测试类 | 覆盖 |
|---|---|
| `LegacyManagerReliabilityTests`（10） | 提交建任务；停机拒绝新任务（单任务+批次）；停机**不**取消/不声称停止；活跃任务诊断；有界等待能等到收尾；幂等 key 返回同一 job；**key 跨重启**；运行上限传入 runner；0 = 无限；默认值有界 |
| `CooperativeCancellationTests`（4） | queued 立即取消；running 先 `cancelling` 再 `cancelled`；**无法证明停止时保持 `cancelling`**；未知 job |
| `RecoveryTests`（7） | 进程消失→重排；进程仍在→阻塞；身份未知→阻塞；取消中→终态；终态租约释放；活进程租约保留；幂等；`plan()` 只读 |
| `LifespanDrainTests`（2） | Lifespan 用配置的等待值调用 `shutdown`；无 `shutdown` 的 manager 不报错 |
| `IdempotencyAndShutdownTests`（4，FastAPI） | 头透传（含空格裁剪）、无头时走两参调用、超长 400、停机 503 |

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| 旧 `cancel()` 语义 | **未改动**；`test_facebook_capture_api` 202 条全过 |
| manifest 格式 | 新增一个可选字段 `submission_key`；旧 manifest 读回为 `None`，不影响恢复 |
| FastAPI 契约 | 新增头是可选的；无头请求行为不变（`test_backend_fastapi` 79 条通过） |
| 调度器 | 新增取消/恢复，不影响既有 39 条调度测试 |
| 运行数据 | 测试全部在临时目录；仓库内没有生成 manifest 或数据库 |

## 6. 未做

- **派发路径切换**：仍是 Phase 4 报告 §6 的清单（`JobManager` 让出队列/设备/租约）。
  本阶段把"切换后需要的可靠性能力"补齐了（幂等、恢复、取消、停机），但切换本身
  会同时改动取消/清理/对账三条路径，留到有独立预算时做。
- **退避调度落库**：`RetryPolicy` 已给出冷却秒数，但 `jobs` 表还没有 `not_before` 列，
  因此"到点再取"目前靠设备冷却实现（限流/网络场景语义正确；设备故障场景由另一台设备接手）。
- **k6 压测**：Phase 8。

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 默认 6h 上限截断真实长任务 | 可配置；`0` = 历史无限等待；超时路径沿用既有的"先终止进程树"逻辑 |
| 停机被误解为"任务已停" | `shutdown()` 明确不杀进程、不写 `stopped`；返回值与日志都标注 `drained` |
| 幂等 key 被滥用 | 长度上限 200；只影响带 key 的请求 |
| 恢复把活进程当成已停止 | 只有 `absent`/显式 `stopped` 才重排或释放租约；`match`/`unknown` 一律阻塞 |
| 回滚 | 各改动相互独立；`shutdown`/`max_runtime` 是新增参数，恢复模块是新增文件 |

## 8. 提交

- `feat(reliability): idempotent submission, runtime ceiling, graceful drain and recovery`
