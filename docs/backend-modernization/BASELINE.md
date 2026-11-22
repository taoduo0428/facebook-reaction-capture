# Backend Modernization — Phase 0 Baseline Audit

- **审计时间**：2026-11-21
- **仓库**：`C:\Users\lenovo\Desktop\facebook`（origin `https://github.com/taoduo0428/facebook-reaction-capture.git`）
- **审计时 HEAD**：`2f6036d6213f9c6cd1b5efe9381255c245ebf1ff`（`Separate success and timeout budgets in API example fixtures`）
- **当前分支**：`codex/stabilization-20260915`（跟踪 `origin/codex/stabilization-20260915`）
- **Python**：3.13.12（项目 `.venv`，`requirements.txt` 建议 3.12）
- **审计原则**：只读 + 执行现有测试，不改业务代码。

> 本文件是改造前的**事实快照**。任何后续阶段都不得用本文档描述替代实现；文档与实现不一致时以代码和测试为准。

---

## 1. 当前系统架构

单进程、单模块的本地 HTTP 服务 + 子进程执行器：

```
浏览器 UI (web/ , web_v2/)
        │  fetch /v1/*
        ▼
facebook_capture_api.py  (5 523 行)
  ├─ ApiHandler(BaseHTTPRequestHandler)     ← HTTP 层，手写 if 链路由
  ├─ CaptureServer(ThreadingHTTPServer)     ← 线程化服务器，每请求一线程
  └─ JobManager                             ← 进程内唯一状态所有者
        ├─ queue.Queue[str]                 ← 内存任务队列（非持久）
        ├─ dict[str, dict] _jobs/_batches   ← 内存状态（持久化靠 JSON manifest）
        ├─ threading.Thread _worker_loop    ← 唯一调度线程（primary/standby 串行）
        ├─ subprocess.Popen                 ← 每个 job 一个捕获子进程
        └─ api-jobs/*.job.json / api-batches/*.batch.json  ← 恢复用 manifest
                 │
                 ▼
        run_facebook_capture.ps1 (Windows) / macos/facebook_macos.py (macOS)
                 │
                 ▼
        ADB → Android 模拟器 → mitmproxy/frida 捕获 → runs/<job_id>/ 产物
```

关键事实：

| 项 | 现状 |
|---|---|
| 模块规模 | `facebook_capture_api.py` 5 523 行 / 248 535 字符 |
| HTTP 框架 | 标准库 `BaseHTTPRequestHandler` + `ThreadingHTTPServer` |
| 路由方式 | `do_GET` / `do_POST` 内手写 `segments` if 链（无装饰器、无 schema） |
| 状态所有者 | `JobManager` 单实例，`threading.RLock` 保护 |
| 队列 | `queue.Queue`，仅内存；重启后靠 manifest 重建 |
| 调度线程 | 1 个（`facebook-capture-primary-standby`），主备切换而非并行 |
| 执行单元 | 1 个 `subprocess.Popen`（PowerShell 或 Python），`start_new_session=True` |
| 持久化 | JSON manifest（每 job / 每 batch 一个文件，原子替换写） |
| 依赖 | `mitmproxy`、`frida`、`frida-tools`、`requests`（无 Web 框架依赖） |

`facebook_backend/` 包在审计时**不存在**；仓库内无 FastAPI / SQLAlchemy / Alembic 痕迹。

---

## 2. 现有 API（完整清单，22 个业务路由 + 4 个静态资源）

### GET

