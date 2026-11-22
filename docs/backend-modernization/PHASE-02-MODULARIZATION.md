# Phase 2 — 业务模块化 阶段报告

- 阶段：**Phase 2 / 业务模块化**（Phase 0 审计 + Phase 1 FastAPI Adapter 之后）
- 目标：把 `facebook_capture_api.py` 里**职责清晰、可独立验证**的部分下沉到
  `facebook_backend/*`，同时**不改变任何已验证行为**。
- 结果：**完成**。旧模块 5 518 → 5 069 行（−449），新增 8 个职责模块；
  新增 32 条测试；全量 **956 tests OK（4 skipped）**。

---

## 1. 问题

`facebook_capture_api.py` 5 518 行里混着七种职责：

| 职责 | 原位置 | 问题 |
|---|---|---|
| HTTP 路由 | `ApiHandler` | Phase 1 已迁出 |
| 请求归一化 | `normalize_*` | 与 HTTP 无关的业务契约，混在服务文件里无法单独测试 |
| 产物目录/边界 | `ARTIFACT_*`、`_checked_artifact_path` | 安全相关代码埋在 5 000 行文件中间 |
| 进程身份/终止 | `_process_creation_time` 等 | 平台相关，含 Windows ctypes 与 POSIX 进程组 |
| 原子写 | `_atomic_write_json/csv` | 持久化原语，Phase 3 数据库也要用 |
| 设备/Worker 配置 | `WorkerConfig`、`load_worker_config` | 调度器输入，属于 Scheduler 层 |
| 调度/进程表/租约 | `JobManager` | 真正有状态的核心，**本轮不动** |

## 2. 设计

### 2.1 依赖方向

```
domain/normalization  →  domain/limits, artifacts/catalog, domain/exceptions
artifacts/manager     →  artifacts/catalog, domain/exceptions
platform/process_manager  →  仅标准库
persistence/atomic_write  →  仅标准库
scheduler/device_registry →  domain/exceptions
```

新模块**只依赖标准库和其它新模块**，不 import `facebook_capture_api`；
唯一例外是 `compatibility/legacy.py`（Phase 1 已定义，它是唯一的接触面）。

### 2.2 为什么用"实现下沉 + 同名委托"

现有 924 条测试里有 **60 处** `mock.patch("facebook_capture_api.X")`，它们按
**符号名**拦截。如果把调用点也搬走，这些补丁会失效。虽然失效会**报错而不是静默**，
但一次性改 60 处补丁目标属于高风险大改，与"不破坏已验证能力"直接冲突。

因此 Phase 2 的判据不是"文件变小"，而是**实现只有一份**：

```python
# facebook_capture_api.py
def _process_creation_time(pid: int) -> float | None:
    """Wrapper kept so call sites/tests keep patching this exact symbol."""
    return process_manager.creation_time(pid)
```

- 新模块是**唯一实现**；
- 旧名字是**委托**，签名与语义不变；
- 调用点与补丁目标都还在原位，924 条既有测试**零改动**通过；
- 用两条测试证明"不是复制"：patch 新模块 → 旧符号结果随之改变；
  旧模块源码里不再出现该实现的特征串（如 `kernel32`、`GetProcessTimes`）。

### 2.3 类型身份共享

`ApiError` / `SchedulerStartError` / `WorkerUnavailableError` / `WorkerConfig`
的**类对象**移到 `facebook_backend`，旧模块 `from ... import` 回来：

```python
api.ApiError is facebook_backend.domain.exceptions.ApiError  # True
api.WorkerConfig is facebook_backend.scheduler.device_registry.WorkerConfig  # True
```

这样 `isinstance` 检查、`assertRaises(api.ApiError)`、以及
`compatibility/legacy.is_legacy_api_error()` 全部继续成立，而错误类型不再
"住在服务文件里"。`is_legacy_api_error` 因此不再需要 import 旧模块。

## 3. 实际修改

### 新增模块

| 文件 | 行数 | 职责 |
|---|---|---|
| `facebook_backend/domain/normalization.py` | 184 | `normalize_request` / `batch` / `post_url` / `collect` / `save_artifacts` / `reaction_scroll_limit` / `persisted_request` |
| `facebook_backend/domain/limits.py` | 21 | `MAX_BATCH_POSTS`、`REACTION_SCROLL_LIMIT_MIN/MAX/DEFAULT` |
| `facebook_backend/artifacts/catalog.py` | 66 | 9 个 job 产物 + 4 个 batch 产物的逻辑名/路径/必需集/分组 |
| `facebook_backend/artifacts/manager.py` | 123 | 路径边界（绝对路径/`..`/符号链接/越界/空文件）、产物元数据、完整性判定 |
| `facebook_backend/platform/process_manager.py` | 214 | PID 创建时间（Windows `GetProcessTimes`）、存在性探测、进程树终止、PID/时间戳归一 |
| `facebook_backend/persistence/atomic_write.py` | 54 | 同目录临时文件 + `os.replace` 的 JSON/CSV 原子写 |
| `facebook_backend/scheduler/device_registry.py` | 157 | `WorkerConfig`、默认 Worker、校验、加载、写回 |
| `tests/test_backend_modularization.py` | 302 | 32 条测试（见 §4） |

### 修改

