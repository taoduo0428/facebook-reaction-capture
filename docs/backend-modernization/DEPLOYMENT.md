# 部署说明（Windows / macOS / 本地开发 / Docker）

- 适用版本：[v1.1.1](RELEASE_NOTES_v1.1.1.md)
- 原则：**真实设备执行必须原生运行**（需要本机 ADB、Android SDK 与可见模拟器窗口）。Docker 只承载只读控制面。
- 只允许绑定回环地址。需要远程访问时走经过认证的私有隧道（SSH/VPN）；入口会拒绝非回环 bind。

---

## 1. 运行形态

| 形态 | 命令 | 说明 |
|---|---|---|
| 后端（默认） | `python -m facebook_backend --host 127.0.0.1 --port 8765 --serial emulator-5554` | FastAPI + Uvicorn，**单进程** |
| Windows 一键 | `start_facebook_capture_api.ps1` | 默认走新入口 |
| Windows 回滚 | `start_facebook_capture_api.ps1 -Legacy` | 旧标准库服务器，仍按同一 store 选择 |
| 旧入口 | `python facebook_capture_api.py` | 保留；与 FastAPI 共用状态所有者锁 |
| 本地控制台 | `python facebook_local.py start` | 经 `facebook_backend.__main__` 启动，与上面同一实现 |
| Docker（只读控制面） | `docker compose up -d backend` | 无设备、无 ADB；只提供健康/指标/只读查询，提交返回 503 |

## 2. 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `FACEBOOK_API_HOST` | `127.0.0.1` | 监听地址（仅回环） |
| `FACEBOOK_API_PORT` | `8765` | 监听端口 |
| `FACEBOOK_CAPTURE_SERIAL` | `emulator-5554` | 默认设备 serial |
| `FACEBOOK_API_MAX_REQUEST_BYTES` | `65536` | 请求体上限 |
| `FACEBOOK_API_REQUEST_TIMEOUT` | `15` | 连接超时（秒） |
| `FACEBOOK_API_LOAD_LOCAL_CONFIG` | `1` | 是否加载 `facebook.local.json` |
| `FACEBOOK_API_WORKERS` | `1` | **必须为 1**，>1 直接拒绝启动 |
| `FACEBOOK_CAPTURE_MAX_RUNTIME_SECONDS` | `21600`（6h） | 单次捕获硬上限，`0` = 无限 |
| `FACEBOOK_SHUTDOWN_WAIT_SECONDS` | `30` | 停机等待（不杀进程） |
| `FACEBOOK_LOG_LEVEL` | `INFO` | 结构化日志级别 |
| `FACEBOOK_JOB_STORE` | `auto` | `auto` 新装自动建库、旧清单要求迁移；`database` 强制要求已迁移；`manifest` 显式回退 |
| `FACEBOOK_DB_URL` | `sqlite+pysqlite:///<项目根>/data/facebook_backend.db` | SQLite 地址，供 database 模式、Alembic 与离线导入工具共用 |

## 3. Windows 10/11 原生部署

前置：Python 3.12/3.13、Android SDK + `platform-tools/adb`、一个已登录 Facebook 的 AVD。

```powershell
# 1) 依赖
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip check

# 2) 配置
Copy-Item facebook.local.example.json facebook.local.json      # 按需修改
Copy-Item facebook_workers.example.json facebook_workers.json  # 按需修改
.venv\Scripts\python.exe facebook_local.py doctor              # 环境自检

# 3) 启动（新安装默认 auto：首次会自动建库并迁移）
.\start_facebook_capture_api.ps1 -Port 8765 -Serial emulator-5554

# 4) 验证
curl.exe http://127.0.0.1:8765/health
curl.exe http://127.0.0.1:8765/metrics | Select-Object -First 5
```

开机自启：用「任务计划程序」以当前用户身份运行 `start_facebook_capture_api.ps1`，**不要**用 Session 0 服务方式启动模拟器（可见窗口需要交互式会话，`launch_visible_in_user_session.ps1` 处理这个边界）。

