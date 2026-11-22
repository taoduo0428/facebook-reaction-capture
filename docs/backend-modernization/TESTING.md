# 测试说明

- 全量：`python -m unittest discover -s tests -t .`
- v1.0.1 的 Windows 本地全量基线为 `Ran 1158 tests in 151.005s`，`OK (skipped=5)`。1158 是发现并运行的用例总数，含 5 项跳过。
- `main@f83bcb3` 的公开 [Windows/macOS CI](https://github.com/taoduo0428/facebook-reaction-capture/commit/f83bcb3/checks) 成功。CI 按平台分模块执行，真实设备验收另见 §5。

---

## 1. 必跑命令

```powershell
$env:PYTHONPATH = "."
.venv\Scripts\python.exe tools\check_release_version.py
.venv\Scripts\python.exe -m unittest discover -s tests -t .
.venv\Scripts\python.exe -m pip check
node --check web/app.js
node --check web_v2/app.js
node --check examples/capture_fetch.mjs
node --check perf/k6_api.mjs
.venv\Scripts\python.exe tools\export_source.py     # 校验发布清单
```

## 2. 测试分层

| 套件 | 覆盖 | 数量级 |
|---|---|---|
| `tests/test_facebook_*.py` | 既有业务与执行路径（采集、批处理、导航、传输、取消、worker bootstrap、macOS、UI 合约） | ≈1030 |
| `tests/test_backend_fastapi.py` | 路由、Host/Origin、请求体边界、Lifespan、静态资源、**双服务器逐字节对比** | 79 |
| `tests/test_backend_modularization.py` | 模块下沉与委托（patch 新模块 → 旧符号结果改变；旧模块不再含实现） | 32 |
| `tests/test_backend_persistence.py` | schema/迁移漂移、PRAGMA、Session、乐观锁、租约 fencing、manifest 导入 | 38 |
| `tests/test_backend_scheduler.py` | 派发规则、CAS 认领、租约接管、失败策略、串行/并行、故障隔离、取消、恢复 | 39 |
| `tests/test_backend_reliability.py` | 幂等、运行上限、停机、协作取消、启动恢复、Lifespan drain | 24 |
| `tests/test_backend_observability.py` | 日志字段与脱敏、线程隔离上下文、span/trace、指标与 `/metrics` | 22 |
| `tests/test_backend_client_compat.py` | UI 路由契约、状态词表、真实客户端端到端、批次一致性 | 19 |
| `tests/test_backend_perf_harness.py` | k6 语法、采样器、两个基准脚本、假服务、报告可复现性 | 9 |
| `tests/test_layout.py` | 源码布局、BOM、发布清单完整性、CI 必跑模块 | 5 |
| `tests/manual/*.ps1` | 需要在真机/模拟器上人工执行的脚本 | — |

## 3. 已知的跳过与不稳定

| 项 | 说明 |
|---|---|
| v1.0.1 Windows 基线跳过 5 项 | 用 `python -m unittest discover -s tests -t . -v` 查看本机每个跳过项和原因。平台、符号链接权限及可用工具会影响结果。 |
| `tests/test_facebook_navigation_latency.py`（已修） | 该用例断言 PowerShell 助手的等待预算，剩余预算来自真实 `Get-Date` 差值；Windows 定时器可能提前几毫秒返回，导致总和偶发 4013ms。现改为「预算 + 100 ms 定时器容差」并保留下界（≥3500 ms），真正的回归（在探针之外再睡满预算）仍会失败；连跑 3 次全绿 |

## 4. CI

`.github/workflows/check.yml`：Windows + macOS 矩阵，Python 3.12、Node 22，分模块跑
unittest（含全部 `tests.test_backend_*`），`pip check`、`node --check` 四个脚本、
`tools/export_source.py` 清单校验。

```powershell
# 本地复刻 CI 的关键一步：确认清单里的每个文件都存在且是 UTF-8 文本
.venv\Scripts\python.exe tools\export_source.py
```

## 5. 真实设备验收（与自动化分开记录）

下表记录真实设备验收。Windows 开发机本轮只读观测到 ADB 服务已在回环 5037 监听，`adb devices` 没有列出设备，8765/8766 没有监听。未发起真实任务。macOS 栏留待真实宿主填写。每次验收记录构建 SHA、操作系统、SDK 版本、脱敏 job/attempt ID、开始和结束时间，将原始证据保存在现场。

| 验收项 | 放行证据 | Windows | macOS |
|---|---|---|---|
| ADB 设备状态 | 目标 serial 在线且 Android 已完成启动 | 已检查，设备列表为空 | 待现场 |
| API health | 私有回环 `/health` 与预期版本和端口一致 | 检查时无监听 | 待现场 |
| Worker health | 活跃角色、设备状态、最后心跳可核对 | 待现场 | 待现场 |
| Device lease | 接单前空闲，运行时有本次 attempt 的占用凭证 | 待现场 | 待现场 |
| Job submit | 授权样本提交获 202、重放得到同一 ID | 待现场 | 待现场 |
| Job progress | 状态、阶段、递增进度关联到同次执行 | 待现场 | 待现场 |
| Job complete | 结果范围、目标、来源和终点符合样本预期 | 待现场 | 待现场 |
| Artifact download | 原逻辑文件名、下载响应头及实际字节符合预期 | 待现场 | 待现场 |
| Artifact SHA-256 | 用本地下载字节独立复算，与该 attempt 校验报告对照 | 待现场 | 待现场 |
| Batch summary | 子任务关系、顺序、各终态计数和批次报告一致 | 待现场 | 待现场 |
| Cancellation | 请求受理时间及 `cancelling` 到终态时间分开记录 | 待现场 | 待现场 |
| Process stop | 进程身份和整棵自有进程树的停止证据完整 | 待现场 | 待现场 |
| Restart recovery | 受控重启后旧 attempt 仍可追踪，未知态持续阻塞 | 待现场 | 待现场 |
| Worker failover | 主设备确认故障、停止和清理完成后按顺序交接，限流仍保持绑定 | 待现场 | 待现场 |
| Transport cleanup | 前后设备设置复核、清理记录和租约释放属于同一 attempt | 待现场 | 待现场 |

## 6. 性能测试

```powershell
.venv\Scripts\python.exe perf\run_api_load.py --read-vus 8 --read-duration 10s --submit-rate 20 --submit-duration 10s
.venv\Scripts\python.exe perf\scheduler_bench.py --jobs 200 --devices 1
```

数据与边界见 `PERF_REPORT.md`：API ≈900 RPS（单进程）、调度编排 96.6 jobs/s（串行）、
2 设备并行 72.4 jobs/s（SQLite 写串行化）。**没有真实设备性能数据。**

## 7. 写测试的约定

- 测试全部在临时目录里跑，不写仓库（`BASE_DIR` 需要 patch 时用 `mock.patch.object`）；
- 需要设备的地方一律用 `MockExecutor` 或 fake manager；
- 断言"没发生"同样重要：例如取消未验证时必须仍是 `cancelling`、租约未证明时必须保留；
- 新增 `tests/test_*.py` 要同时加进 `release-files.txt`（`test_layout` 会检查）。