| 文件 | 改动 |
|---|---|
| `facebook_capture_api.py` | 5 518 → 5 069 行；错误类、常量、归一化、进程原语、产物助手、原子写、Worker 配置改为导入/委托 |
| `facebook_backend/domain/exceptions.py` | +3 个运维错误类（同一对象，非复制） |
| `facebook_backend/domain/__init__.py` | 导出新的错误类 |
| `facebook_backend/compatibility/legacy.py` | 错误判定改用共享类型，不再依赖旧模块 |
| `release-files.txt` | +12 条（模块 + 新测试） |
| `docs/backend-modernization/ARCHITECTURE.md` | 状态表更新 + §6.1 拆分策略 |

**未改动**：调度逻辑、进程表、租约、取消、传输清理、结果判定、执行器、
`web/`、`web_v2/`、`facebook_api_client.py`、`macos/*`。

## 4. 测试

新增 `tests/test_backend_modularization.py`（32 条，1 条在本机 skip）：

| 测试类 | 覆盖 |
|---|---|
| `SingleImplementationTests` | 8 条：patch 新模块 → 旧符号结果改变（进程原语/归一/产物/原子写/设备配置）；`load_worker_config` 传 `base_dir=BASE_DIR`；`_process_identity_state` 仍通过旧符号组合（924 条既有测试的补丁依赖） |
| `SharedIdentityTests` | 4 条：错误类/`WorkerConfig`/归一化函数/产物常量是**同一对象** |
| `NoDuplicatedImplementationTests` | 1 条：旧模块源码不再含 `kernel32`、`GetProcessTimes`、归一化错误文案、产物边界文案、Worker 校验文案 |
| `ArtifactBoundaryTests` | 8 条：合法文件、`..`、绝对路径、空文件、缺失、符号链接（本机无权限→skip，CI macOS 会跑）、完整性规则、元数据状态机 |
| `WorkerRegistryTests` | 4 条：缺文件回退、端口重复、无 enabled、经旧 writer 往返 |
| `AtomicWriteTests` | 3 条：紧凑 UTF-8 + 结尾换行、CSV 保留 BOM、不留临时文件 |
| `NormalizationBehaviourTests` | 7 条：合法 URL、非 Facebook 域、有界采样显式位、范围校验、旧 manifest 保持 unlimited、批次去重与上限 |

### 执行结果

```
$ .venv\Scripts\python.exe -m unittest tests.test_backend_modularization
Ran 32 tests in 0.042s
OK (skipped=1)

$ .venv\Scripts\python.exe -m unittest discover -s tests -t .
Ran 956 tests in 131.337s
OK (skipped=4)
```

基线对照：Phase 1 结束时 `Ran 924 tests ... OK (skipped=3)`。
新增 32 条，**没有删除或弱化任何既有测试**。

skip 说明：`test_rejects_symlinked_component` 在本机 Windows 上创建符号链接
需要额外权限，故 skip；macOS CI 会真实执行。其余 3 条 skip 是既有基线。

## 5. 旧功能检查

| 检查项 | 结果 |
|---|---|
| `facebook_capture_api.py` 的公开名字 | 全部保留（错误类、常量、`WorkerConfig`、`normalize_*`、`_process_*`、`_artifact_*`、`_atomic_write_*`） |
| 既有测试 | 924 条**零改动**通过 |
| manifest 格式 | `_atomic_write_json` 委托后字节格式不变（紧凑 JSON + 结尾换行）；`write_worker_config` 仍写 `version: 1` |
| CSV 格式 | 仍为 `utf-8-sig`（BOM），`extrasaction="ignore"` |
| 产物边界 | 规则逐条未变（新增测试覆盖） |
| 进程终止 | 代码逐行搬移；`_terminate_known_pid` 仍留在旧模块（它内联自己的 taskkill/killpg 分支） |
| 错误语义 | 类型对象共享，`isinstance`/`assertRaises` 不变 |
| FastAPI 契约 | `tests.test_backend_fastapi` 75 条通过（含双服务器逐字节对比） |

## 6. 未做（明确留到后续阶段）

| 项 | 为什么现在不做 |
|---|---|
| Scheduler 队列/派发/租约 | 这些是**有状态**的：队列、`_capture_lease_uncertain`、`_worker_runtime` 都在 `JobManager` 里。只有 Phase 3（数据库）把状态搬出去，Phase 4 才能把调度器搬出去而不制造第二份状态 |
| Worker Runtime / Executor | 依赖 `run_capture_job`、`ensure_emulator`、`build_capture_command` 的调用点；同样等状态迁移 |
| `JobManager` 拆分 | 同上。本轮只搬"叶子"，不碰状态所有者 |
| 产物 sha256 | Phase 3 随 `artifacts` 表一起加（不改现有通过/失败规则） |

## 7. 风险与回滚

| 风险 | 说明 | 缓解 |
|---|---|---|
| 委托层变"第二实现" | 有人在旧模块里改逻辑 | `NoDuplicatedImplementationTests` + `SingleImplementationTests` 双向卡住 |
| 补丁目标失效 | 调用点被误搬 | 本轮**不搬调用点**；测试 8 条专门覆盖 |
| 行尾被改写 | 脚本写文件在 Windows 上转 CRLF | 已修复并校验（`crlf=0`，`.gitattributes` 要求 `*.py eol=lf`） |
| 回滚 | 新模块是纯增量 | 恢复 `facebook_capture_api.py` 到上一提交即可；`facebook_backend` 新模块不参与旧入口 |

## 8. 提交

- `refactor(backend): extract domain, process, artifact and registry modules`
  （包含 Phase 2 全部改动；`facebook_capture_api.py` 的 diff 里同时包含本分支
  此前**未提交**的共享契约/`contextvars` 改动——那是当前工作区的权威版本，
  基线测试一直跑的就是它）
