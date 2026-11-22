# Phase 8 — 性能与压力测试报告

- 阶段：**Phase 8 / 性能验证**
- 结论：单进程 API 在本机约 **900 RPS** 饱和（p95 从 15ms 升到 83ms，失败率始终 0）；
  调度器编排吞吐约 **96.6 jobs/s**（串行），**2 设备并行反而慢 25%**，瓶颈是 SQLite 写入串行化。
- 所有数字都来自本机实测，命令与产物路径写在 §6；**没有真实设备数据**，也没有把 Mock 结果
  包装成真机结果。

---

## 1. 测试对象与边界

| 对象 | 是什么 | 不是什么 |
|---|---|---|
| `perf/fake_server.py` | FastAPI 应用 + **内存版 manager**，覆盖 health/latest/submit/status/progress/metrics | 不执行采集、不连 ADB、不写 manifest |
| `perf/scheduler_bench.py` | `Scheduler` + `MockExecutor`（零延迟）编排路径 | 不代表真机执行耗时 |
| 真实设备 | **本轮未测**（需要模拟器 + 登录态） | 不得用上面的数字代替 |

## 2. 环境

| 项 | 值 |
|---|---|
| CPU | 12th Gen Intel Core i7-12700H（14 核 / 20 线程） |
| 内存 | 16 GB |
| OS | Windows 11 家庭版 10.0.26200 |
| Python | 3.13.12（`.venv`） |
| ASGI | uvicorn 0.41.0，**单进程**（进程内调度器的硬约束） |
| 数据库 | SQLite（WAL，`busy_timeout=30000`） |
| 压测工具 | k6 v0.54.0（`k6.exe v0.54.0 (commit/baba871c8a, go1.23.1, windows/amd64)`） |
| 说明 | 共享工作站，非隔离环境；重复运行波动约 ±10% |

## 3. API 压测（k6）

两个场景同时跑：
- `reads`：8 个常驻 VU 循环 `GET /health`、`GET /v1/jobs/latest`、`GET /metrics`；
- `submits`：恒定 20 次/秒 `POST /v1/jobs`（202）后接 `GET /v1/jobs/{id}` 与 `/progress`。

### Profile A：8 VUs + 20 submits/s，10 秒（3 次运行）

| 指标 | 值 |
|---|---|
| RPS | 884.34 / **901.45（中位）** / 948.01 |
| 延迟 p50 | ≈10.0 ms |
| 延迟 p95 | 14.5 / **15.7（中位）** ms |
| 延迟 p99 | ≈21.1 ms |
| 失败率 | **0.0**（三次都是 0） |
| 服务端 CPU | 10.13 / 10.23 / 10.53 秒（每次 ~11.5 秒墙钟，≈ 88% 单核） |
| 服务端 RSS | ≈53.6 MiB |

### Profile B：32 VUs + 100 submits/s，15 秒（3 次运行）

| 指标 | 值 |
|---|---|
| RPS | 854.96 / **889.93（中位）** / 894.09 |
| 延迟 p50 | 65.9 / 70.9 ms |
| 延迟 p95 | 82.0 / **83.9** ms |
| 延迟 p99 | 100.0 / 112.2 ms |
| 失败率 | **0.0** |
| 服务端 CPU | 15.30 / 15.52 / 15.67 秒（每次 ~16 秒墙钟，≈ 96% 单核） |
| 服务端 RSS | ≈60 MiB |

**解读**：RPS 在 900 附近封顶（CPU 已接近单核满载），继续加压只增加排队延迟
（p95 15ms → 83ms），但**没有出现失败**——是"变慢"而不是"打崩"。
多进程/多实例能把天花板抬高，但那需要先完成跨进程调度与租约协调（本项目的既定前置条件）。

## 4. 调度器吞吐（MockExecutor，无设备）

200 个任务，每个配置跑 5 次取中位数：

| 模式 | 吞吐（jobs/s） | 队列等待 p50 | 说明 |
|---|---|---|---|
| 串行（1 设备，`max_concurrent_jobs=1`） | **96.6**（94.87–99.07） | 1121.5 ms | 生产默认 |
| 并行（2 设备，`tick(parallel=True)`） | **72.41**（69.28–76.37） | 1357.6 ms | 两设备各 100 个任务，分配均匀 |

