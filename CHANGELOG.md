# Changes

## v1.0.1 — 2026-11-22

- 修复 Python 3.12 依赖解析、Windows 导入报告编码和 macOS 进程采样。清理已跟踪的运行数据与工具链，保留既有 Git 历史。见 [v1.0.1 发行说明](docs/backend-modernization/RELEASE_NOTES_v1.0.1.md)。
- 标签 `v1.0.1` 指向 `b2b8270`。发行说明随后合入 `main@f83bcb3`，该提交的公开 Windows/macOS CI 为 `success`。本地 Windows 全量基线为 `Ran 1158 tests in 151.005s`，`OK (skipped=5)`。

## 2026-11-22 — 调度器接管线上派发决策

- `JobManager` 的派发决策不再是一段内联布尔判断：`_dispatch_plan_locked()` 把运行态映射成
  `Device` 快照（enabled / current_job_id / cooldown_until），连同"未验证的捕获租约"
  （`active_leases={active: uncertain}`）一起交给 `Dispatcher.plan()`——也就是
  多进程 `Scheduler.tick()` 用的同一套规则引擎。返回 `lease-held` / `cooldown` / `busy` /
  `disabled` 的阻塞原因，可被测试直接断言。
- 只把**活跃 Worker** 作为候选交给调度器：主备切换仍由 `_activate_next_worker_locked`
  负责，恢复语义不变。
- 循环结构保持原样（先决策、再在 `queue.get(0.5)` 上阻塞取任务），因此取消、超时、
  结果落库、故障接管等 200+ 条既有行为一条未改。
- 新增 `tests/test_backend_dispatch_decision.py`（10 条）：活跃设备被选中、备用设备不被
  派发、队列深度为 0 不派发、冷却/在飞/失败/孤儿/禁用/租约未验证各自返回正确阻塞原因。
- 说明：这是"派发决策与设备互斥"由调度器接管；执行与结果落库仍走 `JobManager` 既有路径
  （那部分被既有测试钉死，重接风险高于收益）。多进程 `Scheduler.tick()` 循环保留。
- 测试：`Ran 1155 tests ... OK (skipped=4)`。

## 2026-11-21 — 事件历史、产物校验指标与导航预算抖动修复

- **追加式任务事件**：`DatabaseJobStore.save_job` 现在在插入时写 `job.created`、状态变化时写
  `job.status_changed{from,to}` 到 `job_events`，并带上当前 trace/batch 上下文。同一状态重复
  保存不会伪造转换；历史只追加、不覆盖（任务时间线/执行事件查看的数据基础）。
- **产物校验指标落地**：`required_artifacts_ready` 是完成规则的唯一评估点，现在按
  `ready`/`incomplete` 计数 `facebook_artifact_verifications_total`（此前只声明未打点；
  进度快照每轮都调用 `artifact_metadata`，因此不在那里计数，避免指标被轮询放大）。
- **修复既有 flaky 用例**：`test_detail_reveal_requires_new_snapshot_and_keeps_budget_on_miss`
  断言睡眠总和 ≤ 4000ms，但剩余预算来自真实 `Get-Date` 差值，Windows 定时器提前几毫秒返回
  时会得到 4013ms。改为「预算 + 100ms 定时器容差」并保留下界 ≥3500ms——真正的回归
  （在探针之外再睡满预算）仍会失败。连跑 3 次全绿。
- 测试：`Ran 1145 tests ... OK (skipped=4)`。

## 2026-11-21 — 状态所有者切换（manifest → SQLite）

- 新增状态存储接缝 `facebook_backend/persistence/job_store.py`：`JobManager` 只依赖
  `load_job_manifests` / `save_job` / `load_batch_manifests` / `save_batch` 四个方法，
  由 `ManifestJobStore`（默认，字段集与顺序和历史逐字一致）或 `DatabaseJobStore`
  （`jobs`/`batches`/`batch_jobs`）实现。恢复、取消、进程身份、租约、产物校验**一行未改**。
- 新增开关 `FACEBOOK_JOB_STORE=manifest|database`（默认 `manifest`）与
  `FACEBOOK_DB_URL`；库未迁移时启动直接失败并提示 `alembic upgrade head`。
