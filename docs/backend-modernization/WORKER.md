# Worker 与原生执行器

一个 worker 对应一台配置的 Android 设备。原生服务在一个执行槽中运行 active worker，其余设备作为按顺序接管的备用。配置版本仍是 `1`；它描述设备配置格式，与 SQLite schema 版本分别管理。

## 配置和运行态

使用[示例配置](../../facebook_workers.example.json)中的 `worker_id`、`serial`、`avd_name`、`capture_port`、`native_port` 和 `enabled`。名称、serial、AVD 名各自唯一，两个端口在全部 worker 中唯一且在 1024–65535 内。至少一个 worker 启用。冷却秒数非负，默认 900；可选 `frida_ssl_unpin` 默认关闭。缺少配置文件时，原生加载器回退到单个默认 worker。[校验与加载](../../facebook_backend/scheduler/device_registry.py#L61)

静态配置定义设备；运行态 `devices` 记录已登记设备的占用、冷却和策略状态。`GET /v1/workers` 和 `/v1/devices` 返回登记投影，未曾登记的备用可能缺席，worker 进程心跳缺证时为 `null`。`GET /v1/workers/{worker_id}/history` 返回尝试记录。原生 `/health` 另行探测设备，能报告 `active_worker_id` 和 `capture_lease.reason`。[投影实现](../../facebook_backend/services/runtime_snapshot.py#L52) · [原生健康诊断](../../facebook_capture_api.py#L4566)

## 一次原生执行

| 步骤 | 调用点与检查 |
|---|---|
| 认领 | `ProductionTransactions.claim` 写入设备与 attempt、token、fence，使用每次独立的 `attempt-NNN`。旧目录已存在时原生执行保持保护。[认领](../../facebook_backend/scheduler/production_transactions.py#L70) |
| 启动 | `NativeAttempt.execute → LegacyCaptureExecutor.run_attempt → run_capture_job`，通过回调登记进程句柄、启动身份与传输待清理状态。Mock 仅用于隔离测试。[适配器](../../facebook_backend/executors/legacy.py) · [捕获入口](../../facebook_capture_api.py#L2276) |
| Windows | 启动器构造 PowerShell runner。Job Object 在创建原生 runner 时绑定，用活动进程数检查其后代。共享基础进程不可因一次取消被集体终止。传输恢复还要核对当前尝试的代理、reverse 读回及 watchdog 交接收据。[进程观测](../../facebook_backend/platform/owned_process.py#L114) · [恢复收据](../../facebook_backend/platform/transport_receipt.py#L107) |
| macOS | Darwin 分支构造 Python 的 `facebook_android_capture.py` 命令。检查进程/进程组状态及本次运行的 owner、退出码、清理错误、代理与 reverse 读回。[命令选择](../../facebook_capture_api.py#L417) · [收据检查](../../facebook_backend/platform/transport_receipt.py#L48) |
| 结果与释放 | 从 attempt 文件重建业务结果，验证目标、来源、完整范围。以停止和传输证据核对同一所有者后才提交结果和释放租约。[结果](../../facebook_capture_api.py#L1888) · [执行结算](../../facebook_backend/scheduler/production_transactions.py#L182) |

## 取消、接管与时间预算

排队时取消可直接落 `cancelled`。运行中取消先写 `cancelling`，再通知持有该执行的线程及已登记的原生句柄。晚登记句柄也受取消约束。根进程结束、时间预算到期、用户收到取消回应，均不是传输恢复凭证。异常失败、取消、无法读回代理或旧 attempt 收据均可能继续占用租约。[取消转发](../../facebook_backend/scheduler/production.py#L51) · [原生证据门禁](../../facebook_backend/scheduler/production_native.py#L83)

单次默认预算 21600 秒，`FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS=0` 表示无预算上限。`FACEBOOK_SHUTDOWN_WAIT_SECONDS` 默认 30 秒，控制服务等待线程的时长。启动恢复、网络退避、限流不换设备和主备接管条件见[调度说明](SCHEDULER.md)。配置变更用已提供的 reload/enable/disable 接口，并按当前执行情况检查返回结果。新增备用不会创建第二个派发线程。[配置接口](../../facebook_backend/api/routes/workers.py) · [线程选择](../../facebook_capture_api.py#L2469)

历史运行样本见[稳定基线](../../STABLE_BASELINE.md)，v1.1 的真实平台验收项目见[测试说明](TESTING.md)。Job Object、runner 收据及隔离测试各自只覆盖一部分证据，完整设备任务需要在对应平台记录。
