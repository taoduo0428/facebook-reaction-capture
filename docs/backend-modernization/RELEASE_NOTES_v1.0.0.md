# v1.0.0 — 后端现代化

**Facebook Automation Platform — Device-aware Automation Task Orchestration Platform**

本版本把已经具备复杂执行与恢复能力的本地采集工具，升级为模块化、可持久化、
设备感知调度、可观测、可测试的 Python 后端。**HTTP 契约、状态语义、产物名称与
采集算法没有变化**——旧前端、旧客户端、旧入口继续可用。

---

## 一、这一版做了什么

| 阶段 | 内容 | 报告 |
|---|---|---|
| 0 | 仓库审计：22 条路由、Job/Batch 生命周期、主备串行、进程身份、恢复机制、技术债；实测基线 849 条测试 | [BASELINE](BASELINE.md) |
| 1 | FastAPI Adapter：22 条路由 + 静态资源；Host/Origin/请求体边界复用同一份安全模块；双服务器逐字节对比 | [PHASE-01](PHASE-01-FASTAPI-ADAPTER.md) |
| 2 | 业务模块化：错误类型、请求归一化、进程原语、产物边界、原子写、设备注册表下沉；旧模块保留同名委托 | [PHASE-02](PHASE-02-MODULARIZATION.md) |
| 3 | SQLite + SQLAlchemy 2.x + Alembic：8 张表、乐观锁、租约 fencing、离线 manifest 导入（dry-run/备份/幂等/核对/回滚） | [PHASE-03](PHASE-03-PERSISTENCE.md) |
| 4 | 设备感知调度：Dispatcher + CAS 队列 + 租约（过期≠可接管）+ 失败分类与重试策略 + 执行器契约 | [PHASE-04](PHASE-04-SCHEDULER.md) |
| 5 | 可靠性：Idempotency-Key、最大运行时间、优雅停机、协作取消、启动恢复 | [PHASE-05](PHASE-05-RELIABILITY.md) |
| 6 | 可观测性：JSON 日志（六字段 + 脱敏）、OpenTelemetry trace、Prometheus `/metrics` | [PHASE-06](PHASE-06-OBSERVABILITY.md) |
| 7 | 前端与客户端兼容：路由契约、状态词表、真实客户端端到端 | [PHASE-07](PHASE-07-CLIENT-COMPAT.md) |
| 8 | 性能：k6 API 压测与 MockExecutor 调度基准（实测数字，无真机数据） | [PERF_REPORT](PERF_REPORT.md) |
| 9 | 文档、部署、发布与仓库整理 | 本文 + [RELEASE](RELEASE.md) / [REPO_CLASSIFICATION](REPO_CLASSIFICATION.md) |

## 二、升级方式

```powershell
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt     # 新增 fastapi/uvicorn/pydantic/httpx/SQLAlchemy/alembic/opentelemetry
.venv\Scripts\python.exe -m alembic upgrade head                # 可选：启用数据库层
.venv\Scripts\python.exe -m unittest discover -s tests -t .     # 应为 1111 OK / 4 skipped
.\start_facebook_capture_api.ps1                                # 默认走新 FastAPI 入口
```

回滚：`.\start_facebook_capture_api.ps1 -Legacy` 或 `python facebook_capture_api.py`。

## 三、兼容性

- **HTTP**：22 条业务路由、状态码、错误体、产物响应头、`download=1` 精确匹配、
  严格 `{"acknowledge": true}`、Host/Origin 校验、64 KiB 请求体上限——全部保持不变，
  由新旧服务器对比测试锁定。
- **前端**：`web/`、`web_v2/` 一行未改；路由契约、状态词表、产物占位符由自动化测试保证。
- **客户端**：`facebook_api_client.py` 一行未改；真实 Uvicorn 上跑通
  submit → poll → wait → result → download → cancel。
- **采集语义**：`partial` 仍不是状态（是 `completion_scope` + `result_outcome`）；
  取消仍要验证停止；重启仍不承诺续跑。

## 四、新增运维能力

| 能力 | 用法 |
|---|---|
| 幂等提交 | `POST /v1/jobs` 带 `Idempotency-Key: <key>`，重放返回原任务 |
| 指标 | `GET /metrics`（Prometheus 文本格式） |
| 优雅停机 | 关闭时停止接收新任务（503），有界等待，不杀进程 |
| 最大运行时间 | `FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS`（默认 6h，0=无限） |
| 数据库迁移 | `alembic upgrade head`；离线导入 `python -m facebook_backend.persistence.legacy_manifest_import` |
| 排障 | [TROUBLESHOOTING](TROUBLESHOOTING.md) |

## 五、验证证据

- 全量测试：**1111 通过 / 4 跳过**（Windows 本机；macOS CI 多跑一条符号链接用例）
- 契约：新旧服务器 44 组请求逐字节对比
- 迁移：真实语料 dry-run 668 jobs / 69 batches / 112 artifacts，0 无效、0 孤儿
- 性能：API 中位 901 RPS（p95 15.7ms，失败率 0）；调度编排 96.6 jobs/s（串行）
- 仓库：351 个文件被跟踪，与 `release-files.txt` 一致；`tools/export_source.py` 校验通过

## 六、已知限制（如实说明）

1. **运行时仍以 manifest 为准**：数据库层与导入工具已完成，但状态所有者切换与
   调度器重建绑定，尚未执行（清单见 [PHASE-04 §6](PHASE-04-SCHEDULER.md)）。
2. **单进程**：进程内队列/租约，Uvicorn 多 worker 被入口拒绝。
3. **没有真实设备自动化验收**：真机/模拟器项需人工执行（见 [TESTING §5](TESTING.md)）。
4. **macOS 无 PID 创建时间校验**：恢复时按 `unknown` 处理，宁可阻塞也不猜。
5. **一条既有时间敏感用例偶发**（导航延迟预算，毫秒抖动），已记录待修。
