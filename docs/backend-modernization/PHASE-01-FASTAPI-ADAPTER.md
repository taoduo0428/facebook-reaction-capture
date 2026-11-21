# Phase 1 — FastAPI Adapter 阶段报告

- 阶段：**Phase 1 / FastAPI Adapter**（在 Phase 0 基线审计之后）
- 目标：把 HTTP 层从 `BaseHTTPRequestHandler + ThreadingHTTPServer` 换成 **FastAPI + Uvicorn + Pydantic v2 + Lifespan**，**业务状态仍由现有 `JobManager` 独占**，接口契约零漂移。
- 结果：**完成**。新增 75 条测试全部通过；全量测试 924 条通过（3 skip）；契约由"同一 fake manager 双服务器逐字节对比"锁定。

---

## 1. 问题（改造前）

1. `facebook_capture_api.py` 5 523 行，HTTP 层用 `if segments == [...]` 手写，无法生成 OpenAPI，也无法独立测试。
2. Host/Origin/body 边界散落在 `do_GET`/`do_POST` 与 `facebook_http_security`，新增接口极易漏校验。
3. 每个请求一个线程（`ThreadingHTTPServer`），阻塞调用（ADB、头像网络请求）直接跑在请求线程里。
4. 没有应用生命周期：启动/关闭没有钩子，`JobManager` 由 `main()` 手工构造，未来接入调度器/数据库没有落点。
5. 没有分层：路由、业务、错误映射混在一起，Phase 2 无法增量拆分。

## 2. 设计

### 2.1 分层（见 `ARCHITECTURE.md`）

```
api/routes/*  →  services/*  →  JobManagerPort  →  compatibility/legacy.py  →  JobManager
```

- 路由只做：读 body、调 service、返回 `ApiJSONResponse`。
- Service 只做：调 manager + 把旧异常映射成 `BackendError(400/404/409/503)`。
- `JobManagerPort` 是 HTTP 层唯一的依赖抽象（Protocol），Phase 2/3 换实现不动路由。

### 2.2 安全边界复用而不是重写

`security/guard.py` 通过 `HeaderBag` + `_RequestView` 把 ASGI scope 适配成
`facebook_http_security.request_error(handler)` 期望的形状，**直接调用同一个函数**；
POST 的 framing 同样调用 `body_length()`。两套服务器因此不可能在校验规则上漂移。

### 2.3 契约复刻的四个关键点

| 点 | 做法 |
|---|---|
| JSON 序列化 | `json.dumps(ensure_ascii=False, separators=(",", ":"))`，`application/json; charset=utf-8` + `no-store` + `nosniff` |
| 路径空段 | 旧 `_segments()` 丢弃空段（`/health/` 等价 `/health`）；新增 `normalize_path_scope()` 复刻 |
| 产物头 | CSV 才加 charset；其余用 `mimetypes` 原值；`download=1` 必须**整个 query 精确等于** |
| 严格 ack | 仅 `application/json` + 精确 `{"acknowledge": true}`，禁重复键/多余字段 |

### 2.4 阻塞与并发

- 所有 manager/ADB/网络调用走 `run_in_threadpool`；async endpoint 不碰磁盘/子进程。
- Uvicorn **单进程**：`Settings.uvicorn_workers=1`；`__main__` 拒绝 `>1` 与 `WEB_CONCURRENCY>1`。
- `JobManager` 只在 Lifespan 构造一次；import 阶段零副作用。

### 2.5 错误映射（逐路由固定）

| 旧异常 | HTTP | 说明 |
|---|---|---|
| `KeyError` | 404 | 文案按资源：`job not found` / `batch not found` / `worker not found` / `no jobs found` / `no batches found` |
| `ApiError` | 400 或 409 | 提交路径 400，查询/取消/对账 409（与旧 handler 一致） |
| `SchedulerStartError` | 503 | 逐路由文案与旧代码逐字一致 |
| `OSError` | 503 | 逐路由文案一致 |
| 其它 | 500 | 兜底，记录 traceback，不泄漏路径 |

## 3. 实际修改