- 新增 `manifest_export.py`：把库状态导回 manifest（默认 dry-run、默认不覆盖），
  使切换可回退；`tests/test_backend_job_store.py`（22 条）覆盖往返、upsert、批次关系、
  重启恢复、取消写回、导出可逆与"未迁移即失败"。
- 修正批次写入的外键顺序：数据库实现先确保批次行存在再写子任务，随后由 `save_batch`
  补齐 `run_dir`/`completion_scope` 并建立关系，语义与旧流程一致。
- 文档：新增 `STATE-STORE-CUTOVER.md`，`DATABASE.md` §5 重写为切换/回退步骤。
- 边界：默认值仍是 `manifest`（切换需在停机、导入、核对后由操作者显式打开）；
  调度器仍未接管派发。
- 测试：`Ran 1141 tests ... OK (skipped=4)`。

## 2026-11-21 — 后端现代化 Phase 9（文档、部署与发布 v1.0.0）

- **仓库可复现性修复**（本次最关键的缺陷）：`facebook_http_security.py`、
  `facebook_share_contract.py` 等 156 个发布清单内文件此前**未被跟踪**，克隆后服务根本
  起不来；31 个根目录 `test_*.py` 仍被跟踪而工作区已迁入 `tests/`。现在跟踪树与
  `release-files.txt` 一致（351 个文件），`tests/` 包完整入库（`e049d4d`）。
- `.gitignore` 改为明确分类：跟踪源码/测试/文档/配置模板；忽略运行状态（`runs/`
  1.84 GB、`api-jobs/`、`data/`）、生成物（`dist/`、`perf/results/`）、历史证据
  （`evidence/`、`archive/`、`backups/`）、本机配置与密钥、工具链（`.venv/`、`jre/`、
  `platform-tools/`）。**不删除任何文件**，分类与理由见 `REPO_CLASSIFICATION.md`。
- 新增文档：`DATABASE.md`（表结构/SQLite 参数/迁移与离线导入/PostgreSQL 迁移要点）、
  `SCHEDULER.md`（设备抽象、租约与接管、失败策略、启动恢复、切换清单）、
  `WORKER.md`（设备配置、执行器契约、主备与冷却、运维动作）、`DEPLOYMENT.md`
  （Windows/macOS 原生 + Docker 仅后端 + 升级回滚 + 上线检查表）、`TESTING.md`
  （分层、基线、已知跳过与不稳定项、真机验收单列）、`TROUBLESHOOTING.md`
  （现象→原因→处理）、`REPO_CLASSIFICATION.md`、`RELEASE.md`、
  `RELEASE_NOTES_v1.0.0.md`；README 增加「后端 v1.0」入口表。
- 新增 `Dockerfile` / `.dockerignore` / `docker-compose.yml`：**仅后端**镜像，端口只发布到
  宿主机回环（`127.0.0.1:8765:8765`），镜像内不含 ADB/SDK/模拟器。**本机无 Docker，
  未实际构建验证**，已用静态检查（端口、单 worker、无设备工具链）兜住约定。
- 新增 `tests/test_backend_docs.py`（8 条）：必需文档存在且非空、相对链接可达、
  README 链接、Docker 打包约定、发布说明与 CHANGELOG 版本一致、分类与 `.gitignore` 一致。
- 测试：`Ran 1119 tests ... OK (skipped=4)`。

## 2026-11-21 — 后端现代化 Phase 8（性能与压力测试）

- 新增 `perf/` 压测工装：`fake_server.py`（FastAPI + 内存 manager，无设备）、
  `k6_api.mjs`（reads + submits 两场景，含阈值与分位统计）、`run_api_load.py`
  （起服务 → 跑 k6 → 采样服务端 CPU/RSS → 写 JSON 报告）、`scheduler_bench.py`
  （MockExecutor 测编排吞吐）、`sampler.py`（无依赖读 CPU/RSS，按监听端口定位真正
  提供服务的进程 —— Windows venv 的 python.exe 是启动器，直接采样会读到 4 MiB 的假数）。
