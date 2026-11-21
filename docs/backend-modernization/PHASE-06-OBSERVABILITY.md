# Phase 6 — Observability 阶段报告

- 阶段：**Phase 6 / 可观测性**
- 目标：统一 trace（API → 创建 → 队列 → 调度 → 执行 → 产物）、结构化日志（六个关联字段）、
  Prometheus 指标（延迟、队列、执行时长、重试、故障接管、取消、恢复）。
- 结果：**完成**（22 条新测试）；全量 **1083 tests OK（4 skipped）**。
- 边界：指标已在**新调度器**与 **API 层**真实埋点；`JobManager` 的旧派发路径只加了
  请求级指标/日志，任务级指标等派发切换后自然生效（见 §6）。

---

## 1. 问题

改造前只有 `print("[api] ...")` 和子进程 `capture.log`：

- 没有 trace：一次请求经过 API、队列、调度、子进程、产物校验，无法串起来；
- 日志是自由文本，没有 `job_id`/`trace_id`，排查只能靠时间戳猜；
- 没有任何指标：队列积压、执行时长、重试/接管次数、恢复次数全靠人工数文件；
- 服务关闭、恢复、取消这些关键动作没有结构化事件，事后无法复盘。

## 2. 设计

### 2.1 上下文（`observability/context.py`）

六个字段放进 `ContextVar`，由 API 中间件与调度器在工作单元入口绑定一次：

```
job_id, batch_id, worker_id, device_serial, attempt_id, trace_id, request_id
```

用 `ContextVar` 而不是全局 dict：调度器在多个线程里跑任务，每个线程必须看到自己的上下文
（有测试：两个线程各自 `bound(job_id=...)` 互不干扰）。

### 2.2 结构化日志（`observability/logging.py`）

一行一个 JSON：`ts/level/logger/service/event` + 上下文 + 结构化字段。

- **事件名是稳定标识符**（`job.completed`、`job.retry_scheduled`），不是句子；
- **凭据永不落盘**：字段名含 `password/token/api_key/authorization/cookie/secret/...`
  一律丢弃（`redact`），请求体完全不记录（有测试：POST 体里的 `password` 不出现在日志里）；
- 幂等安装：已有结构化 handler 时只更新级别，不会在 Lifespan 启动时把调用方的 handler 换掉。

### 2.3 Trace（`observability/tracing.py`）

- 提供 `configure_tracing(exporter=...)`；**SDK 缺失时自动降级为 no-op**，请求路径不需要
  到处写 `if tracing:`。
- 不写 `trace.set_tracer_provider`（它是 write-once，测试里会互相污染）；需要时用
  `set_global=True` 显式开启。
- `span(name, **attrs)` 自动把上下文塞进属性，并在 span 启动后补 `trace_id`
  （span 未启动时没有 id，这一点有测试验证）。
- 已埋的 span：`http.request`、`job.create`、`scheduler.tick`、`job.execute`。

### 2.4 指标（`observability/metrics.py` + `GET /metrics`）

自包含的 registry（counter / gauge / histogram），渲染 **Prometheus 文本格式**
（`# TYPE`、`_bucket{le=...}`、`_sum`、`_count`、`+Inf`）。不引入 `prometheus_client`：
任务书说 Prometheus/Grafana 后续接入，但**数值现在就要记**，否则历史丢失；渲染标准格式后
换成官方客户端是 drop-in。

实际埋点的指标（没有装饰性空指标）：

| 指标 | 类型 | 标签 |
|---|---|---|
| `facebook_api_requests_total` | counter | method, route, status |
| `facebook_api_request_duration_seconds` | histogram | method, route |
| `facebook_jobs_submitted_total` | counter | — |
| `facebook_jobs_completed_total` | counter | status |
| `facebook_job_execution_seconds` | histogram | device, status |
| `facebook_queue_wait_seconds` | histogram | — |
| `facebook_queue_depth` | gauge | — |
| `facebook_devices` | gauge | state（available/busy/cooldown/disabled/blocked） |
| `facebook_job_retries_total` | counter | failure |
| `facebook_failovers_total` | counter | — |
| `facebook_cancellations_total` | counter | outcome（verified-stop/unverified） |
| `facebook_recoveries_total` | counter | action |
| `facebook_scheduler_ticks_total` | counter | — |