### 新增（36 个文件）

| 路径 | 内容 |
|---|---|
| `facebook_backend/__init__.py` | 懒加载 `create_app`，import 零副作用 |
| `facebook_backend/main.py` | `create_app()`、Lifespan、异常处理器、中间件 |
| `facebook_backend/config.py` | `Settings`（host/port/serial/base_dir/limits/workers=1） |
| `facebook_backend/dependencies.py` | `get_manager` / `get_*_service` |
| `facebook_backend/__main__.py` | `python -m facebook_backend`，单进程/回环强制 |
| `facebook_backend/api/__init__.py` | `build_router()`（路由顺序） |
| `facebook_backend/api/routes/{health,jobs,batches,workers,avatars,setup,static}.py` | 22 条业务路由 + 4 个静态资源 |
| `facebook_backend/api/request_body.py` | 提交体 framing、严格 ack |
| `facebook_backend/api/responses.py` | JSON/产物/静态响应复刻 |
| `facebook_backend/security/guard.py` | ASGI 信任边界 + 路径归一 |
| `facebook_backend/services/{job,batch,worker,artifact}_service.py` | 用例 + 错误契约 |
| `facebook_backend/domain/{ports,statuses,exceptions}.py` | 端口、状态词表、领域错误 |
| `facebook_backend/schemas/{job,batch,worker,device,result}.py` | Pydantic v2 模型 |
| `facebook_backend/compatibility/legacy.py` | 与旧模块唯一接触面 |
| `tests/test_backend_fastapi.py` | 75 条测试 |

### 修改（5 个文件）

| 文件 | 改动 |
|---|---|
| `requirements.txt` | 新增 `fastapi==0.121.2`、`uvicorn==0.41.0`、`pydantic==2.14.4`、`httpx==0.28.1` |
| `.github/workflows/check.yml` | Windows/macOS 各加一步 `tests.test_backend_fastapi` |
| `tests/test_layout.py` | 把 `tests.test_backend_fastapi` 加入"CI 必须包含"的断言集合 |
| `start_facebook_capture_api.ps1` | 默认启动 `python -m facebook_backend`；`-Legacy` 回滚旧入口 |
| `stop_all_facebook.ps1` | 进程标记加入 `facebook_backend`，新入口可被 `STOP.cmd` 正常停止 |
| `release-files.txt` | 追加 40 条新文件 |

**没有改动**：`facebook_capture_api.py`（5 523 行，一行未动）、`facebook_api_client.py`、`web/`、`web_v2/`、`examples/`、`facebook_local.py`、`macos/*`、`facebook_android_capture.py`。

## 4. 测试

### 4.1 新增测试（`tests/test_backend_fastapi.py`，75 条）

| 测试类 | 覆盖 |
|---|---|
| `RouteInventoryTests` | 22 条旧路由全部注册、静态白名单一致、状态词表与旧源码不漂移、OpenAPI 记录提交体 |
| `LifespanTests` | import 不建 manager、startup 只建一次、5 次请求仍 1 个、注入的 manager 不被 shutdown、自建 manager 的 shutdown 只调一次 |
| `TrustBoundaryTests` | 非回环 Host、端口不匹配、跨源 Origin、`Sec-Fetch-Site`、`Transfer-Encoding`（且**未触达 manager**）、超大 body |
| `JsonFormatTests` | 紧凑 UTF-8 序列化、`no-store`/`nosniff`、未知路径 404 体、方法不匹配 → 404、尾斜杠归一 |
| `JobRouteTests` | 202 提交、空 body、JSON 解析错误文案、归一化错误 400、落盘失败 503、latest 404、409 冲突、progress/result、cancel |
| `ArtifactRouteTests` | CSV `inline` + `Content-Length`、`download=1` → `attachment`、多余 query 不触发下载、未知产物、缺文件 404 |
| `BatchRouteTests` | 202、latest 404、两条进度路径、artifact、cancel |
| `WorkerRouteTests` | reload 200/409/503×2、enable/disable、未知 worker 404 |
| `ReconcileRouteTests` | 无 body、错 Content-Type、多余字段、重复键、非布尔、正确 ack、charset 变体、404/409 |
| `SetupRouteTests` | `/v1/setup` 成功与 400、`/v1/capabilities`、`/guide` |
| `StaticAssetTests` | 4 个 UI 资源 + CSP + `no-store` |
| `AvatarRouteTests` | 302 + `Location` + `Cache-Control`、503 |
| `MissingManagerTests` | 未启动 lifespan 时 503 |
| `RealManagerIntegrationTests` | **真实 `JobManager`**（隔离 `BASE_DIR`）+ 真实 manifest 恢复 + `/health` 调度器状态 |
| `EntrypointWiringTests` | 启动脚本默认 FastAPI/保留 Legacy、停止脚本标记、`__main__` 拒绝多 worker/非回环 |
| `ContractParityTests` | 旧 `CaptureServer` vs 新 FastAPI（真实 Uvicorn）**44 组请求逐字节对比**（状态码 + body + 5 个关键头） |