- 实测（i7-12700H / 16GB / Win11，单进程 uvicorn，SQLite WAL）：
  - API 8 VUs + 20 submits/s：**901 RPS**，p50 10.0ms / p95 15.7ms / p99 21.1ms，
    失败率 0，服务端 CPU ≈88% 单核、RSS ≈53.6 MiB。
  - API 32 VUs + 100 submits/s：**890 RPS**，p95 83.9ms / p99 112.2ms，失败率 0，
    CPU ≈96% 单核、RSS ≈60 MiB —— 单进程天花板约 900 RPS，加压只增加延迟不产生失败。
  - 调度器编排（200 任务 ×5 次取中位）：串行 **96.6 jobs/s**，2 设备并行 **72.4 jobs/s**。
    并行更慢的原因已定位：每任务 6–8 次 SQLite 写事务，写者串行化；真实采集是分钟级，
    编排开销可忽略，但**编排吞吐的下一步瓶颈是存储层**（合并事务或 PostgreSQL）。
- 顺带修复：`tick(parallel=True)` 原来每个 tick 新建 ThreadPoolExecutor，改为常驻池
  （不改变上述结论，只是去掉每轮线程创建开销）。
- 明确边界：本轮**没有真实设备数据**；所有数字来自假 manager 与 MockExecutor，
  `PERF_REPORT.md` 中已写明不得当作真机结果。
- 测试：新增 9 条工装自检（k6 脚本语法、采样器、基准脚本、假服务、报告可复现性）；
  全量 `Ran 1111 tests ... OK (skipped=4)`。

## 2026-11-21 — 后端现代化 Phase 7（前端与客户端兼容）

- 新增 `tests/test_backend_client_compat.py`（19 条），用三层自动化证据证明旧前端与旧客户端
  在新 FastAPI 后端上行为不变：
  1. **客户端→路由契约**：从 `web/app.js`、`web_v2/app.js` 提取每一处 `api(...)` 调用
     （含模板字符串），归一化后与 FastAPI 路由表比对；删改任何路由立即失败。
  2. **状态词表契约**：`web_v2` 必须覆盖全部 job/batch 状态；`cancelling` 与 `cancelled`
     的标签必须不同（取消请求 ≠ 已停止）；两个 UI 都必须读后端 `{"error": ...}` 字段。
  3. **真实客户端端到端**：启动真实 Uvicorn（fake manager），用 `FacebookApiClient` 走
     submit → poll → wait → result → download → cancel，并验证 failed/cancelled 抛
     `FacebookJobError`、404 抛 `FacebookApiHttpError`、`?download=1` 返回 attachment、
     客户端不重试 POST。
- 批次一致性：用真实 `write_batch_outputs` 断言批次状态与 `status_counts` 和逐任务状态
  一致，且写出的 `batch-summary.json` 与 API 返回一致。
- 本阶段为验证阶段：`web/`、`web_v2/`、`facebook_api_client.py`、`examples/` 一行未改。
- 测试：`Ran 1102 tests ... OK (skipped=4)`。

## 2026-11-21 — 后端现代化 Phase 6（可观测性）

- **上下文**：`job_id` / `batch_id` / `worker_id` / `device_serial` / `attempt_id` /
  `trace_id` / `request_id` 放进 `ContextVar`，API 中间件与调度器在工作单元入口绑定一次；
  调度器多线程各自独立（有测试）。
- **结构化日志**：一行一个 JSON，事件名是稳定标识符（`job.completed`、
  `job.retry_scheduled`）；凭据字段（password/token/api_key/authorization/cookie/...）
  一律丢弃，请求体不记录；`configure_logging` 幂等，不会在 Lifespan 启动时替换调用方 handler。
- **OpenTelemetry**：`configure_tracing(exporter=...)`，SDK 缺失时自动降级 no-op；span 自动带
  上下文属性并在启动后补 `trace_id`；已埋 `http.request`、`job.create`、`scheduler.tick`、
  `job.execute`。
