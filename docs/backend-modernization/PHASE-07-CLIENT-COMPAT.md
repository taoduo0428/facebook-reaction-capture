# Phase 7 — 前端与客户端兼容 阶段报告

- 阶段：**Phase 7 / 前端与客户端兼容**
- 目标：证明**没有改动一行前端/客户端代码**的前提下，`web/`、`web_v2/`、
  `facebook_api_client.py` 在新 FastAPI 后端上行为不变。
- 结果：**完成**（19 条新测试）；全量 **1102 tests OK（4 skipped）**。
- 说明：本阶段是**验证阶段**，除测试与文档外没有修改产品代码。

---

## 1. 问题

Phase 1 已经用"同一 fake manager 双服务器逐字节对比"锁住了 HTTP 契约，但那只覆盖
**后端视角**。真正会坏的地方在客户端：

1. UI 里写死的路径（含模板字符串）如果和后端路由漂移，浏览器里才会炸；
2. 状态词表（`cancelling` vs `cancelled`、`completed_with_errors`）如果 UI 没映射，
   用户看到空白徽标；
3. `facebook_api_client.py` 是**真实代码**，它对 202/404/`{"error": ...}` 的解析必须
   真的能跑通，而不是靠 mock 断言；
4. 产物下载用的是 `artifact.url` 里的 `{job_id}` 占位符 + `?download=1` 精确匹配，
   任何一边改动都会静默 404 或变成 inline。

## 2. 设计

### 2.1 静态"客户端 → 路由"契约

从 `web/app.js`、`web_v2/app.js` 里提取每一处 `api(...)` 调用（含模板字符串），
归一化 `${...}` / `{param}` 后与 FastAPI 路由表比对：

```python
calls = js_api_calls(source)          # [("GET", "/v1/jobs/${...}/progress"), ...]
missing = [c for c in calls if (method, normalize(c.path)) not in routes]
```

删掉或重命名任何一条后端路由，这条测试立刻失败 —— 而不是等用户打开页面。

### 2.2 状态词表契约

- `web_v2/app.js` 的引号字符串集合必须覆盖 `JOB_STATUSES | BATCH_STATUSES`；
- `cancelling` 与 `cancelled` 的**标签必须不同**（"停止中" vs "已停止"）——
  这正是"任务被取消时展示真实停止状态"：后端只有在验证停止后才写 `cancelled`，
  UI 只要把两个状态分开渲染，语义就是真的；
- 两个 UI 的 `api()` 都必须读 `payload.error`（后端错误体就是 `{"error": ...}`）。

### 2.3 真实客户端端到端

启动**真实 Uvicorn**（fake manager，无设备），用 `FacebookApiClient` 走完整流程：

```
submit → get_status → wait(completed) → result → download → cancel
```

另外覆盖：`wait` 对 `failed`/`cancelled` 抛 `FacebookJobError`（带状态）、
未知 job → `FacebookApiHttpError(404, "job not found")`、批次流程、
`?download=1` 返回 `attachment`、以及客户端**不重试 POST**（源码守卫）。

### 2.4 批次一致性

用真实的 `write_batch_outputs` 生成批次汇总（2 个 job：1 completed + 1 failed），断言：

- 批次状态 = `completed_with_errors`；
- `status_counts` 与逐任务状态完全一致；
- 写出的 `batch-summary.json`（UI 下载的那个文件）与 API 返回一致；
- 运行中的批次状态落在 `BATCH_STATUSES` 内。

## 3. 实际修改

| 文件 | 内容 |
|---|---|
| `tests/test_backend_client_compat.py` | 19 条测试（新增） |
| `tests/test_layout.py` | 必跑清单加入该模块 |
| `.github/workflows/check.yml` | CI 加入该模块（Windows + macOS） |
| `release-files.txt` | +2 条 |

**没有修改** `web/`、`web_v2/`、`facebook_api_client.py`、`examples/`、`start_ui_v2.py`。

## 4. 测试

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_client_compat
Ran 19 tests in 1.022s
OK

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 1102 tests in ...s
OK (skipped=4)
```

| 测试类 | 覆盖 |
|---|---|
| `FrontendRouteContractTests`（4） | `web/app.js` 与 `web_v2/app.js` 的每个 `api()` 调用都有对应路由；产物 `{job_id}` 占位符可解析；`?download=1` 字面量一致 |
| `StatusRenderingContractTests`（3） | 后端全部 job/batch 状态都有 UI 标签；旧 UI 处理终态；两个 UI 都读 `error` 字段 |
| `CancellationRenderingTests`（1） | `cancelling` 与 `cancelled` 标签必须不同（取消请求 ≠ 已停止） |
| `ApiClientEndToEndTests`（6） | 真实 Uvicorn 上的 submit/poll/wait/result/download/cancel；failed/cancelled → `FacebookJobError`；404 → `FacebookApiHttpError`；批次流程；`?download=1` → attachment；客户端无重试 |
| `BatchConsistencyTests`（2） | 批次汇总计数与逐任务状态一致；`batch-summary.json` 与 API 一致；批次状态在词表内 |
| `FrontendAssetTests`（2） | 4 个静态资源仍可访问且带 CSP；`web_v2` 仍由自己的启动器提供 |

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| 前端文件 | 未修改（`git diff` 为空） |
| Python 客户端 | 未修改；真实 HTTP 端到端通过 |
| 静态资源 | 4 个路径 200 + CSP（Phase 1 已有，这里再确认） |
| 契约 | 双服务器逐字节对比测试仍通过 |

## 6. 未做（如实说明）

- **浏览器实测**：没有真实浏览器/模拟器，本轮用"路由契约 + 状态词表 + 真实客户端 HTTP"
  三条自动化证据替代；`web_v2` 的 DOM 渲染逻辑本身未在浏览器里跑过。
- **SSE 实时进度 / Worker/Device 管理页**：任务书列为"未来可以增加"，本轮不引入。
- **真实设备上的 UI 联调**：需要模拟器与登录态，属 Phase 9 部署验收范围。

## 7. 提交

- `test(compat): prove the existing UIs and Python client work against FastAPI`
