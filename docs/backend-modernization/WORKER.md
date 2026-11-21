# Worker 与执行器说明

- 代码：`facebook_backend/scheduler/device_registry.py`、`facebook_backend/executors/`
- 配置：`facebook_workers.json`（静态配置的唯一来源）
- 运行时（当前）：`JobManager` 内的主备串行调度线程

---

## 1. Worker 是什么

**1 个 Worker = 1 台 Android 设备 = 1 个 AVD + 1 个 serial + 一对端口**。
Worker 之间不共享设备，也不共享运行目录。

`facebook_workers.json`（version 1）：

```json
{
  "version": 1,
  "workers": [
    {
      "worker_id": "worker-01",
      "serial": "emulator-5554",
      "avd_name": "fb-x86_64-api35",
      "capture_port": 8081,
      "native_port": 18766,
      "enabled": true,
      "cooldown_seconds": 900,
      "frida_ssl_unpin": false
    }
  ]
}
```

校验规则（`validate_worker_config`）：

- 至少一个 Worker，至少一个 `enabled`；
- `worker_id` / `serial` / `avd_name` 三者各自唯一，`worker_id` 匹配 `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`；
- 两个端口都在 1024–65535，彼此不同，且**全局唯一**（含跨 Worker）；
- `cooldown_seconds >= 0`。

> 配置只存在 JSON 里，数据库的 `devices` 表只是它的投影（租约要引用 `devices.id`）。
> 不要在两处同时维护设备配置。

## 2. 执行器契约

```python
class ExecutorPort(Protocol):
    def execute(self, assignment: WorkerAssignment, *, cancel_event=None) -> ExecutionOutcome: ...
```

`ExecutionOutcome` 的两条硬规则：

1. 只有**真正确认成功**才返回 `status="completed"`，且必须带 `result`；
2. `process_state` 只在执行器**亲自验证**过时才填 `"stopped"`；否则填 `None`（未知）。
   调度器不会替你猜。

现有实现：

| 执行器 | 用途 | 行为 |
|---|---|---|
| `MockExecutor` | 调度器测试 | 脚本化结果，零延迟，记录调用 |
| `LegacyCaptureExecutor` | 真实路径 | 调用 `facebook_capture_api.run_capture_job`（PowerShell / macOS Python / ADB 生命周期 / 结果校验都在里面）；失败时 `process_state=None` |

## 3. 平台执行路径（未改动）

| 平台 | 入口 | 说明 |
|---|---|---|
| Windows | `run_facebook_capture.ps1`（由 `build_capture_command` 组装） | PowerShell 执行器，mitmproxy + frida + ADB |
| macOS | `macos/facebook_macos.py` | Python 执行器，同样的采集与清理语义 |
| Android | ADB | `ensure_emulator` 负责启动、等待 boot、配置代理 |

这三条路径本轮**没有改动**，`build_capture_command` / `ensure_emulator` / `run_capture_job`
的行为由既有测试覆盖。

## 4. 主备与冷却（当前运行时）

- 只有一个**活跃 Worker**（`_active_worker_id`），其余是备用；
- 活跃 Worker 失败且确认设备不可用时，`_activate_next_worker_locked` 切到下一台；
- 失败后按 `cooldown_seconds` 冷却，冷却期内不派发；
- 限流只冷却设备、不换设备（`"rate-limit" in str(exc)` 分支）；
- `_capture_lease_uncertain` 为真时**停止派发**，直到进程/清理被确认。

## 5. 最大运行时间与停机

| 参数 | 默认 | 环境变量 |
|---|---|---|
| 单个捕获最大运行时间 | 6 小时（`0` = 无限） | `FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS` |
| 停机等待 | 30 秒 | `FACEBOOK_SHUTDOWN_WAIT_SECONDS` |

- 超时后 `run_capture_job` **先终止进程树**，成功才抛超时错误；终止失败抛
  `WorkerUnavailableError`，不重试、不释放设备。
- 停机**不杀进程**：等待超时就退出，下次启动按进程身份判定（`match` → 阻塞）。

## 6. 运维动作

```powershell
# 重载配置（不重启服务）
curl -X POST http://127.0.0.1:8765/v1/workers/reload

# 启停某台 Worker（运行中的活跃 Worker 不能禁用）
curl -X POST http://127.0.0.1:8765/v1/workers/worker-02/enable
curl -X POST http://127.0.0.1:8765/v1/workers/worker-02/disable
```

- 重载失败会返回 409/503，**不要盲目重试**，先看 `/health` 里的 `capture_lease`。
- 设备异常时先看 `adb devices`、再看 `/health` 的 `android.state` 与 `proxy`。

## 7. 新增一台设备

1. 创建 AVD（与现有 AVD 不同的名字与端口）；
2. 在 `facebook_workers.json` 里加一条（serial / avd_name / 两个端口都唯一）；
3. `POST /v1/workers/reload`；
4. 确认 `/health` 里出现该 Worker 且 `android.state=device`；
5. 若要用多设备并行，先跑 `tests/test_backend_scheduler.py` 的隔离用例，再逐台放开。

## 8. 自检

```powershell
.venv\Scripts\python.exe -m unittest tests.test_backend_scheduler tests.test_facebook_worker_bootstrap -v
```