- **Prometheus 指标**：自包含 registry（counter/gauge/histogram）渲染标准文本格式，
  新增 `GET /metrics`（`text/plain; version=0.0.4`）。指标含 API 请求量/延迟、提交数、
  完成数、执行时长、排队时长、队列深度、设备状态、重试、故障接管、取消结果、恢复动作、
  调度 tick。`route` 标签用路由模板而非真实 id，避免基数爆炸。
- 说明：任务级指标埋在新 `Scheduler` 上；旧 `JobManager` 派发路径目前只有请求级观测，
  派发切换后任务级指标自动生效。直方图 p50/p95/p99 由桶边界推导，属上界估计（文档写明）。
- 依赖：`opentelemetry-api==1.38.0`、`opentelemetry-sdk==1.38.0`。
- 测试：`Ran 1083 tests ... OK (skipped=4)`，新增 22 条可观测性测试。

## 2026-11-21 — 后端现代化 Phase 5（任务可靠性）

- **幂等提交**：`POST /v1/jobs` 支持可选的 `Idempotency-Key` 头，同一 key 重放返回原
  job（仍为 202），key 持久化在 manifest 里，服务重启后依然有效；超长 key 返回 400。
  不做"相同 post_url 自动去重"，避免改变既有语义。
- **最大运行时间**：`JobManager(max_runtime_seconds=...)`，默认 6 小时
  （`FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS`，`0` = 历史无限等待），通过
  `_execute_capture()` 真正传给 `run_capture_job`。此前该调用**没有传 timeout**，
  卡死的捕获会永久占住设备。
- **优雅停机**：新增 `JobManager.shutdown(wait_seconds)` 与 `accepting_submissions`；
  停机后新任务/新批次返回 503；有界等待当前任务收尾，**不杀进程、不写 stopped**，
  等待超时只返回 `drained=false` 诊断。FastAPI Lifespan 关闭时按配置的等待值调用。
- **协作取消（新调度器）**：`Scheduler.request_cancel()` — queued 直接取消；running 先
  进 `cancelling` 并设置执行器取消事件；只有执行器证实停止才落 `cancelled`，否则保持
  `cancelling`（`cancel-failed-stop-unverified`）并冷却设备。
- **启动恢复**：新增 `scheduler/recovery.py`。`running`/`cancelling` 任务只有在进程
  身份 `absent`（或显式 `stopped`）时才重排/终态；`match`/`unknown` 一律保持阻塞；
  活跃租约只在同样证据下释放，否则设备继续被占住。`plan()` 只读，`apply()` 幂等。
- 停机等待（默认 30s）与最大运行时间（默认 6h）的关系在报告里专项说明：停机不等任务
  跑完、也不杀进程，重启后按进程身份判定，不会把"重启"说成"续跑"。
- 测试：`Ran 1061 tests ... OK (skipped=4)`，新增 24 条可靠性测试 + 4 条 FastAPI 用例。

## 2026-11-21 — 后端现代化 Phase 4（Device-aware Scheduler）

- 新增 `facebook_backend/scheduler/`：`Device`/`ExecutionSlot`/`WorkerHealth`/`WorkerAssignment`
  值对象、纯决策的 `Dispatcher`（一设备一任务、冷却、租约、端口/serial 冲突、并发上限）、
  持久队列 `JobQueue`（`UPDATE ... WHERE version=?` 的 compare-and-swap 认领，三线程并发
  认领测试保证同一任务不会发给两台设备）、`LeaseManager`（租约 + `fencing_version`）与
  `Scheduler` 主体。
- **接管规则**：租约过期只是必要条件，还必须同时拿到"旧进程已确认停止"和"传输清理已确认
  完成"两项证据，否则设备保持阻塞；三条拒绝用例 + 一条接管用例覆盖。
- **失败分类**（`scheduler/failures.py`）：限流 / 设备 / 网络 / 执行器 / 超时 / 取消分开处理。
  限流会把任务钉在 `jobs.pinned_device_id`（新增迁移 `0002_job_pinning`）并冷却该设备，
  **不会**换账号或换设备继续；设备故障与执行器崩溃允许换设备，超时与取消不自动重试。
- 新增 `facebook_backend/executors/`：`ExecutorPort`/`ExecutionOutcome` 契约、`MockExecutor`
  （调度器测试完全不碰设备）与 `LegacyCaptureExecutor`（适配现有 `run_capture_job`，
  失败时**不伪造** `process_state="stopped"`）。
