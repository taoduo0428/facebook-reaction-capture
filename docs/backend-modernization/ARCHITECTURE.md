# 后端架构

Windows 和 macOS 原生服务各运行一个 HTTP 进程。服务启动时选定一个状态所有者，一个串行派发线程按主备策略控制设备。数据库保存任务状态和执行归属，每次尝试的文件留在本机。十张调用图、恢复决策和当前缺口见[开发分支架构评审](V1.1-FINAL-ARCHITECTURE-REVIEW.md)。

## 从 HTTP 到持久状态

| 层 | 当前入口与职责 |
|---|---|
| HTTP | `create_app()` 注册路由、错误映射、观测及回环 Host/Origin 边界。阻塞管理操作由路由放入线程池。[应用工厂](../../facebook_backend/main.py) · [作业路由](../../facebook_backend/api/routes/jobs.py) |
| 业务服务 | `JobService.submit` 调用 `JobManagerPort`，区分提交、查询、取消与对账错误，保留 `{"error": ...}` 响应。[服务](../../facebook_backend/services/job_service.py) · [接口合约](API_COMPATIBILITY.md) |
| 管理门面 | `JobManager.submit` 归一化请求并处理幂等键，`submit_batch` 组织有序子任务。数据库受理事务提交后才发布内存视图、唤醒派发。[单任务受理](../../facebook_capture_api.py#L2810) · [批次事务](../../facebook_backend/persistence/job_store.py#L285) |
| 持久化 | `open_state_store` 默认 `auto`。全新默认安装可自动初始化 SQLite。有历史清单须先核对迁移收据，已有库与自定义库须通过当前迁移和结构检查。`manifest` 是显式回退选项。[启动条件](../../facebook_backend/persistence/state_startup.py#L180) · [数据库操作](DATABASE.md) |
| 派发 | 数据库模式调用 `ProductionScheduler.run` 和 `ProductionTransactions.claim`。独立 `Scheduler.tick` 供组件用例使用。显式 manifest 模式走原有 `_worker_loop`。[线程选择](../../facebook_capture_api.py#L2469) · [调度说明](SCHEDULER.md) |
| 执行与结果 | `NativeAttempt` 为每轮建立独立目录，经 `LegacyCaptureExecutor.run_attempt` 进入平台 runner。业务证据、进程停止、传输恢复与持久化决定结算。H 的摘要验证状态见架构评审第 9 节。[原生执行](../../facebook_backend/scheduler/production_native.py) · [Worker](WORKER.md) |
| 运行态读接口 | `RuntimeSnapshotService` 以 `mode=ro` 打开 SQLite，在一次读事务里查询已提交的设备、租约或调度计数。运行态 API 与会探测设备的原生 `/health` 分开。[投影路由](../../facebook_backend/api/routes/runtime.py) · [快照实现](../../facebook_backend/services/runtime_snapshot.py) |

## 进程生命周期

原生 CLI 先绑定回环监听套接字。工厂的 lifespan 再取得状态根和数据库文件的所有者锁，双锁覆盖服务和迁移工具。此后才打开 store、执行启动恢复并决定是否开派发线程。创建应用或处理单个请求不会重建调度器；测试注入的 manager 由调用方管理。[CLI](../../facebook_backend/__main__.py#L38) · [lifespan](../../facebook_backend/main.py#L116) · [互斥锁](../../facebook_backend/persistence/state_startup.py#L70)

关停时停止接单与新认领，唤醒循环并有界等待线程。未退出的线程继续持有状态所有权。重启时重新核对进程、传输、attempt 和 lease 证据。`drained` 需要无线程活动、无活动任务及无未决保护。[关停实现](../../facebook_capture_api.py#L4517) · [延迟解锁](../../facebook_backend/main.py#L43)

## 兼容与历史

Windows 启动脚本及 `facebook_local.py start` 均可进入 FastAPI。Windows `-Legacy` 选择标准库 HTTP 入口，也从配置选择同一个 store。换 HTTP 入口与回退状态存储是两项操作。数据库回退先离线验证独立回退包，再在恢复安装中显式选 manifest。[部署](DEPLOYMENT.md) · [兼容入口](../../facebook_capture_api.py#L5184)

Phase 1 将 HTTP 适配与门面分离，Phase 2 保留旧符号、下沉叶子实现。Phase 3 引入数据库仓储，Phase 4 验证独立调度组件。这些报告保留当时的调用点、基准和限制。v1.0.1 仅复用 `Dispatcher.plan` 的现状记录适用于该版本；本开发分支按上表走数据库生产循环。[Phase 1](PHASE-01-FASTAPI-ADAPTER.md) · [Phase 2](PHASE-02-MODULARIZATION.md) · [Phase 3](PHASE-03-PERSISTENCE.md) · [Phase 4](PHASE-04-SCHEDULER.md) · [v1.0.1](RELEASE_NOTES_v1.0.1.md)
