# API Compatibility — Legacy `ThreadingHTTPServer` → FastAPI

- 迁移对象：`facebook_capture_api.ApiHandler` → `facebook_backend.api.routes.*`
- 契约来源：现有客户端 `facebook_api_client.py`、`web/app.js`、`web_v2/app.js`、`examples/`、`tests/test_facebook_capture_api.py`
- 验证方式：`tests/test_backend_fastapi.py::ContractParityTests` —— 同一 fake manager 下**同时启动**旧 `CaptureServer` 与新 FastAPI（真实 Uvicorn），对 44 组请求逐字节比较 **状态码 + 响应体 + 关键响应头**。

```
python -m unittest tests.test_backend_fastapi
Ran 74 tests ... OK
```

> 只要本文件里的"必须一致"项与代码冲突，以对比测试为准；对比测试失败即视为回归。

---

## 1. 迁移原则

1. **HTTP 层只换框架，不换语义**：路由、状态码、错误体、响应头、序列化方式逐项对齐。
2. **状态所有者不变**：新路由调用同一个 `JobManager`，Phase 1 不复制、不重写任何业务逻辑。
3. **安全边界复用同一份代码**：Host/Origin 校验直接调用 `facebook_http_security.request_error`，请求体框架校验调用 `body_length`，避免两套实现漂移。
4. **阻塞调用移出事件循环**：所有 `JobManager` / ADB / 网络调用经 `run_in_threadpool`，但**结果语义不变**。
5. **失败不得伪装成功**：`ApiError` / `SchedulerStartError` / `KeyError` / `OSError` 的映射逐路由固定。

---

## 2. 逐接口对比

图例：`＝` 完全一致（对比测试逐字节验证）；`≈` 语义一致但存在下表列明的可见差异；`＋` 新增能力（旧服务没有）。

### GET

| 路由 | 旧 | 新 | 一致 |
|---|---|---|---|
| `/health` | `manager.health()` → 200 JSON | 同 | ＝ |
| `/guide` | `README.md` → `text/plain; charset=utf-8` | 同（读取失败时返回 500 JSON 而非断连） | ≈ |
| `/v1/setup` | `facebook_local.doctor()`；`(OSError,ValueError,RuntimeError)` → 400 `{error}` | 同 | ＝ |
| `/v1/capabilities` | `{platform, capture_pcap_supported}` | 同 | ＝ |
| `/v1/jobs/latest` | 200；`KeyError` → 404 `no jobs found` | 同 | ＝ |
| `/v1/batches/latest` | 200；`KeyError` → 404 `no batches found` | 同 | ＝ |
| `/v1/jobs/{id}` | 200；`KeyError`→404 `job not found`；`ApiError`→409 | 同 | ＝ |
| `/v1/jobs/{id}/progress` | 200；404 / 409 | 同 | ＝ |
| `/v1/jobs/{id}/result` | 200；404 / 409（未 completed） | 同 | ＝ |
| `/v1/jobs/{id}/artifacts/{name}` | 200 流式；`KeyError`→404 job；`ApiError`→404 | 同（1 MiB 分块、显式 `Content-Length`） | ＝ |
| `/v1/batches/{id}` | 200（与 `/progress` 同源）；`KeyError`→404 `batch not found` | 同 | ＝ |
| `/v1/batches/{id}/progress` | 同上 | 同 | ＝ |
| `/v1/batches/{id}/artifacts/{name}` | 200；404 | 同 | ＝ |
| `/v1/avatars/{id}` | 302 + `Location` + `Cache-Control: private, max-age=300`；`ApiError`→503 | 同（多一个 `content-length: 0`） | ≈ |

### POST

| 路由 | 旧 | 新 | 一致 |
|---|---|---|---|
| `/v1/jobs` | **202** + `public_job()` | 同 | ＝ |
| `/v1/batches` | **202** + `batch_progress()` | 同 | ＝ |
| `/v1/jobs/{id}/cancel` | 200；`KeyError`→404 `job not found` | 同 | ＝ |
| `/v1/batches/{id}/cancel` | 200；`KeyError`→404 `batch not found` | 同 | ＝ |
| `/v1/workers/reload` | 200；`ApiError`→409；`SchedulerStartError`→503；`OSError`→503 | 同（错误文案逐字保留） | ＝ |
| `/v1/workers/{id}/enable` | 200；404 / 409 / 503 | 同 | ＝ |
| `/v1/workers/{id}/disable` | 200；404 / 409 / 503 | 同 | ＝ |
| `/v1/jobs/{id}/reconcile-result-persistence` | 200；严格 ack 校验失败→400；404 / 409 / 503 | 同 | ＝ |

### 静态资源