- 范围说明：调度组件已实现并用 Mock Executor 验证（38 条测试），但**尚未**替换线上派发
  路径——`JobManager` 仍持有队列/设备/租约三项所有权，一次性替换会造成双调度。
  切换清单与门禁写在 `docs/backend-modernization/PHASE-04-SCHEDULER.md` §6。
- 测试：`Ran 1032 tests ... OK (skipped=4)`，其中新增 38 条调度器测试。

## 2026-11-21 — 后端现代化 Phase 3（SQLite + SQLAlchemy + Alembic）

- 新增 `facebook_backend/persistence/`：SQLite engine（WAL / `foreign_keys=ON` /
  `busy_timeout=30000` / `synchronous=NORMAL`）、每工作单元一个 Session（成功提交、
  异常回滚、始终关闭）、8 张表（jobs / batches / batch_jobs / job_attempts /
  job_events / devices / device_leases / artifacts）与仓储层。
- 并发与租约：`jobs.version` 做乐观锁，陈旧写入抛 `ConcurrentUpdateError` 而不是覆盖；
  `device_leases` 用**部分唯一索引**（`WHERE released_at IS NULL`）保证一台设备只有一个
  活跃租约，`fencing_version` 随重新获取递增；**租约过期不等于可接管**，仍需进程身份
  校验与传输清理确认。
- Alembic：`alembic.ini` + 包内 `migrations/`，初始版本 `0001_initial`；
  `tests/test_backend_persistence.py` 逐表比对"迁移产物 vs 模型定义"，漂移即失败。
- 离线迁移工具 `legacy_manifest_import.py`：默认 dry-run；`--apply` 前探测 API 是否仍在
  响应（`--force` 才覆盖并留痕）、先备份 manifest、单事务写入（失败全回滚）、幂等重跑、
  冲突不覆盖、核对 job/batch 数量与 artifact 路径、按进程身份把"仍在运行/身份未知"的任务
  标记 blocked 而**不**标记可恢复。
- 范围说明：本阶段交付数据层与迁移工具，`JobManager` 仍以 manifest 为运行时来源，
  因此不存在双写；状态所有者的切换与调度器重建放在 Phase 4（报告 §6 说明了排序理由）。
- 依赖：`SQLAlchemy==2.0.44`、`alembic==1.16.5`；`.gitignore` 增加 `/data/`、
  `__pycache__/`、`*.pyc`（数据库是机器状态，不入库）。
- 测试：`Ran 994 tests ... OK (skipped=4)`，其中新增 38 条持久化/迁移测试。

## 2026-11-21 — 后端现代化 Phase 0-2（审计 / FastAPI / 模块化）

- Phase 0：新增 `docs/backend-modernization/BASELINE.md`，记录改造前的真实架构、22 条路由、
  Job/Batch 生命周期、主备串行调度、进程身份校验、产物与恢复机制、技术债与风险；
  实测基线 `Ran 849 tests ... OK (skipped=3)`。同时记录两处已知缺口：macOS 没有 PID
  创建时间校验（`_process_creation_time` 在非 Windows 返回 `None`），产物完整性只到
  "存在且非空"、没有 sha256。
- Phase 1：新增 `facebook_backend/`（FastAPI + Uvicorn + Pydantic v2 + Lifespan），
  由新路由调用**未改动**的 `JobManager`；Host/Origin/请求体边界直接复用
  `facebook_http_security`，JSON 序列化、产物响应头、`download=1` 精确匹配、
  严格 `{"acknowledge": true}`、路径空段折叠逐项对齐；`workers=1` 单进程强制。
  `start_facebook_capture_api.ps1` 默认走新入口，`-Legacy` 可回滚；旧模块仍可单独运行。
  新增 `tests/test_backend_fastapi.py`（75 条），其中 `ContractParityTests` 同时启动旧
  `CaptureServer` 与真实 Uvicorn，对 44 组请求逐字节比较状态码、响应体与关键响应头。