直方图的 p50/p95/p99 由桶边界推导，是**上界估计**（文档里写明），count/sum 是精确值。

### 2.5 路由标签的基数控制

`route` 取 Starlette 解析后的**模板**（`/v1/jobs/{job_id}`），不是真实 id。有测试断言
渲染结果里出现模板、不出现真实 job id —— 否则指标基数会随任务数爆炸。

## 3. 实际修改

| 文件 | 内容 |
|---|---|
| `observability/context.py` | 上下文绑定/恢复、线程隔离、`snapshot()` |
| `observability/logging.py` | JSON 格式化、脱敏、`configure_logging`、`log_event` |
| `observability/tracing.py` | OTel 配置 + no-op 降级 + `span()` + `current_trace_id()` |
| `observability/metrics.py` | registry、直方图、Prometheus 渲染、`PlatformMetrics` |
| `observability/middleware.py` | 每请求：span + 日志 + 两个指标；请求 id |
| `main.py` | 装配中间件（最外层，拒绝的请求也被观测）、Lifespan 装日志、事件化日志 |
| `api/routes/health.py` | `GET /metrics`（`text/plain; version=0.0.4`） |
| `services/job_service.py` | `job.create` span、提交计数、`job.submitted` 事件 |
| `scheduler/manager.py` | tick/execute span、队列深度与设备状态 gauge、执行时长/排队时长、重试/接管/取消/完成计数与事件 |
| `scheduler/recovery.py` | 每个恢复动作计数 + `recovery.action` 事件 |
| `requirements.txt` | `opentelemetry-api==1.38.0`、`opentelemetry-sdk==1.38.0` |
| `tests/test_backend_observability.py` | 22 条测试 |

## 4. 测试

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_observability
Ran 22 tests in 0.798s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 1083 tests in ...s
OK (skipped=4)
```

| 测试类 | 覆盖 |
|---|---|
| `StructuredLoggingTests`（3） | JSON 结构 + 六字段；凭据脱敏（password/authorization/cookie 都不出现）；一行一对象 |
| `ContextTests`（2） | 嵌套绑定与恢复；**线程隔离** |
| `TracingTests`（2） | 未配置时 no-op；配置后嵌套 span 共享 trace id、属性带 job/device/trace |
| `MetricsTests`（3） | counter/gauge/histogram 数值；Prometheus 渲染（`_bucket`/`+Inf`/`_sum`/`_count`）；reset 重建 |
| `ApiObservabilityTests`（5） | 请求被计量并记日志；**route 用模板不用真实 id**；提交计数与 `job.submitted`；**请求体不进日志**；`/metrics` 文本格式 |
| `SchedulerObservabilityTests`（7） | tick 计数与队列深度；设备状态 gauge 映射；限流重试不计接管；设备故障计接管；取消结果区分 verified/unverified；恢复动作计数 |

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| HTTP 契约 | 中间件只观察不修改响应；**双服务器逐字节对比测试仍通过** |
| 日志默认行为 | 未调用 `configure_logging` 时日志照常（标准 logging），不改变旧入口 |
| 依赖 | 新增 OTel 两个包；SDK 缺失时 tracing 自动降级，其余功能不受影响 |
| 性能 | 每请求多一次 `perf_counter`、一次 JSON 序列化和两个指标写入；无 I/O |

## 6. 未做（如实说明）

- **`JobManager` 的任务级指标**：旧派发路径仍由 `JobManager` 拥有，本阶段只在其上加了
  请求级观测。任务级（执行时长/重试/接管）已在新 `Scheduler` 埋好，切换后自动生效。
- **Grafana 面板**：需要真实 Prometheus 抓取数据，属 Phase 9 文档/部署内容；本轮交付
  可抓取的 `/metrics` 与指标清单。
- **OTLP 导出器**：`configure_tracing(exporter=...)` 接受任意 exporter，但默认不建连接；
  Phase 9 部署文档给出接 Collector 的示例。

## 7. 提交

- `feat(observability): structured logs, OpenTelemetry spans and Prometheus metrics`
