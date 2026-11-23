# 测试说明

- 全量：`python -m unittest discover -s tests -t .`
- **v1.1.1 发布提交（`2a264a0`）**：`Ran 1323 tests`，`OK (skipped=9)`。
- **产品化分支（本文档所在分支，新增 7 项文档/指标回归后）**：`Ran 1330 tests`，`OK (skipped=9)`，本机 Windows 耗时约 196 秒。
- 历史基线（保留原记录）：v1.0.1 为 `Ran 1158 tests in 151.005s`、`OK (skipped=5)`；v1.0.0 为 `1111`/`4 skipped`。数字不同代表用例增加，不是回归。
- 61 个 `tests/test_*.py` 文件、约 1302 个 `def test_` 定义，加 2 个 `tests/manual/*.ps1` 人工脚本。

---

## 1. 必跑命令

```powershell
$env:PYTHONPATH = "."
.venv\Scripts\python.exe tools\check_release_version.py --tag v1.1.1
.venv\Scripts\python.exe -m unittest discover -s tests -t .
.venv\Scripts\python.exe -m pip check
node --check web/app.js
node --check web_v2/app.js
node --check examples/capture_fetch.mjs
node --check perf/k6_api.mjs
.venv\Scripts\python.exe tools\export_source.py     # 校验发布清单
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///$PWD/release-check.db"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current         # 应为 0004_job_event_context (head)
```

## 2. 四层证据必须分开记录

同一个「通过」在不同层的含义完全不同，报告里不得混写。

| 层 | 含义 | 本轮状态 |
|---|---|---|
| **CI** | GitHub 托管 runner 上的真实操作系统 + 干净克隆 | `contracts (windows-latest)`、`contracts (macos-latest)`、`image` 均 success（PR #7/#8/#9/#10/#11） |
| **Mock / 合成夹具** | 假执行器、临时 SQLite、自建子进程；证明控制流与事务，不证明设备行为 | 全量 1330 项的主体 |
| **真实操作系统** | 本机 Windows 或 macOS 上真实启动服务、真实子进程与真实 PowerShell/Python runner | 仅限无设备路径（端口、锁、进程身份、清理读回） |
| **真实 Android 设备** | 真实模拟器 + 真实登录账号 + 真实帖子 | **本轮未执行**，见 §5 模板 |

**禁止**把 CI 的 macOS 结果写成「本机 macOS 全量通过」；也禁止把 `MockExecutor` 的用例写成「真实 Android 测试」。

## 3. 测试分层（按 `def test_` 定义数）

| 套件 | 覆盖 | 数量 |
|---|---|---|
| `tests/test_facebook_*.py` 等历史套件 | 采集、批处理、导航、传输、取消、worker bootstrap、macOS、UI 合约 | ≈850（其余） |
| `test_backend_fastapi.py` | 路由、Host/Origin、请求体边界、Lifespan、静态资源、**双服务器逐字节对比** | 81 |
| `test_backend_persistence.py` | schema/迁移漂移、PRAGMA、Session、乐观锁、租约 fencing、manifest 导入 | 52 |
| `test_backend_scheduler.py` | 派发规则、CAS 认领、租约接管、失败策略、故障隔离、取消、恢复 | 39 |
| `test_backend_modularization.py` | 模块下沉与委托（patch 新模块 → 旧符号结果改变） | 32 |
| `test_backend_job_store.py` | 批次原子受理、DB 权威成员关系、事务回滚、重启恢复 | 27 |
| `test_backend_reliability.py` | 幂等、运行上限、停机、协作取消、启动恢复、Lifespan drain | 24 |
| `test_backend_observability.py` | 日志字段与脱敏、线程隔离上下文、span/trace、指标与 `/metrics` | 23 |
| `test_backend_client_compat.py` | UI 路由契约、状态词表、真实客户端端到端、批次一致性 | 19 |
| `test_backend_lifecycle_events.py` | 状态机、事件幂等、回放、结果对账保护、迁移往返 | 18 |
| `test_backend_production.py` | 生产工厂 + 合成执行器：HTTP→SQL→线程→回调→结果、取消、租约保留、任务级指标 | 16 |
| `test_backend_perf_harness.py` | k6 语法、采样器、基准脚本、假服务、报告可复现性 | 15 |
| `test_backend_rollback.py` | 回退包、WAL 快照、目标目录竞态、写故障、pin/未决保护 | 13 |
| `test_backend_production_recovery.py` | 启动恢复只重排可证明未启动者、竞争与回滚 | 13 |
| `test_backend_store_startup.py` | 新装建库、旧清单拒启、收据、跨进程互斥、句柄释放 | 13 |
| `test_backend_artifact_integrity.py` | 流式 SHA-256、stamp 优先、篡改检测、批次产物 | 12 |
| `test_backend_dispatch_decision.py` | 运行态→派发决策映射 | 10 |
| `test_backend_docs.py` | 文档存在性、相对链接、版本一致性、Docker 打包守卫、产品化一致性 | 16 |
| `test_backend_process_identity.py` | Windows/macOS 出生时间、PID 复用、进程组、整树停止 | 10 |
| `test_backend_runtime_snapshots.py` | 只读快照、WAL 隔离、敏感列排除、轮询不写库 | 9 |
| `test_backend_transport_receipt.py` | 每次执行的恢复凭据、Windows 清理读回、macOS 双校验 | 8 |
| `test_backend_failover_policy.py` | 限流不换设备、退避、最多一次接管、重启复验 | 8 |
| `test_backend_owned_process.py` | Windows Job Object 原子绑定、ActiveProcesses 证明、受限句柄 | 8 |
| `test_backend_container.py` | 控制面只读、提交 503、镜像无设备工具链、compose 不挂运行目录 | 7 |
| `test_backend_telemetry_contract.py` | 标签基数、敏感字段、跨线程 traceparent、幂等只计一次 | 7 |
| `tests/manual/*.ps1` | 需在真机/模拟器上人工执行的脚本 | 2 |