- Phase 2：把叶子职责下沉到 `facebook_backend/{domain,artifacts,platform,persistence,scheduler}`，
  `facebook_capture_api.py` 由 5 518 行降至 5 069 行，旧名字保留为同名委托；错误类型与
  `WorkerConfig` 改为共享同一类对象。新增 `tests/test_backend_modularization.py`（32 条）
  用"patch 新模块 → 旧符号结果改变"和"旧模块源码不再含该实现特征串"证明实现只有一份。
- 依赖：`requirements.txt` 增加 `fastapi==0.121.2`、`uvicorn==0.41.0`、`pydantic==2.14.4`、
  `httpx==0.28.1`；CI 增加 `tests.test_backend_fastapi` 步骤。
- 测试：`Ran 956 tests ... OK (skipped=4)`；`pip check` 无破损依赖；`node --check` 三个
  前端/示例脚本通过；`tools/export_source.py` 清单 309 条全部可解析。
- 未做（后续阶段）：SQLite/SQLAlchemy/Alembic、Device-aware Scheduler 的队列与租约、
  Worker Runtime/Executor 拆分、优雅停机、OpenTelemetry/Prometheus、k6 压测。

## 2026-09-17 — 保留稳定流程并补齐交付

- CI 后续修复：传输健康 PowerShell 测试文件保留 UTF-8 BOM；UI 集成测试使用空闲回环端口、绕过系统代理并保留启动错误。避免固定测试环境假设，不改现有运行服务。

- 群组通过当前 UI 的 Reaction 行语义与相对布局定位入口，补齐长内容位置夹具；Reel 修复转义 URL 的脱敏/解析，继续严格核对目标与来源。
- 完成任务的空名单增加兼容字段 `result_outcome`，区分零反应与仅好友姓名可见；API、批次及两版 UI 同步显示，未触发查询仍失败。
- Windows 目标早就绪探测提供诊断与显式开关，因未证明稳定净收益，普通 API 默认关闭；保留原有等待和 Mac 行为。
- 补齐本机稳定基线、APK 来源未知项、Windows/macOS 首次与日常步骤、Codex 配置/账号/Serena/RTK 指南及四种语言可运行 API 示例；不改变依赖版本或现有本机配置。
- Windows PowerShell 选择器夹具使用 UTF-16 EncodedCommand 与明确输出编码，避免非中文 Windows CI 的 cp1252 错误。历史 9 月 16 日失败保留在下文；随后成功样本和恢复证据见 VALIDATION_2026-09-16.md 与 STABLE_BASELINE.md，不应将旧失败快照当成当前结论。

## 2026-09-16 — 稳定化收尾（待最终验收）

- Feed 卡片时间戳识别补齐简体“分享对象”、繁体“分享對象”和全角冒号，保留 Facebook 包名、坐标、详情页和点击结果检查；行为夹具实际执行导航函数。
- 目标匹配按 Facebook URL 完整组件和解码后的 Story 身份检查，拒绝数字或 pfbid 前缀误匹配、外部域名冒充以及相同帖子 ID 的作者冲突。
- 取消流程对进程停止异常、错误或不完整的清理结果保持占用保护；macOS 清理证明需符合当前 attempt 的完整状态结构。已有失败任务可通过取消接口重试未完成的清理，保留原始失败状态和错误。
- 补齐 Windows/macOS 部署入口、配置和依赖说明，README 增加架构与测试方法；真实 Mac 验收仍未执行。
- 最新候选源码包在全新解压目录运行 543 项测试，结果 OK（2 项跳过），并通过 JavaScript/PowerShell 语法、依赖和精确清单校验。原 pfbid 单条复验取得 425 个唯一用户、18 页终点；完整连续批次和最终取消恢复仍待收尾。详细历史与失败证据见 `VALIDATION_2026-09-16.md`。
- 当前真实收尾批次 `batch_d68121ac9c464f00` 顺序执行五类 URL：普通帖子和 `share/p` 各完成 238 个用户、11 页并保留跨任务重复用户；pfbid 在 32 个用户后以 `query-not-triggered` 失败，群组 permalink 未产生新鲜来源 ID，Reel 三次严格校验未匹配。三项失败均保留 partial evidence、停止进程并恢复代理/lease，不能宣称五类全部通过。
- `failure-recovery-20260916-193055.json` 的严格目标拒绝和 transport cleanup 通过；后续 Reel 因当前会话目标不可见而失败。早先成功的 Reel/pfbid 批次仍作为历史证据保留，当前最终验收和源码归档仍待完成。
- 当前源码已重新导出为 119 文件归档，并在全新解压目录通过 548 项 CI 等价测试（2 项跳过）、Node/PowerShell 语法、`pip check`、`git diff --check`、文档和源码白名单审计。真实 Apple Silicon 硬件仍未验收，五类 URL 当前仅 2/5 完成，不能扩大声明范围。