### 为什么并行反而更慢（实测结论）

每个任务在编排路径上要经过 **6–8 次 SQLite 写事务**（claim、lease acquire、event、
lease release、queue complete、clear_pin …）。SQLite 同一时刻只有一个写者，
两个线程只是把写请求互相排队，外加锁等待开销，所以零延迟 Mock 下并行净亏 ~25%。

**这不否定多设备并行**：真实采集是分钟级，编排开销（~10 ms/任务）可以忽略，
2 台设备确实能把采集吞吐翻倍。它说明的是：

- 想提高**编排**吞吐，下一步该优化存储（把每任务多次写合并成一次事务，或换 PostgreSQL），
  而不是加线程；
- 这正是任务书把 PostgreSQL 定位为"多实例/并发写入需求明确后"的触发条件。

### 顺带修掉的一个实现问题

`tick(parallel=True)` 原来**每个 tick 创建一个 ThreadPoolExecutor**。已改为在
`Scheduler.__init__` 里建一个常驻池、`shutdown()` 时释放。改动没有改变上面的结论
（瓶颈是 SQLite 而不是线程创建），但去掉了每轮派发的线程创建/销毁开销。

## 5. 队列等待与吞吐的关系

串行 200 个任务、吞吐 96.6 jobs/s 时，第 N 个任务大约等待 `N / 96.6` 秒：
中位数 1.12 秒与 `100 / 96.6 ≈ 1.04` 秒吻合。也就是说在**批量提交**场景下，
队列延迟由吞吐线性决定，与调度器本身的开销无关。

## 6. 复现方式

```powershell
# 1) API 压测（自动找 k6：K6_BIN / PATH / 本机临时目录）
.venv\Scripts\python.exe perf\run_api_load.py `
  --read-vus 8 --read-duration 10s --submit-rate 20 --submit-duration 10s --label api

.venv\Scripts\python.exe perf\run_api_load.py `
  --read-vus 32 --read-duration 15s --submit-rate 100 --submit-duration 15s --label api_stress

# 2) 调度器吞吐（MockExecutor，无设备）
.venv\Scripts\python.exe perf\scheduler_bench.py --jobs 200 --devices 1
.venv\Scripts\python.exe perf\scheduler_bench.py --jobs 200 --devices 2 --parallel

# 3) 只做语法检查（CI 里跑）
node --check perf\k6_api.mjs
```

产物：`perf/results/api_<时间戳>.json`、`perf/results/scheduler_<模式>_<时间戳>.json`
（该目录在 `.gitignore` 中，不入库）。每个 JSON 含 `k6_version`、`base_url`、
`profile`、延迟分位、失败率、服务端 CPU/RSS 与采样来源（`pid_source`）。

## 7. 与任务书指标对照

| 任务书要求 | 本轮结果 |
|---|---|
| RPS | 900（Profile A 中位）/ 890（Profile B 中位） |
| p50 / p95 / p99 | 10.0 / 15.7 / 21.1 ms（A）；70.9 / 83.9 / 112.2 ms（B） |
| Queue Latency | 串行 p50 1121 ms（200 任务批量） |
| Worker Throughput | 96.6 jobs/s（编排），2 设备并行 72.4 jobs/s（见 §4） |
| CPU | ≈88%（A）/ ≈96%（B）单核 |
| Memory | 53.6 MiB（A）/ 60 MiB（B）峰值 RSS |

## 8. 已知限制（必须与数字一起读）

1. **没有真实设备数据**：本报告全部是 HTTP 层与编排层数字。
2. **假 manager**：`/v1/jobs` 只写内存 dict，真实路径还要写 manifest（原子写 + `os.replace`），
   会额外增加每次提交的磁盘开销。
3. **共享工作站**：后台进程会影响 ±10%，报告用 3–5 次运行的中位数。
4. **单进程 uvicorn**：这是当前架构的硬约束，不是配置疏忽。
5. **k6 不是项目依赖**：CI 只做语法检查，压测需本机安装 k6 后手动执行。