## 4. 已知的跳过与不稳定

| 项 | 说明 |
|---|---|
| 当前 9 项跳过 | 平台专属（Windows 无符号链接权限、POSIX 专属进程组、macOS 专属读回等）。用 `-v` 查看每项原因。 |
| `test_facebook_navigation_latency.py`（已修） | 断言「预算 + 100 ms 定时器容差」并保留下界；机器繁忙时定时器可能提前几毫秒返回 |
| 网络/端口相关用例 | 一律在临时目录与回环端口上运行；离线停服探测用「绑定后释放」的端口，避免 macOS 丢 SYN 导致超时 |
| 真实设备用例 | 不在 `tests/` 内自动执行，全部集中在 §5 模板 |

## 5. 真实设备验收与自动化分开记录

本轮**没有**在线模拟器与可用测试账号，因此下表全部为「待现场」。填写要求：

- 每次验收记录：构建 SHA、操作系统与版本、SDK/AVD、脱敏后的 `job_id`/`attempt_id`、开始与结束时间、执行人。
- 证据（截图、`/health`、`/v1/scheduler`、CSV 首行、SHA-256）保存在现场，不进入仓库。
- 任何一项失败都记录原始错误，不要用「重试后通过」覆盖第一次结果。

### 5.1 记录头

| 字段 | Windows | macOS |
|---|---|---|
| 构建 SHA | 待现场 | 待现场 |
| 操作系统版本 | 待现场 | 待现场 |
| Android SDK / AVD | 待现场 | 待现场 |
| Facebook 版本 | 待现场 | 待现场 |
| 执行人 / 时间 | 待现场 | 待现场 |

### 5.2 十五项验收