## 4. macOS（Apple Silicon）原生部署

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp facebook.local.example.json facebook.local.json
.venv/bin/python facebook_local.py doctor
./START.command                 # 或 .venv/bin/python -m facebook_backend
```

- 详细步骤见 [macOS Codex 部署 Runbook](../../macos/CODEX_DEPLOYMENT_RUNBOOK.md)（Apple Silicon、Android 35 ARM64、frida、证书）。
- 常驻可用 `launchd`（`~/Library/LaunchAgents/*.plist`），同样只绑定 `127.0.0.1`。
- 进程身份：macOS 通过 `psutil` 读取内核提供的亚秒级出生时间（`kinfo_proc.p_starttime`），与 Windows 一样做精确比对。读不到时返回 `unknown`，恢复逻辑保持阻塞，不会把「不确定」当成「已停止」。
- 进程组：POSIX 采集器以独立 session 启动，停止时先核对组长身份，再确认整组消失才报告成功。

## 5. 本地开发

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m unittest discover -s tests -t .   # 全量
```

开发时不启动真实采集也能验证控制面：`FACEBOOK_JOB_STORE=auto` + 空 `api-jobs/` 会建一个空库并正常启动；`GET /v1/workers`、`/v1/devices`、`/v1/scheduler` 会返回已登记设备与空队列。

## 6. SQLite 新安装

```powershell
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///C:/data/facebook_backend.db"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic current     # 应为 0004_job_event_context (head)
```

- 不设 `FACEBOOK_JOB_STORE` 时默认 `auto`：项目根 `data/` 下的默认库缺失、且没有历史清单时，启动器会自己建库并迁移。
- 已有库或自定义路径要求 schema 已迁移且与代码匹配；启动会校验 Alembic 版本、表、主键、索引、外键与一致性。
- 应用与离线工具共用 `state_owner(root, database_url)` 互斥锁：同一状态根或同一数据库文件不会被两个进程同时当作所有者。

## 7. 旧 Manifest 迁移（v1.1.0 → v1.1.1 升级路径）

**必须停机**。服务在 8765 上仍响应时，导入工具会拒绝执行。

```powershell
# 0) 先确认版本与备份目录可用
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip check

# 1) 只统计，不写库
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --dry-run

# 2) 建库并迁移
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///C:/data/facebook_backend.db"
.venv\Scripts\python.exe -m alembic upgrade head

# 3) 正式导入（默认在 backups/ 下新建时间戳目录；单事务写入 + 读回核对）
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --apply --backup --report migration-apply-001.json

# 4) 只读复核（逐字段比对源与目标）
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --verify --report migration-verify-001.json

# 5) 以数据库为状态来源启动
$env:FACEBOOK_JOB_STORE = "database"
.\start_facebook_capture_api.ps1
```

- 退出码 `0` 通过；`2` 表示校验/迁移未通过（看报告里的 `invalid_*`、`orphan_batches`、`conflicts`）；`1` 表示命令外围错误。
- 每次报告用新文件名；`--backup` 必须指向全新目录。
- **不要删除** `api-jobs/`、`api-batches/`、`runs/`：它们是回退与取证依据。
- 想先保持原样：设 `FACEBOOK_JOB_STORE=manifest` 即可继续用历史清单模式，不需要迁移。

## 8. 数据库备份与恢复

```powershell
# 备份（停机后做；直接复制打开中的 .db 可能漏掉 WAL 里的提交）
.venv\Scripts\python.exe -c "import sqlite3;src=sqlite3.connect(r'data/facebook_backend.db');dst=sqlite3.connect(r'C:/backup/facebook_backend-2026-11-23.db');src.backup(dst);dst.close();src.close()"

# 恢复：停服 → 用备份覆盖 data/facebook_backend.db → 删掉残留的 -wal/-shm → 启动并核对 alembic current
```

回退包（database → manifest）用离线工具导出到**全新目录**，不要覆盖现有清单：

```powershell
.venv\Scripts\python.exe -m facebook_backend.persistence.legacy_manifest_import --root . --rollback --rollback-dir C:/data/rollback-001 --report rollback-check-001.json
```

导出包包含一致的数据库副本与任务/批次清单。未决执行、未完成的传输恢复、失配结果、活动租约，或 v1 清单无法表达的设备绑定都会阻止回退。

## 9. Docker（只读控制面）

```bash
docker compose config
docker compose build backend
docker compose up -d backend
curl http://127.0.0.1:8765/health          # {"service":"ready","mode":"control-plane",...}
docker compose down
```

- 镜像里**没有** ADB、Android SDK、模拟器，因此**不拥有执行权**：入口是 `python -m facebook_backend.container`，只提供健康、指标与任务/批次/设备/租约的只读查询。
- 提交、取消、结果对账返回 **503**，并说明需要在原生宿主执行；这样可以避免「容器里恢复了现场任务却无法验证进程」的第二个状态所有者。
- 只挂 `./data`（SQLite）。`runs/`、`api-jobs/`、`api-batches/` 不挂载，它们属于原生执行器。
- 容器内监听 `0.0.0.0`，宿主仍只发布 `127.0.0.1:8765`；应用自身的 Host/Origin 守卫仍要求回环 Host 头。
- 构建与运行由 `.github/workflows/container.yml` 在 GitHub runner 上实测（config → build → up → healthcheck → 断言 503 → down）。**本机没有安装 Docker，这里引用的是 CI 结论**，不是本机结论。

## 10. 升级与回滚

```powershell
# 升级
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
# 重启服务（有界停机：停止接单 → 等待 → 不杀进程）

# 回滚 HTTP 入口（不改代码）
.\start_facebook_capture_api.ps1 -Legacy

# 回滚状态存储（切回历史清单；先确认导出副本已核对）
$env:FACEBOOK_JOB_STORE = "manifest"
```

停机语义：新 `POST` 立即 503；运行中的任务最多等 `FACEBOOK_SHUTDOWN_WAIT_SECONDS`；**不会**把任务标成已停止，下次启动按进程身份判定。若等待超时且工作线程仍存活，状态所有者锁与数据库连接会保留到线程真正退出。

## 11. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 启动报 `legacy manifests require a verified migration` | 有历史清单但库未迁移或收据缺失 | 按第 7 节迁移；或临时 `FACEBOOK_JOB_STORE=manifest` |
| 启动报 `owner lock unavailable` | 另一个进程仍持有同一状态根/库 | 先停掉旧进程；确认没有遗留的 `python -m facebook_backend` |
| 启动报 `database schema ... upgrade head` | 库版本落后于代码 | `alembic upgrade head` |
| 任务一直 `queued` | 无在线设备或设备被租约/冷却/`stop-unverified` 阻塞 | 看 `GET /v1/devices`、`GET /v1/scheduler` 与 `/health` |
| 取消后停在 `cancelling` | 进程未确认停止或传输未恢复 | 不要手工改库；按 `/health` 的 `capture_lease` 原因核对进程与代理 |
| `POST` 返回 503 `control plane` | 这是容器只读控制面 | 在原生宿主执行任务 |
| `429`/`1675004` | 平台限流 | 保留部分结果并冷却；不要换账号继续同一任务 |

更多现象见[排障文档](TROUBLESHOOTING.md)。

## 12. 上线检查清单

- [ ] `python -m pip check` 无破损依赖
- [ ] `.venv\Scripts\python.exe -m unittest discover -s tests -t .` 全绿
- [ ] `node --check web/app.js`、`node --check web_v2/app.js`、`node --check examples/capture_fetch.mjs`、`node --check perf/k6_api.mjs`
- [ ] `alembic current` 等于最新 head（`0004_job_event_context`）
- [ ] `tools/check_release_version.py --tag v1.1.1` 通过
- [ ] `/health` 里 `service=ready`；有设备时 `android.state=device`
- [ ] `/metrics` 能抓到 `facebook_api_requests_total`
- [ ] 只监听 `127.0.0.1`
- [ ] `facebook.local.json` / `facebook_workers.json` / 证书私钥没有入库（`git status` 干净）

## 13. 已知限制

1. **单进程**：调度与租约在进程内，Uvicorn 多 worker 会各自调度同一批任务，入口直接拒绝 `>1`。
2. **单活跃执行槽**：`max_concurrent_jobs=1`。多 Worker 只用于故障接管，不是并行租户；多设备真实并发未通过实机验收。
3. **旧清单不静默切换**：`auto` 检测到历史清单即拒绝启动并给出迁移指引；`manifest` 是显式回退。
4. **Docker 无执行能力**：只读控制面，不承载 Android 采集。
5. **真实设备验收**：按[测试说明 §5](TESTING.md#5-真实设备验收与自动化分开记录)在 Windows 与 macOS 分别记录；CI 通过不等于实机通过。
