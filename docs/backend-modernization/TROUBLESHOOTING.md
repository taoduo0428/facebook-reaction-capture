# 故障排查

按"现象 → 原因 → 处理"组织。所有命令都在项目根目录、已激活 `.venv` 的前提下执行。

---

## 1. 服务起不来

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: facebook_http_security` | 克隆不完整（该文件曾未被跟踪） | `git pull`；确认 `git ls-files facebook_http_security.py` 有输出 |
| `refusing non-loopback bind` | `--host 0.0.0.0` 之类 | 只绑 `127.0.0.1`，远程访问用 SSH/VPN 隧道 |
| `the in-process scheduler requires exactly one uvicorn worker` | `FACEBOOK_API_WORKERS>1` | 设回 1：进程内队列/租约不能跨进程共享 |
| `Address already in use` | 端口被占 | `Get-NetTCPConnection -LocalPort 8765 -State Listen` 找到进程；或用 `-Port` 换端口 |
| 启动即退出，日志有 `sqlalchemy.exc.OperationalError` | `FACEBOOK_DB_URL` 指向的库没迁移 | `python -m alembic upgrade head` |

## 2. 请求被拒绝

| 状态/错误 | 原因 | 处理 |
|---|---|---|
| `403 {"error":"untrusted Host"}` | `Host` 头不是回环或端口不匹配 | 用 `http://127.0.0.1:8765`；反向代理必须保留原始 Host/端口 |
| `403 {"error":"cross-origin requests are not allowed"}` | 页面与 API 不同源 | 用项目自带 UI（同源），或走 `start_ui_v2.py` 的代理 |
| `403 {"error":"cross-site requests are not allowed"}` | `Sec-Fetch-Site: cross-site` | 同上 |
| `400 {"error":"transfer encoding is not supported"}` | 用了 chunked 上传 | 带明确的 `Content-Length` |
| `400 {"error":"request body exceeds 65536 bytes"}` | 请求体过大 | 分批提交（批次上限 100 个帖子） |
| `503 {"error":"service is shutting down; no new jobs are accepted"}` | 正在停机 | 等重启完成；停机期间不接收新任务是有意的 |

## 3. 任务状态异常

| 现象 | 含义 | 处理 |
|---|---|---|
| 一直是 `cancelling` | 取消已请求，但**停止未被证实** | 查 `process_state`：`stop-unverified` 表示无法确认进程消失。查 `Get-Process -Id <pid>`、`adb devices`；确认后再取消一次 |
| `failed` + `interrupted by API service restart` | 重启时无法确认旧进程已停 | 正常保护：确认没有残留进程后重新提交 |
| `failed` + `capture timed out after Ns` | 超过 `FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS` | 任务可能真的卡住；查 `capture.log`。需要更长就把该变量调大 |
| `failed` + `capture process did not stop after timeout` | 超时后进程树没杀干净 | **不要**直接重跑；先手工确认进程已退出，否则设备会被占住 |
| `queued` 一直不动 | 设备不可用/冷却中/租约阻塞 | 看 `/health` 的 `worker`、`android.state`、`capture_lease.state`、`workers[].cooldown_until` |
| `/health` 里 `capture_lease.state=uncertain` | 有未确认的进程或传输清理 | 停止派发是有意的；确认进程消失并完成清理后重启服务 |
| 限流后任务没有换设备 | **设计如此** | 限流只冷却设备，任务钉在原设备（`jobs.pinned_device_id`） |

## 4. 设备相关

| 现象 | 原因 | 处理 |
|---|---|---|
| `android.state=offline` | 模拟器没起来或 ADB 断开 | `adb devices`；`emulator -list-avds`；重启 AVD |
| `android.state=unauthorized` | 未授权 USB 调试 | 在模拟器里允许调试 |
| `proxy` 为空且 `proxy_error` 有值 | 设置代理超时 | `adb shell settings get global http_proxy`；重新配置 |
| `worker configuration has duplicate capture_port` | 两台设备端口冲突 | 改 `facebook_workers.json`，`POST /v1/workers/reload` |
| `cannot disable the active worker while a job is running` | 活跃 Worker 正在跑任务 | 等任务结束，或先取消任务 |

## 5. 数据库与迁移

| 现象 | 原因 | 处理 |
|---|---|---|
| `alembic.util.exc.CommandError: Can't locate revision` | 库版本比代码旧/新 | `alembic current` → `alembic upgrade head` / `downgrade -1` |
| `sqlite3.OperationalError: database is locked` | 有长事务或外部进程占用 | 本项目 `busy_timeout=30000`；检查是否有别的进程开着同一个库（服务与导入工具不要同时跑） |
| 导入工具退出码 `2` | 有无效 manifest / 孤儿批次 / 冲突 | 看报告 JSON 的 `invalid_*`、`orphan_batches`、`conflicts`；处理后再跑 |
| 导入工具拒绝执行 | 探测到 8765 还在响应 | 先停服务；确实要带服务跑用 `--force`（会记 `forced_while_running`） |
| 导入后任务数对不上 | 有跳过/冲突 | 报告的 `counts_match=false` 会列出 `missing_jobs`/`missing_batches` |

## 6. 日志与指标

| 现象 | 原因 | 处理 |
|---|---|---|
| 日志是 JSON 一行一条 | 结构化日志（Phase 6） | 用 `jq`/`Select-String` 过滤 `event`、`job_id` |
| 日志里没有 `job_id` | 该行不是任务上下文内的日志（如启动日志） | 正常；任务相关日志都带上下文 |
| `/metrics` 里没有 `facebook_api_requests_total` | 还没有请求进来 | 先打一次 `/health` |
| 指标里 `route` 是模板而不是真实 id | 有意的基数控制 | 正常 |
| 找不到 `job_id` 对应的执行时长 | 执行时长是聚合直方图，不按 job 建标签 | 看 `/metrics` 的 `facebook_job_execution_seconds`；单个任务的时间线用 `job_events` 的 `timestamp` 推算 |

## 7. 测试相关

| 现象 | 原因 | 处理 |
|---|---|---|
| `Ran 1323 tests ... OK (skipped=9)` 是 v1.1.1 正常基线 | — | 数量随用例增加而变化；若明显减少或出现 FAIL/ERROR 才说明环境有问题 |
| `test_rejects_symlinked_component` skipped | Windows 无创建符号链接权限 | 正常；macOS CI 会执行 |
| `test_facebook_navigation_latency` 偶发失败（`sum(sleeps)>4000`） | 既有时间敏感用例，毫秒抖动 | 重跑；见 `TESTING.md` §3 |
| `test_layout` 报清单缺文件 | 新增测试没加进 `release-files.txt` | 补上对应条目 |

## 8. 收集诊断信息

```powershell
curl.exe -s http://127.0.0.1:8765/health
curl.exe -s http://127.0.0.1:8765/v1/jobs/latest
curl.exe -s http://127.0.0.1:8765/metrics | Select-String "facebook_"
Get-Content runs\<job_id>\capture.log -Tail 50
adb devices
Get-Process python,emulator,qemu-system-x86_64 -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime
```

报告问题时请附：`/health` 输出、`job_id`、`capture.log` 末尾 50 行、`adb devices`。
**不要附** `facebook.local.json`、证书私钥、账号 Cookie。