| # | 验收项 | 放行证据 | Windows | macOS |
|---|---|---|---|---|
| 1 | ADB 设备状态 | `adb devices` 列出目标 serial 且状态 `device`；`/health` 的 `android.state=device` | 待现场（本轮只读观测：ADB 服务在 5037 监听，设备列表为空） | 待现场 |
| 2 | API Health | `GET /health` 返回 `service=ready`，端口与配置一致 | 待现场 | 待现场 |
| 3 | Worker Health | `GET /v1/workers` 活跃角色、设备、当前任务与预期一致 | 待现场 | 待现场 |
| 4 | Device Lease | 接单前 `GET /v1/devices` 无活动租约；运行时 `GET /v1/leases/history` 出现本次 attempt 的占用 | 待现场 | 待现场 |
| 5 | Job Submit | `POST /v1/jobs` 返回 202；同一 `Idempotency-Key` 重放返回同一 `job_id` | 待现场 | 待现场 |
| 6 | Job Progress | `GET /v1/jobs/{id}/progress` 阶段推进、计数递增、`updated_at` 变化 | 待现场 | 待现场 |
| 7 | Job Complete | `GET /v1/jobs/{id}/result` 的 `completion_scope` 与样本预期一致（`terminal` 或 `bounded-sample`） | 待现场 | 待现场 |
| 8 | Artifact Download | `?download=1` 返回 attachment，文件名与响应头符合契约，字节数与库内 `size` 一致 | 待现场 | 待现场 |
| 9 | SHA-256 验证 | 本地对下载字节独立 `sha256sum`，与 `artifact_integrity` 基线一致；改动文件后重查应报 `changed` | 待现场 | 待现场 |
| 10 | Batch Summary | `batch-summary.json` 的 `status_counts` 与逐任务状态一致，子任务顺序与提交顺序一致 | 待现场 | 待现场 |
| 11 | Cancellation | 取消请求受理时间与 `cancelling`→终态时间分别记录；终态前进程必须真实消失 | 待现场 | 待现场 |
| 12 | Process Stop Verification | 记录 PID/出生时间、Job Object 或进程组证据；`unknown` 必须表现为继续阻塞 | 待现场 | 待现场 |
| 13 | Service Restart Recovery | 受控重启后旧 attempt 仍可追踪；不可证明者保持租约并给出原因 | 待现场 | 待现场 |
| 14 | Worker Failover | 主设备确认故障 → 停止与清理完成 → 备用接管一次；限流场景**不得**换设备 | 待现场 | 待现场 |
| 15 | Transport Cleanup | 代理与 reverse 映射读回原值、watchdog 恢复记录、租约释放属于同一 attempt | 待现场 | 待现场 |

### 5.3 验收结论

| 结论 | 含义 |
|---|---|
| 通过 | 15 项全部有证据且互相一致 |
| 有条件通过 | 某项失败但有明确原因且不影响采集正确性（例如可选 PCAP 缺少 tcpdump） |
| 未通过 | 目标匹配、终点、完整性、进程停止或清理任一项缺证据 |

## 6. CI

| 工作流 | 触发 | 内容 |
|---|---|---|
| `.github/workflows/check.yml` | push / PR | Windows + macOS 矩阵；Python 3.12、Node 22；分模块 unittest、`pip check`、四个 `node --check`、`export_source.py`、版本门禁（标签构建额外校验 tag=包版本） |
| `.github/workflows/container.yml` | push / PR / 手动 | `docker compose config` → `build` → `up -d` → 等 healthcheck → 断言提交返回 503 → `down` |

本地复刻 CI 的关键一步：

```powershell
.venv\Scripts\python.exe tools\export_source.py    # 清单里每个文件都必须存在且是 UTF-8 文本
```

## 7. 性能

```powershell
.venv\Scripts\python.exe perf\run_api_load.py --read-vus 8 --read-duration 10s --submit-rate 20 --submit-duration 10s
.venv\Scripts\python.exe perf\scheduler_bench.py --jobs 200 --devices 1
.venv\Scripts\python.exe perf\state_bench.py
```

v1.1 本轮实测（合成夹具、固定低负载）见 [v1.1 性能](V1.1-PERFORMANCE.md)：读 9.0 RPS / p95 11.6 ms，提交 3.0 RPS / p95 18.0 ms，SQLite 两线程写入 152 笔/秒，流式 SHA-256 256 MiB/s。历史 k6 数字（≈900 RPS）来自另一套负载条件，不能与本轮直接比较。**没有真实设备性能数据。**

## 8. 写测试的约定

- 全部在临时目录里跑，不写仓库（需要时 `mock.patch.object(module, "BASE_DIR", tmp)`）。
- 需要设备的地方一律用 `MockExecutor` / 合成执行器；真实设备路径只写在 §5。
- 断言「没发生」同样重要：取消未验证时必须仍是 `cancelling`、租约未证明时必须保留、不可证明的尝试必须继续阻塞。
- 涉及端口的用例不要在「已绑定但未监听」的端口上断言拒绝——macOS 会丢 SYN 变成超时；用「绑定后释放」的端口。
- 新增 `tests/test_*.py` 要同时加进 `release-files.txt` 与 CI 的模块清单（`tests/test_layout.py` 会检查）。