| 路由 | 旧 | 新 |
|---|---|---|
| `/`、`/index.html`、`/styles.css`、`/app.js` | 读 `web/`，`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、固定 CSP | 同（白名单仍取自 `facebook_capture_api.STATIC_ASSETS`） |

---

## 3. 横切契约（逐条对照实现位置）

| 契约 | 旧实现 | 新实现 | 一致性 |
|---|---|---|---|
| origin-form 目标、无反斜杠、无控制字符 | `request_error()` | 同函数，经 `HeaderBag`/`_RequestView` 适配 ASGI scope | ＝ |
| Host 唯一、回环、端口匹配 | `request_error()` | 同上（端口取 `scope["server"][1]`） | ＝ |
| Origin 同源 / `Sec-Fetch-Site` | `request_error()` | 同上 | ＝ |
| 禁止 `Transfer-Encoding` | `body_length()` | 中间件对**所有** POST 先校验 | ＝ |
| `Content-Length` 唯一数字、≤65536 | `body_length()` | 同 | ＝ |
| 空 body 提交 | 400 `invalid request body size` | 同 | ＝ |
| body 短于声明长度 | 400 `incomplete request body` | 同 | ＝ |
| JSON 解析错误文案 | 400 + `str(JSONDecodeError)` | 同 | ＝ |
| 严格 ack（唯一字段、布尔 true、禁重复键） | `do_POST` 内联 | `api/request_body.require_explicit_acknowledgment` | ＝ |
| JSON 序列化 | `ensure_ascii=False, separators=(",",":")` | `security.guard.json_bytes`（同一表达式） | ＝ |
| `Content-Type: application/json; charset=utf-8` | 手写 | `ApiJSONResponse` | ＝ |
| `Cache-Control: no-store` + `nosniff` | 手写 | `ApiJSONResponse` / 静态 / 产物 | ＝ |
| 产物 `Content-Type` | CSV 加 charset，其余 `mimetypes` 原值 | 同（**故意不**给 JSON 加 charset） | ＝ |
| `download=1` 精确匹配整个 query | `urlparse(path).query == "download=1"` | `scope["query_string"] == b"download=1"` | ＝ |
| `Content-Disposition` inline/attachment | 按后缀白名单 | 同 | ＝ |
| 路径边界（绝对路径/`..`/符号链接/越界/空文件） | `_checked_artifact_path` | 仍由 `JobManager.artifact()` 执行，服务层只补元数据 | ＝ |
| 未知路径 / 方法不匹配 | 404 `{"error":"not found"}` | 404 同体（405 被映射为 404） | ＝ |
| 路径空段折叠（`/health/`） | `_segments()` 丢弃空段 | `normalize_path_scope()` | ＝ |

---

## 4. 有意保留的差异（必须知道，且都已验证）

| # | 差异 | 旧行为 | 新行为 | 影响 | 理由 |
|---|---|---|---|---|---|
| D1 | 未实现的方法（`PUT`/`DELETE`/`OPTIONS`/`HEAD`） | 501 + HTML 错误页 | 404 JSON `not found`（HEAD 对 GET 路由返回 200 空体） | 无客户端使用；行为更一致（fail-closed） | FastAPI/Starlette 路由语义；不伪造 501 HTML |
| D2 | 未捕获异常 | 连接被断开（无响应） | 500 JSON `{"error":"internal server error"}` + 服务端 traceback 日志 | 客户端至少能拿到可诊断响应 | 可观测性要求 |
| D3 | `Server` / `Date` 响应头 | `FacebookCaptureAPI/0.1 Python/3.13.x` | `uvicorn` / 标准 `date` | 无 | 由 ASGI 服务器决定 |
| D4 | 302 头像响应 | 无 `Content-Length` | `content-length: 0` | 无 | Starlette Response 默认 |
| D5 | `/guide` 读取失败 | 抛异常断连 | 500 JSON | 无（README 恒存在） | 同上 D2 |
| D6 | 访问日志 | `print("[api] ...")` 每请求一行 | 关闭 Uvicorn access log；Phase 6 接入结构化日志 | 日志格式变化，端口/健康检查脚本不依赖 | 结构化日志在 Phase 6 统一 |
| D7 | 新增文档路由 | 无 | `/docs`、`/redoc`、`/openapi.json` | 只增不改 | 开发者体验；不影响旧路径 |
| D8 | 进程数 | 1（ThreadingHTTPServer） | 1（Uvicorn `workers=1`，入口强制拒绝 >1） | 无 | 进程内调度器不可共享 |
| D9 | 批次 GET 的 `ApiError` | 未捕获 → 断连 | 500 JSON | 无 | 同 D2 |

> D1/D2/D3/D4/D5/D9 均不改变任何**成功路径**的响应；D6/D7 不影响客户端。

---

## 5. 路由清单校验

`RouteInventoryTests.test_every_legacy_route_is_registered` 硬编码 22 条业务路由（14 GET + 8 POST），断言 FastAPI 路由表**包含且不遗漏**；`test_static_whitelist_matches_legacy` 断言静态白名单与 `STATIC_ASSETS` 完全相同。新增旧接口时，这两条测试会先失败。

---

## 6. 客户端兼容性核对清单

| 客户端 | 依赖 | 结论 |
|---|---|---|
| `facebook_api_client.py` | 路径、状态码、JSON 字段、202 提交 | 未改动；对比测试覆盖其全部调用路径 |
| `web/app.js` | `/health`、`/v1/jobs*`、`/v1/batches*`、静态资源 | 未改动；路由与响应体逐字节一致 |
| `web_v2/app.js` | 同上 + 批次选择/进度 | 未改动；`test_ui_v2_and_safety` 通过 |
| `examples/*.py/.mjs/.ps1/.sh` | 示例请求 | 未改动；`test_api_examples` 通过 |
| `start_ui_v2.py`（UI 反向代理） | 只代理，不解析 | 未改动 |

---

## 7. 回滚

- `start_facebook_capture_api.ps1 -Legacy` 直接回到旧入口，无需改代码。
- `python facebook_capture_api.py` 仍然完全可用（旧模块未做任何修改）。
- 新入口：`python -m facebook_backend [--host 127.0.0.1] [--port 8765] [--serial emulator-5554]`。