| 路由 | 语义 | 成功码 | 失败语义 |
|---|---|---|---|
| `/health` | 服务/调度器/Worker/设备健康 | 200 | — |
| `/guide` | 返回 `README.md`（text/plain） | 200 | 文件缺失即异常 |
| `/v1/setup` | 本机环境自检 `facebook_local.doctor()` | 200 | 400 `{error}` |
| `/v1/capabilities` | `{platform, capture_pcap_supported}` | 200 | — |
| `/v1/jobs/latest` | 最近创建 job | 200 | 404 `no jobs found` |
| `/v1/batches/latest` | 最近创建 batch | 200 | 404 `no batches found` |
| `/v1/jobs/{job_id}` | 公开 job 状态（不含 result） | 200 | 404 `job not found` / 409 ApiError |
| `/v1/jobs/{job_id}/progress` | 进度快照（含阶段、指标、产物） | 200 | 404 / 409 |
| `/v1/jobs/{job_id}/result` | 完整性校验后的结果 | 200 | 404 / 409（未 completed） |
| `/v1/jobs/{job_id}/artifacts/{name}` | 下载产物（逻辑名白名单） | 200 | 404 job/artifact |
| `/v1/batches/{batch_id}` | 批次汇总（与 `/progress` 同源） | 200 | 404 `batch not found` |
| `/v1/batches/{batch_id}/progress` | 同上 | 200 | 404 |
| `/v1/batches/{batch_id}/artifacts/{name}` | 批次产物 | 200 | 404 |
| `/v1/avatars/{user_id}` | 302 跳转到头像 URL | 302 | 503 ApiError |

### POST

| 路由 | 语义 | 成功码 | 失败语义 |
|---|---|---|---|
| `/v1/jobs` | 提交任务 | **202** | 400（body 尺寸/JSON/归一化）/ 503（落盘失败） |
| `/v1/batches` | 提交批次（≤100 个去重 URL） | **202** | 400 / 503 |
| `/v1/jobs/{job_id}/cancel` | 请求取消 | 200 | 404 `job not found` |
| `/v1/batches/{batch_id}/cancel` | 批量取消 | 200 | 404 `batch not found` |
| `/v1/workers/reload` | 重载 `facebook_workers.json` | 200 | 409 ApiError / 503（调度器启动失败、I/O 失败） |
| `/v1/workers/{worker_id}/enable` | 启用 Worker | 200 | 404 / 409 / 503 |
| `/v1/workers/{worker_id}/disable` | 禁用 Worker | 200 | 404 / 409 / 503 |
| `/v1/jobs/{job_id}/reconcile-result-persistence` | **严格**确认结果持久化对账 | 200 | 400（ack 不合法）/ 404 / 409 / 503 |

### 静态资源（`STATIC_ASSETS`）

`/`、`/index.html`、`/styles.css`、`/app.js` → `web/`，统一带 CSP 与 `X-Content-Type-Options: nosniff`。

### 横切契约（改造必须逐条保留）

1. **请求目标**必须是 origin-form（`/` 开头、无反斜杠、无控制字符）。
2. **Host** 必须唯一、回环（`127.0.0.1` / `localhost` / `::1`）、端口等于监听端口。
3. **Origin** 若存在必须同源；`Sec-Fetch-Site: cross-site` 一律拒绝。
4. **请求体框架**：禁止 `Transfer-Encoding`；`Content-Length` 必须唯一且为数字；上限 65 536 字节。
5. **JSON 序列化**：`json.dumps(ensure_ascii=False, separators=(",", ":"))`，`application/json; charset=utf-8`，`Cache-Control: no-store`。
6. **产物下载**：CSV 为 `text/csv; charset=utf-8`，其他类型用 `mimetypes` 原值（**不加 charset**）；`Content-Disposition` 仅对 `{csv,json,jsonl,log,txt}` 且非 `download=1` 时 `inline`；`download=1` 必须是**整个 query 精确等于**。
7. **路径边界**：`_checked_artifact_path` 拒绝绝对路径、`..`、符号链接、解析后越界、空文件。
8. **严格确认接口**：只接受 `Content-Type: application/json` 且 body 精确为 `{"acknowledge": true}`，重复键/多余字段/非 true 全部 400。

---

## 3. Job 生命周期

```
submit() → queued ──dispatch──> running ──> completed
                                   │           failed
                                   │           cancelled
                                   └─ cancel() → cancelling → cancelled
```

