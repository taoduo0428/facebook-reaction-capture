# Scheduler · 串行派发与恢复

原生 API 的数据库模式以 SQLite 的 `jobs`、`job_attempts`、`devices`、`device_leases` 和 `job_events` 协调一个执行槽。`JobManager` 提供 HTTP 门面、设备配置及内存读视图，创建 `ProductionScheduler` 作为数据库工作线程的目标。`put(job_id)` 仅设置唤醒事件，实际候选由 SQL 查询决定。[入口](../../facebook_capture_api.py#L2435) · [主循环](../../facebook_backend/scheduler/production.py#L100)

## 派发顺序

1. 仅选择当前 active worker。`Dispatcher.plan` 考察 enabled、busy、冷却及捕获保护。
2. `ProductionTransactions.claim` 取得 SQLite 写预留，先检查全局活动租约及未结算的执行。再按设备绑定和 `created_at, batch_id, batch_index, id` 顺序取单个 queued 任务。
3. 在同一事务把任务改为 running、登记本次 `attempt-NNN`、占用设备槽、取得新 token 和 fence，并追加 `job.claimed`。fence 是设备每次占用的代号，用来识别过时的写入。
4. `started` 在启动 runner 前再次匹配任务、attempt、worker、token、fence，消耗未启动凭据。取消若先到，保留 `not-started/not-required`。
5. `NativeAttempt.execute` 返回执行结果和资源清理证据。`finish` 核对归属、更新结果与批次计数、结束 attempt，并在同一事务释放租约。证据不足走 `hold`。[认领、启动与完成](../../facebook_backend/scheduler/production_transactions.py) · [执行适配](../../facebook_backend/scheduler/production_native.py)

## 失败后会发生什么

| 条件 | 对本次任务和下一任务的影响 |
|---|---|
| 进程或传输清理存疑 | 记录 `execution-unverified`，保留当前租约。全局设备槽保持阻塞。已发出的取消请求可保持 `cancelling` |
| 结果物化或结算写入失败 | 保留 `failed-result-persistence` 或原先已提交的 running/租约证据。确认原结果写入失败并满足原 attempt、停止和清理条件后，显式对账 |
| 已结算的限流 | 本任务失败，绑定原设备，保存原设备冷却截止时间。后续任务等待冷却，主备身份保持原样 |
| 已结算的网络错误 | `stopped/restored` 且尚有尝试预算时，绑定原设备，按退避时间重排。当前最多三次尝试，前两次失败对应 30 和 120 秒 |
| 已结算的设备故障 | 重复观察原设备异常，再观察顺位备用设备可用性。安全清理、预算、pin 和接管次数都允许时保存 checkpoint，最多跨设备重排一次 |
| 其余已结算的失败 | 记录失败。未知停止或传输状态仍优先进入保护 |

上述策略取自生产事务，不把假执行器的测试结果解释为原生异常一定能自动恢复。策略持久化和主备约束详见[生产策略](../../facebook_backend/scheduler/production_policy.py)、[调用位置](../../facebook_backend/scheduler/production.py#L149)及[Worker](WORKER.md)。

## 重启与关停

`restore` 在启动派发前调用 `recover_startup`。恢复事务交叉核对当前 job、最新 attempt、设备占用、lease、事件重放、PID 身份与批次关系。仅确认没有启动执行的 running 行可安全重排。cancelling 根据未启动或已停止且清理完成的同次证据结算。已经开始执行、业务效果未知的任务保持保护，即使进程已消失。未释放租约的 TTL 到期同样保留占用。[恢复实现](../../facebook_backend/scheduler/production_recovery.py) · [恢复用例](../../tests/test_backend_production_recovery.py)

`shutdown` 停接单、停领、唤醒循环并限时 join。HTTP lifespan 直到线程退出才释放状态锁。排障先读 `/v1/scheduler` 和 `/v1/leases/history` 的已提交投影。`state=unknown`、`last_heartbeat_at=null` 表示缺少独立心跳证据。[关停](../../facebook_capture_api.py#L4517) · [读投影](../../facebook_backend/services/runtime_snapshot.py#L93) · [故障排查](TROUBLESHOOTING.md)

## 历史组件

历史版本（v1.0.1）的实际循环是 `_worker_loop → Dispatcher.plan`；**v1.1.1 的数据库模式已改由上文的事务化 `ProductionScheduler` 承担**，`_worker_loop` 只在显式 manifest 回退时运行。`Scheduler.tick()`、`JobQueue`、`LeaseManager` 和 `RecoveryPlanner` 的组件设计、Mock 基准及当时的接入边界仍见[Phase 4](PHASE-04-SCHEDULER.md)和[Phase 5](PHASE-05-RELIABILITY.md)。显式 manifest 回退保留旧循环，生产 SQLite 路径采用上文的独立事务。控制面 API 与历史基准均没有开放并行设备执行。
