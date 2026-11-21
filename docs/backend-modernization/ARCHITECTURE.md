# Backend Architecture

> 状态标记：**[已实现]** 本轮已落地并有测试；**[下一步]** 目标结构，尚未实现。
> 本文只描述真实存在的分层；未实现的模块不会被写成"已完成"。

## 1. 分层

```
                 ┌──────────────────────────────────────────────┐
 HTTP 客户端 ───▶ │ api/routes/*            [已实现]             │  只做 HTTP：解析、错误映射、响应
 (web, client,   │   health/jobs/batches/workers/avatars/setup  │
  scripts)       │   static                                     │
                 └───────────────┬──────────────────────────────┘
                                 │ Depends(get_*_service)
                 ┌───────────────▼──────────────────────────────┐
                 │ services/*              [已实现]             │  每个资源的用例 + 错误契约
                 │   job/batch/worker/artifact                  │
                 └───────────────┬──────────────────────────────┘
                                 │ JobManagerPort (Protocol)
                 ┌───────────────▼──────────────────────────────┐
                 │ compatibility/legacy.py [已实现]             │  适配现有 JobManager
                 │   facebook_capture_api.JobManager            │  （Phase 3 起换成 DB 版实现）
                 └───────────────┬──────────────────────────────┘
                                 │
      ┌──────────────────────────┴───────────────────────────────┐
      │ JobManager（现有，未改动）：队列 / 调度 / 进程 / 恢复 / 产物 │
      └──────────────────────────────────────────────────────────┘
```

**[下一步]** Phase 2 会把 `JobManager` 内部的调度、进程、产物职责拆成
`scheduler/`、`workers/`、`executors/`、`platform/`、`artifacts/`，并用同样的
`JobManagerPort` 组合它们；Phase 3 用 SQLite 仓储实现同一端口。

## 2. 目录与职责（现状）

| 路径 | 职责 | 状态 |
|---|---|---|
| `facebook_backend/main.py` | `create_app()`、Lifespan、异常处理器、中间件装配 | 已实现 |
| `facebook_backend/config.py` | `Settings`（host/port/serial/base_dir/limits/workers=1），纯函数构造 | 已实现 |
| `facebook_backend/dependencies.py` | `get_manager` / `get_*_service`，manager 来自 `app.state` | 已实现 |
| `facebook_backend/api/__init__.py` | `build_router()`，路由注册顺序 | 已实现 |
| `facebook_backend/api/routes/*` | 每资源一个 APIRouter | 已实现 |
| `facebook_backend/api/request_body.py` | 提交体 framing、严格 ack 解析 | 已实现 |
| `facebook_backend/api/responses.py` | 逐字节复刻的 JSON / 产物 / 静态响应 | 已实现 |
| `facebook_backend/security/guard.py` | ASGI 信任边界（Host/Origin/body framing/路径空段折叠） | 已实现 |
| `facebook_backend/services/*` | 用例 + 错误契约（`map_legacy_errors`） | 已实现 |
| `facebook_backend/domain/ports.py` | `JobManagerPort`（HTTP 层唯一依赖的抽象） | 已实现 |
| `facebook_backend/domain/statuses.py` | Job/Batch 状态与 completion scope 词表 | 已实现 |
| `facebook_backend/domain/exceptions.py` | `BackendError` 及 400/404/409/503 子类 | 已实现 |
| `facebook_backend/schemas/*` | Pydantic v2 模型（OpenAPI 文档 + 响应断言） | 已实现 |
| `facebook_backend/compatibility/legacy.py` | 与旧模块的**唯一**接触面 | 已实现 |
| `facebook_backend/scheduler/*` | 队列 / 派发 / 设备注册 / 租约 | 下一步（Phase 4） |
| `facebook_backend/persistence/*` | SQLAlchemy 模型 / 会话 / 仓储 / Alembic | 下一步（Phase 3） |
| `facebook_backend/workers/*`、`executors/*` | Worker Runtime、Windows/macOS/Android 执行器 | 下一步（Phase 2/4） |
| `facebook_backend/platform/*` | ADB、进程管理器 | 下一步（Phase 2） |
| `facebook_backend/artifacts/*` | 产物发现/校验/下载 | 下一步（Phase 2） |
| `facebook_backend/observability/*` | 结构化日志 / metrics / tracing | 下一步（Phase 6） |

## 3. Lifespan 契约

```python
@asynccontextmanager
async def lifespan(app):
    if app.state.manager is None:
        app.state.manager = await run_in_threadpool(factory)   # 只构造一次
    yield
    app.state.closing = True
    if app.state.manager_owned and hasattr(manager, "shutdown"):
        await run_in_threadpool(manager.shutdown)              # Phase 5 才有真正的 drain
```

不变量（均有测试）：

1. **import 不构造 manager**：`create_app()` 返回的 app 中 `state.manager is None`。
2. **一个进程一个 manager**：5 次请求后工厂仍只被调用 1 次。
3. **每请求不新建 manager**：依赖只读 `app.state`。
4. **注入的 manager 由调用方负责**：`manager=` 传入时 app 不调用其 `shutdown()`。
5. **没有 `shutdown()` 时不假装优雅停机**：只记录告警，恢复仍由 manifest + 进程身份校验负责。

## 4. 并发与阻塞

- 所有 `JobManager` / ADB / 头像网络调用都在 `starlette.concurrency.run_in_threadpool` 中执行，事件循环不做磁盘、子进程或网络 I/O。
- 线程池默认上限 40（anyio），不引入额外池。
- **Uvicorn 必须单进程**：`Settings.uvicorn_workers` 固定 1，`__main__` 拒绝 >1 与 `WEB_CONCURRENCY>1`。
- 捕获子进程仍由 `JobManager` 的调度线程启动，HTTP 进程只观察状态。

## 5. 错误模型

| 领域错误 | HTTP | 产生位置 |
|---|---|---|
| `BadRequestError` | 400 | body framing、JSON 解析、ack 校验、归一化 `ApiError`（提交路径） |
| `NotFoundError` | 404 | `KeyError`（job/batch/worker/artifact）、产物文件缺失 |
| `ConflictError`（`BackendError(status=409)`） | 409 | `ApiError`（查询/取消/对账路径） |
| `ServiceUnavailableError` | 503 | `OSError`、`SchedulerStartError` |
| 其它异常 | 500 | 兜底处理器（记录 traceback，不泄漏细节） |

## 6. 与旧服务的共存

- `facebook_capture_api.py` **完全保留**（含 `ApiHandler` / `CaptureServer`），`facebook_local.py start` 仍走旧服务器。
- 两个入口共享同一 `JobManager`、同一 `facebook_http_security`、同一 manifest。
- 同一时间只能有一个服务实例持有 manifest 目录（原本就是单实例设计）。

## 7. 部署形态

| 形态 | 命令 | 说明 |
|---|---|---|
| 新入口（默认） | `python -m facebook_backend --host 127.0.0.1 --port 8765 --serial emulator-5554` | FastAPI + Uvicorn，单进程 |
| Windows 一键 | `start_facebook_capture_api.ps1` | 默认新入口 |
| Windows 回滚 | `start_facebook_capture_api.ps1 -Legacy` | 旧标准库服务器 |
| 旧入口 | `python facebook_capture_api.py` | 保留 |
| 本地控制台 | `python facebook_local.py start` | 仍用旧服务器（Phase 7 再切） |

Docker：仅可用于后端服务开发环境；**Android 执行器必须原生运行**（需要本机 ADB、可见模拟器窗口与 SDK），不要放进容器。