### 4.2 执行结果

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_fastapi
Ran 75 tests in 4.132s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 924 tests in 129.870s
OK (skipped=3)
```

基线对照：改造前 `Ran 849 tests ... OK (skipped=3)`（见 `BASELINE.md` §10）。新增 75 条，**没有减少任何既有测试**。

对比测试实际发现并修掉了 3 个真实契约偏差（不是设计文档能替代的）：

1. `HeaderBag` 用 `str` 比较 `bytes` 头名 → 所有请求被判 `untrusted Host`。
2. Starlette 会把 `/health/` 当 404/307，旧 `_segments()` 视为同一路由。
3. 产物 `application/json` 被 Starlette 自动追加 `; charset=utf-8`，旧实现只对 CSV 加 charset。

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| `facebook_capture_api.py` 未被修改 | `git diff` 为空 |
| 旧入口可运行 | `python facebook_capture_api.py` 未改动；`start_facebook_capture_api.ps1 -Legacy` 可用 |
| 旧 `CaptureServer` 仍可服务 | `ContractParityTests` 每次都真实启动旧服务器 |
| 旧前端可继续工作 | 4 个静态资源 + 全部 `/v1/*` 路由一致；`tests.test_ui_v2_and_safety` 通过 |
| 旧客户端可继续工作 | `facebook_api_client.py` 未改动；`tests.test_facebook_api_client`、`tests.test_api_examples` 通过 |
| manifest 恢复 | `RealManagerIntegrationTests` 用真实 `JobManager` 读回 `completed` 记录且不篡改 |
| 进程/取消/恢复语义 | 全部仍由未改动的 `JobManager` 承担；本轮**没有**降低任何停止/清理标准 |

## 6. 未做（明确留到后续阶段）

- **优雅停机**：`JobManager` 仍无 `shutdown()`；Lifespan 只记录告警，不假装 drain（Phase 5）。
- **结构化日志/trace/metrics**：Phase 6。
- **`facebook_local.py start` 仍用旧服务器**：Phase 7 统一切换。
- **业务逻辑拆分**：Phase 2。
- **数据库**：Phase 3。

## 7. 回滚方案

1. `start_facebook_capture_api.ps1 -Legacy`（无需改代码）。
2. 或直接 `python facebook_capture_api.py`。
3. `facebook_backend/` 是纯增量包，删除不影响旧服务。

## 8. 提交

本阶段与 Phase 0 分别独立提交（分支 `codex/stabilization-20260915`）：

| 提交 | 内容 |
|---|---|
| `d482e18` `docs(backend): Phase 0 baseline audit of the existing capture API` | `docs/backend-modernization/BASELINE.md`（1 file, +265） |
| `a8bb0fa` `feat(backend): FastAPI adapter with a byte-identical HTTP contract` | 45 files, +3 632 / −46（`facebook_backend/` 35 个文件、测试、3 篇文档、依赖/CI/启动脚本/发布清单） |

工作区中属于其它历史会话的未提交改动**未被包含**在本阶段提交里（`git status` 仍会显示它们），本阶段只提交了上述文件。