- 状态集合实测：`queued`、`running`、`cancelling`、`cancelled`、`completed`、`failed`。
- **`partial` 不是状态**：有界采样提前结束时 job 仍是 `completed`，通过 `completion_scope="bounded-sample"` + result 的 `result_outcome`（如 `partial-open-tail`）表达。改造不得把它压成 `completed` 的普通语义。
- 每次重试/接管拥有独立 `attempt_dirs`，`capture.log` 不覆盖上一次尝试。
- 关键字段：`process_pid`、`process_creation_time`、`process_state`、`transport_cleanup_state`、`capture_run_dir`、`checkpoint`、`recovery_state`、`attempts`、`failover_count`。

---

## 4. Batch 生命周期

```
submit_batch() → 子 job 全部 queued
       ↓ 先写 batch 派生输出（batch-summary.json 等），再提交 manifest
   queued → running → completed | completed_with_errors
```

- `batch_jobs` 关系目前靠 `job.batch_id` / `job.batch_index` 隐式表达，没有独立关系表。
- 子 job 写入遵循"未完成的 batch 派生输出保持私有，不可被重启调度"（`_batch_child_is_committed`）。
- `write_batch_outputs()` 产出 4 个批次产物：`batch-summary.json`、`batch-posts.csv`、`batch-reactor-users.csv`、`batch-user-post-relations.csv`。

---

## 5. Worker 调度（现状 = 主备串行，不是并行）

- 配置来源：`facebook_workers.json`（`load_worker_config` / `validate_worker_config` / `write_worker_config`）。
- `WorkerConfig` 字段：`worker_id`、`serial`、`avd_name`、`capture_port`、`native_port`、`enabled`、`frida_ssl_unpin`、`cooldown_seconds`（默认 900）。
- 校验：worker_id / serial / avd_name / 两个端口必须唯一，且至少一个 enabled。
- 运行时：`_active_worker_id` 只有一个；`_worker_loop` 是**唯一**消费队列的线程；`_worker_runtime[worker_id]` 记录 `current_job_id`、`cooldown_until`、`last_failure`。
- 故障接管：`_worker_failure_confirmed()` → `_activate_next_worker_locked()` → `_recover_after_worker_failure()`，且必须确认旧进程已停止、传输清理完成才允许接管。
- 保护开关：`_capture_lease_uncertain`（捕获租约不确定时整体停止派发）、`_failed_worker_ids`、`_orphaned_worker_ids`、`_persistence_blocked_jobs`。
- **结论**：现有可靠性来自"单活跃 Worker + 确认后接管"，不存在多设备并行；任何并发提升都必须先证明隔离。

---

## 6. Device 管理

- 设备身份即 Worker 身份（1 Worker = 1 AVD = 1 serial = 1 组端口）。
- `adb_snapshot(serial)`：`state`、`boot_completed`、`proxy`、`proxy_error`，用 `_run_quiet` 调 `adb`。
- `ensure_emulator()`：必要时启动 AVD、等待 boot、配置代理端口，带 `process_callback` 以便被取消。
- 健康探测受 `_health_adb_state_scope`（ContextVar）去重，避免一次 `/health` 重复查询 ADB。
- 没有 `devices` / `device_leases` 表，也没有 fencing token；设备占用靠"单调度线程 + 单 active worker"隐式保证。

---

## 7. Process 管理（现有最硬的资产）

| 能力 | 实现 |
|---|---|
| PID + 创建时间校验 | `_process_creation_time()`（Windows `GetProcessTimes`，FILETIME→Unix 秒） |
| 身份比对 | `_process_identity_state()` → `match` / `mismatch` / `unknown` / `absent` |
| 存活判断 | `_process_presence()`（无法判断时返回 `None`，不猜） |
| 进程树终止 | `_terminate_tree()`（`start_new_session` + 进程组/子进程枚举 + `terminate`/`kill` 轮询） |
| 已知 PID 终止 | `_terminate_known_pid(pid, creation_time)`（先验身份再终止） |
| 停止验证 | 只有身份确认消失才 `process_state="stopped"`；否则 `stop-unverified` |
| 停止未验证 | `_mark_process_unverified_locked()` + `_UNVERIFIED_PROCESS_STATES` 阻塞后续派发 |
| 取消语义 | 请求接受 ≠ 已停止；`cancelling` → 真实停止后才 `cancelled` |
| 传输清理 | `_cleanup_job_transport()` + `transport_cleanup_state`（`pending`/`failed`/`restored`/`runner-owned`/`not-required`） |