## 2026-09-15 — 审核异常分支修复

- STOP 在发送 `emu kill` 前核对设备实际 AVD 名称；名称不符、为空或查询失败均跳过该命令，保留按配置 AVD 名称识别的进程清理回退。
- 健康检查识别英文 `No internet connection`，保留中文恢复和正常页面复用；单独出现 `Retry` 不视为离线。
- 严格目标分页在初始进度缺失时停止；首次冷却或限流退避刷新发现来源不匹配时，不再发送下一次分页动作。保留非严格模式、正常分页、原冷却和退避时间。
- 运行中取消保持 `cancelling`，直到进程停止及工作阶段收尾得到确认；保留未确认进程的身份和保护锁，确认停止后释放。同步批次计数、界面文案和重启恢复，取消意图不会被重新排队。
- 验证：API 120、客户端 13、编排 125、导航 11、健康与停止 12 项全部通过；核心环境/适配 52 项中 1 项跳过，网络/复用 20 项中 1 项跳过，结果完整性 29 项通过。UI/安全套件 19 项中 18 项通过，旧 `web/index.html` 固定哈希失败在本轮修改前后相同，未改哈希掩盖历史差异。
- 本轮使用隔离夹具，未执行真实取消、STOP 或新采集，未重启现有 API。正在运行的 API 进程需在正常重启后加载 Python 修复。回滚副本：`backups/audit-fixes-20260915-220619/`。

## 2026-09-15 — 连续任务复用与导航等待优化

- 成功任务收尾先原地核对 Facebook 页面健康，健康时保持进程与页面；异常情况保留原有恢复路径。
- 已确认安装成功的常驻注入组件按设备启动标识、PID、进程启动时间、脚本 SHA-256 复用；UI 检查前后身份需一致，证据缺失或身份变化仍完整初始化。注入失败不会留下旧的可复用状态。
- 点开 Reaction 和展开严格帖子详情后先做短延迟新快照，已就绪就继续，未就绪按原预算补足等待；评论排除、来源校验、初始分页冷却和限流退避均保留。
- 真实连续两帖保持同一 PID，第二帖跳过重复注入；两帖均到达终页，未观测到限流。第二帖开始到目标确认约 39.2→25.1 秒，目标确认到列表约 21.2→15.1 秒。页面数据和网络状态会变化，不作为统一耗时承诺。
- 保留原有 MITM 启动 3 秒等待及打开帖子后 8 秒等待。详细本地证据见 `evidence/latency-20260915/README.md`，回滚源文件保存在 `backups/latency-optimization-20260915/`。

## 2026-09-15 — 帖子 Reaction 导航修复与 API 调用交付

- Reaction 汇总图标识别改用 Facebook 滚动容器可见区域与同排赞/评论/分享结构，修复按钮上移到 `y=752` 后被固定 `y>=900` 过滤的问题；保留评论排除与来源校验。真实目标 `share/p/1Jetj5opcE/` 已经由 API 完整取得 167 个唯一用户，终点和来源一致性通过。
- 框架就绪已有条件轮询，移除其前方重复的固定 8 秒等待；保留超时失败机制。
- 进度说明按任务状态与已选产物的实际存在情况显示，不再对失败任务或默认轻量证据声称 PCAP/MITM 均已保存。
- 新增 `facebook_api_client.py` 与 `API_USAGE.md`，复用 `/v1/jobs` 完成提交、轮询、结果和原子下载，失败/取消/超时明确退出，任务 ID 可用于恢复查询；新增 requests 依赖及 HTTP 测试。
- 将已有 STOP/可见会话启动脚本纳入明确发布清单；可见模拟器适配从本机配置解析 SDK、AVD 与 Worker，消除旧命令文件中的开发机绝对路径依赖。
- 保留进入本轮前的未提交恢复/启动/轻量证据改动。详细基线、回滚路径与本轮实测见 `STABILIZATION_2026-09-15.md`；模拟证据不代表 macOS 或全新机器实测。

