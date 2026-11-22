# 部署说明（Windows / macOS / Docker）

- 适用版本：[v1.0.1](RELEASE_NOTES_v1.0.1.md)
- 原则：**后端原生运行优先**；Docker 只用于后端开发环境，**Android 执行器必须原生运行**
  （需要本机 ADB、Android SDK 与可见模拟器窗口）。

---

## 1. 运行形态

| 形态 | 命令 | 说明 |
|---|---|---|
| 后端（默认） | `python -m facebook_backend --host 127.0.0.1 --port 8765 --serial emulator-5554` | FastAPI + Uvicorn，**单进程** |
| Windows 一键 | `start_facebook_capture_api.ps1` | 默认走新入口 |
| Windows 回滚 | `start_facebook_capture_api.ps1 -Legacy` | 旧标准库服务器 |
| 旧入口 | `python facebook_capture_api.py` | 保留，可用 |
| 本地控制台 | `python facebook_local.py start` | 仍用旧服务器 |
| Docker（仅后端） | `docker compose -f docker-compose.yml up --build backend` | 无设备、无 ADB，用于 API 开发/联调 |

**只允许绑定回环地址。** 需要远程访问时用经过认证的私有隧道（SSH / VPN），
不要直接监听 `0.0.0.0`——入口会拒绝非回环 bind。

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
| `FACEBOOK_JOB_STORE` | `manifest` | FastAPI 任务与批次存储，离线迁移并核对后可选 `database` |
| `FACEBOOK_DB_URL` | `sqlite+pysqlite:///data/facebook_backend.db` | SQLite URL，供 FastAPI 的 database 模式、Alembic 与离线导入工具使用 |

## 3. Windows 原生部署

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

# 3) 数据库（可选，用于迁移/审计）
$env:FACEBOOK_DB_URL = "sqlite+pysqlite:///$PWD/data/facebook_backend.db"
.venv\Scripts\python.exe -m alembic upgrade head

# 4) 启动
.\start_facebook_capture_api.ps1 -Port 8765 -Serial emulator-5554

# 5) 验证
curl.exe http://127.0.0.1:8765/health
curl.exe http://127.0.0.1:8765/metrics | Select-Object -First 5
```

开机自启：用「任务计划程序」以当前用户身份运行 `start_facebook_capture_api.ps1`，
**不要**用 Session 0 服务方式启动模拟器（可见窗口需要交互式会话，
项目里已有 `launch_visible_in_user_session.ps1` 处理这个边界）。

## 4. macOS 原生部署

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp facebook.local.example.json facebook.local.json
.venv/bin/python facebook_local.py doctor
./START.command                 # 或 .venv/bin/python -m facebook_backend
```

- 详细步骤见 `macos/CODEX_DEPLOYMENT_RUNBOOK.md`（Apple Silicon、Android 35 ARM64、frida、证书）。
- 常驻可用 `launchd`（`~/Library/LaunchAgents/*.plist`），同样只绑定 `127.0.0.1`。
- macOS 上 `_process_creation_time` 返回 `None`（无 PID 创建时间校验），恢复时按
  `unknown` 处理——宁可阻塞也不猜。

## 5. Docker（仅后端开发环境）

```bash
docker compose -f docker-compose.yml up --build backend
curl http://127.0.0.1:8765/health
```

- 镜像里**没有** ADB、Android SDK、模拟器：它只跑 HTTP + 数据库 + 调度组件。
- 不要在容器里挂 `platform-tools/` 去控制宿主模拟器——设备路径必须原生。
- 数据卷：`./data`（SQLite）、`./runs`（产物）。生产建议仍用原生部署。
- **本机没有安装 Docker，镜像未实际构建验证**。文件已按"只暴露回环"的约束写好，
  CI 里有静态检查（端口必须是 `127.0.0.1:8765:8765`、镜像里不得出现 ADB/emulator、
  必须是单 worker）。首次使用请自行 `docker compose up --build backend` 并回报结果。

## 6. 升级与回滚

```powershell
# 升级
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
# 重启服务（会走有界停机：停止接收新任务 → 等待 → 不杀进程）

# 回滚到旧入口（不改代码）
.\start_facebook_capture_api.ps1 -Legacy

# 回滚数据库
.venv\Scripts\python.exe -m alembic downgrade -1
```

停机语义：`POST` 提交立即返回 503；运行中的任务最多等 `FACEBOOK_SHUTDOWN_WAIT_SECONDS`；
**不会**把任务标成已停止，下次启动按进程身份判定。

## 7. 上线检查清单

- [ ] `python -m pip check` 无破损依赖
- [ ] `.venv\Scripts\python.exe -m unittest discover -s tests -t .` 全绿
- [ ] `node --check web/app.js`、`node --check web_v2/app.js`
- [ ] `alembic current` 等于最新 head
- [ ] `/health` 里 `service=ready`、`android.state=device`、`capture_lease.state=available`
- [ ] `/metrics` 能抓到 `facebook_api_requests_total`
- [ ] 只监听 `127.0.0.1`
- [ ] `facebook.local.json` / `facebook_workers.json` / 证书私钥没有入库（`git status` 干净）

## 8. 已知限制

1. **单进程**：`JobManager` 的队列/租约在进程内，Uvicorn 多 worker 会各自调度同一批任务，
   入口直接拒绝 `>1`。
2. **FastAPI 存储可选**。默认 `manifest`，切到 `database` 后 SQLite 负责持久化任务和批次。旧系统按[切换步骤](STATE-STORE-CUTOVER.md)停机、导入并核对。旧入口 `facebook_local.py start` 和 `-Legacy` 使用默认 manifest，数据库模式回退前先验证导出副本。
3. **派发与执行**。`Dispatcher.plan()` 判断活跃 worker 能否接单。`JobManager` 消费队列、执行任务、保存结果和处理接管。独立的 `Scheduler.tick()` 在组件测试中运行。
4. 真实设备验收按[测试说明 §5](TESTING.md#5-真实设备验收与自动化分开记录)在对应平台记录。