**已发现的真实缺口**：`_process_creation_time()` 在 `os.name != "nt"` 时直接返回 `None`，即 **macOS 上没有 PID 创建时间校验**，只能依赖 macOS runner 自己写的清理状态文件。这是本轮可靠性改造必须记录的已知限制（不在 Phase 0 修）。

---

## 8. Artifact 管理

- 9 个 job 产物（`ARTIFACT_SPECS`），其中 2 个必需：`facebook-sample-patterns.json`、`facebook-reactor-users.csv`；可选分组：`pcap`、`mitm`、`safe_capture`、`progress`（默认只存后两组）。
- 4 个 batch 产物（`BATCH_ARTIFACT_SPECS`）。
- 完整性 = **必需文件存在且非空**（`_required_artifacts_ready`）+ 结果侧完整性/可见性判定；**没有 sha256**（`hashlib` 仅用于一条归一化摘要，不用于产物校验）。
- 下载安全：`_checked_artifact_path`（见 §2 第 7 条）。
- 元数据：`_artifact_metadata()` 输出 `name/label/layer/path/size_bytes/state/viewable/url/required/selected`。

---

## 9. 当前恢复机制

| 场景 | 机制 |
|---|---|
| 服务重启 | `_load_persisted_jobs()` / `_load_persisted_batches()` 读 `api-jobs/*.job.json`、`api-batches/*.batch.json` |
| manifest 版本 | `version: 1`（`_manifest_payload` / `_batch_manifest_payload`） |
| 运行中任务 | 用持久化的 `process_pid` + `process_creation_time` 判断原进程是否还在；在则标记 `restart-process-still-running` / 不重排 |
| 停止不确定 | `stop-unverified` / `restart-unverified`，并置 `_capture_lease_uncertain` 阻止派发 |
| 传输未清理 | `transport_cleanup_state=pending` 阻止复用设备 |
| 结果落盘失败 | `_persistence_blocked_jobs` + `reconcile_result_persistence(acknowledge=True)` 显式对账 |
| 队列 | **不持久**：重启后 queued job 由 manifest 重建并重新入队（不是"从原执行位置续跑"） |
| 孤儿 Worker | `_orphaned_worker_ids`（历史 Worker 不再可用时阻止接管） |

---

## 10. 现有测试结果（实际执行）

环境：`.venv\Scripts\python.exe`（3.13.12），`PYTHONPATH=.`，Windows 11。

```
python -m unittest discover -s tests -t .
Ran 849 tests in 137.700s
OK (skipped=3)
```

- 日志：`docs/_baseline_unittest.log`（本机执行，未提交）。
- `python -m pip check` → `No broken requirements found.`
- `tests/` 共 41 个测试模块（含 `test_layout.py`、`test_frontend_security.py`、`test_maintenance_gate.py`）。
- 测试分布极不均衡：`test_facebook_capture_api.py` 单文件 293 KB，覆盖 JobManager 的绝大部分语义；`test_facebook_capture_orchestrator.py` 94 KB。
- CI（`.github/workflows/check.yml`）：Windows + macOS 矩阵，Python 3.12、Node 22，按模块分批跑 unittest，`node --check web/app.js`、`node --check web_v2/app.js`、`node --check examples/capture_fetch.mjs`，最后跑 `tools/export_source.py` 校验发布清单。

---

## 11. 技术债务（按改造优先级）