## 2026-09-13 — Codex 可执行的 macOS 部署手册

- 新增 `macos/CODEX_DEPLOYMENT_RUNBOOK.md`，逐步覆盖 Apple Silicon 主机、Android 35 ARM64、APK、Frida、CA、单/主备 Worker、API、分页、产物、重启与升级。
- 为每个关键步骤写明预期结果、停止条件和故障处理，并明确人工登录/验证码边界以及敏感状态不得提交。
- 将 Windows 本地测试、GitHub `macos-latest` 契约检查和真实 M1-M4 实机验收分开记录，避免用 CI 代替真实 Facebook 深分页证据。
- Windows `START.cmd` 现在委托 `START_API_AND_EMULATOR.cmd`，统一启动 API、V2 UI 与主模拟器，并保留入口退出码。

## 2026-09-09 — 文档与运行证据校准

- 新增 `CURRENT_STATUS_2026-09-09.md`，统一解释 `initial-ready`、`partial/open-tail` 和 `terminal-complete`。
- README 补入群组 permalink、Reel、Watch 和第二个 `pfbid` 的真实移动端首屏证据，删除“群组 permalink 尚缺实机证据”的过时表述。
- 明确 9 月 8 日至 9 日的新 Probe 均未分页；完整分页证据仍为 `reaction-full-smoke-20260902-120331`。
- 对齐历史记录、计划文档和 macOS M4 未实机验收边界。

## 2026-09-09 — pfbid 规范地址变化兼容

- `pfbid` 目标验证现在允许 Facebook 将 `www.facebook.com` 规范化为根域名，并在返回新 `pfbid` token 时使用同一导航请求中的唯一稳定 post/author/legacy story ID 绑定目标。
- 错误或缺失导航哈希、多帖子响应、普通 Feed 查询和目标 post ID 不匹配仍按 `not-matched` 处理。
- 原始 IsmailiGoldenLight `pfbid` URL 已重新通过 Android ProbeOnly：严格匹配 post `122106880826437311`，Reaction 总数 `3537`，首屏 31 个用户、2 条查询、零限流且无分页。

## 2026-09-08 — Mac 一键配置与连续帖子启动优化

- 新增 CONFIGURE.command 和 facebook.setup.example.json，将 Android ARM64 镜像、AVD、Frida、证书、APK 和链接默认打开方式纳入可重复运行的配置流程；账号仍由用户登录。
- 新 Worker 初始化时自动避让模拟器控制/ADB 端口对，既有配置保持不变。
- Windows 采集优先使用现有 Facebook 主进程；每帖保持独立采集边界和完整性校验。
- Mac shell 入口固定 LF；源码导出 ZIP 保留可执行权限和对应 SHA-256。
- 新增部署失败路径与热启动模拟测试。实际 M4 抓取和速度收益仍需接收者实机验证。

## 2026-09-08 — 本地部署与源码交付

- 新增 START.cmd / CHECK.cmd、隔离依赖安装、配置模板与本地启动器。
- SDK/AVD/ADB 路径可由本机配置传递；保留历史默认及已存在的 Worker 配置。
- 修复虚拟环境内 mitmdump 发现路径与显式 Python 优先级。
- 修复 v2 HTML 的 UTF-8 字节损坏、标签和属性闭合。
- v2 静态资源使用白名单，禁止读取页面目录外的文件；主页面增加首次配置指南入口。
- 新增源码发布清单、导出包、默认拒绝跟踪的 Git 规则与首次安装说明。
- 保留历史采集、模拟器数据与账号。真实 Facebook / 新 Windows / Mac 验收另行记录，不以本地单测代替。