1. **单模块 5 523 行**：HTTP 层、领域逻辑、调度、进程管理、产物、恢复全部耦合在一个文件，无法独立测试调度器/进程层。
2. **进程内状态 + 内存队列**：无法多进程；重启后 queued 任务重建语义与"续跑"容易被误解。
3. **JSON manifest 不是数据库**：batch 与子 job 分文件写，没有跨记录事务、没有唯一约束、没有并发更新保护（只靠单线程 + RLock）；文件数量随历史线性增长。
4. **HTTP 层手写**：无 schema、无 OpenAPI、错误语义靠 if 链复制；新增接口易漏掉 Host/Origin/body 边界。
5. **阻塞调用在请求线程**：`/health` 会调 ADB，`/v1/avatars` 会发网络请求；线程数随并发上升。
6. **无优雅停机**：`JobManager` 没有 `shutdown()`，SIGINT 直接 `server_close()`；捕获子进程靠 daemon 线程随进程退出 + 下次启动的 manifest/身份校验恢复。
7. **无幂等/去重**：同一 post 重复提交会产生多个 job；客户端超时重试会创建新 job。
8. **无超时/截止时间分级**：`run_capture_job(timeout_seconds=...)` 由调用方传入，未见统一的 deadline 策略；超时后必须 `_terminate_tree` 成功才允许释放 worker，否则 `WorkerUnavailableError`。
9. **无结构化日志 / trace / metrics**：只有 `print("[api] ...")` 与子进程 `capture.log`。
10. **macOS 无 PID 创建时间校验**（见 §7）。
11. **产物无哈希**：完整性只到"存在且非空"。
12. **仓库卫生**：工作区存在大量历史日志/运行目录/备份；`release-files.txt` 手工维护 256 条；本分支相对 `origin/main` 落后 65 个提交（`main` 未同步）。

---

## 12. 风险与改造建议

### 高风险点（改造时最容易破坏的地方）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 取消语义被"降级" | 把"取消请求已接受"当成"已停止" | 保留 `cancelling` → 真实停止验证 → `cancelled`；`stop-unverified` 必须继续阻塞 |
| 重启被当成"续跑" | 重新入队被描述成从原位置继续 | 明确区分：重启只做安全恢复，不做无条件续跑 |
| 设备被过早复用 | lease 过期即接管 | 接管前必须确认旧进程消失 + 传输清理完成 |
| 限流被当成故障 | 触发换账号/换设备 | Rate Limit / 设备故障 / 网络异常 / 执行器崩溃 分开处理，禁止自动换账号 |
| 成功判定被削弱 | 用 HTTP 200 代替真实结果校验 | 结果必须走 `build_api_result` 的完整性/可见性判定 |
| 接口契约漂移 | FastAPI 默认行为改变状态码/错误体 | 用**同一 fake manager 双服务器对比测试**锁死契约 |

### 建议路线（与任务书一致）

1. **Phase 1**：FastAPI Adapter（新路由调用现有 `JobManager`），旧入口保留，双服务器对比测试证明契约一致。
2. **Phase 2**：按 API / Service / Scheduler / Worker Runtime / Executor / Process / Artifact 拆分，模块间只走明确接口。
3. **Phase 3**：SQLite + SQLAlchemy 2.x + Alembic；`legacy_manifest_import.py` 支持 dry-run/备份/幂等/核对/回滚；迁移验证前不删 manifest。
4. **Phase 4**：Device-aware Scheduler；先保持 `max_concurrent_jobs = 1`，隔离测试通过后再提并发。
5. **Phase 5**：可靠性（幂等、重试分类、退避、deadline、协作取消、优雅停机、恢复）。
6. **Phase 6**：OpenTelemetry + Prometheus（日志统一带 `job_id/batch_id/worker_id/device_serial/attempt_id/trace_id`）。
7. **Phase 7**：前端/客户端兼容回归（含 partial/failed/cancelled 展示）。
8. **Phase 8**：k6 API 压测 + Mock Executor 调度器压测（真实设备测试单独记录）。
9. **Phase 9**：文档、部署、发布、仓库整理。

### 不改的东西

采集算法、URL 归一化、完整性/可见性判定、Windows/macOS 执行器、ADB 生命周期、限流与冷却语义 —— 一律保持。
