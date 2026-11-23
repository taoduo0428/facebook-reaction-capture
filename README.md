# Facebook Reaction 本地采集平台

输入一个 Facebook 帖子 URL，程序在本机 Android 模拟器中打开该帖，采集当前登录账号可见的 Reaction 名单，导出 CSV，并给出分页进度、限流状态和产物路径。全部状态、证据与账号都留在你自己的机器上。

- **解决的问题**：把「打开帖子 → 翻完 Reaction 分页 → 去重 → 校验目标与终点 → 导出 CSV」做成可重复、可恢复、可追踪的本地服务，而不是一次性脚本。
- **不提供的部分**：不附带账号、APK、证书私钥或模拟器镜像；不绕过平台权限；不做云端托管与多租户。

## 核心能力

| 能力 | 实际行为 |
|---|---|
| 设备感知编排 | 任务绑定到具体 Worker/设备；同一时刻只有一个活跃执行槽，备用 Worker 仅用于既定故障接管 |
| 持久化任务状态 | SQLite 保存任务、批次、尝试、事件与租约；新安装自动建库，旧 JSON 清单需显式离线迁移 |
| 单一调度主体 | 数据库模式下 `ProductionScheduler` 单线程按 SQL 顺序领取并结算，不允许多个派发者 |
| 可靠执行生命周期 | 取消、停止、传输清理、结果落库分别取证；证据不足就保留租约而不是猜 |
| 跨平台进程身份 | Windows 用 `GetProcessTimes` + Job Object，macOS 用内核出生时间；身份未知一律阻塞 |
| 产物完整性 | 流式 SHA-256 与 `artifact_integrity` 基线；轮询只比 stamp，不重复读盘 |
| 可观测性 | JSON 结构化日志、W3C `traceparent` 贯穿 HTTP→数据库→执行线程、`GET /metrics` |
| 兼容性 | 22 条历史业务路由逐字节保持；旧标准库入口保留为回滚路径 |

## 技术栈

Python 3.12 / 3.13 · FastAPI · Uvicorn（单进程）· Pydantic v2 · SQLAlchemy 2.x · Alembic · SQLite（WAL）· OpenTelemetry · Prometheus · k6 · unittest · GitHub Actions（Windows + macOS + 容器镜像）

## 快速开始

```powershell
# 1) 安装（Windows；macOS 用 .venv/bin/python 与 setup.sh --install-only）
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly

# 2) 启动（首次会生成本机配置；Windows 也可直接双击 START.cmd）
.venv\Scripts\python.exe -m facebook_backend --host 127.0.0.1 --port 8765 --serial emulator-5554

# 3) 提交一个任务
curl.exe -X POST http://127.0.0.1:8765/v1/jobs -H "Content-Type: application/json" `
  -d '{"post_url":"https://www.facebook.com/share/r/1Dg59X6ysJ/"}'

# 4) 看进度与结果（把 fb_xxx 换成上一步返回的 job_id）
curl.exe http://127.0.0.1:8765/v1/jobs/fb_xxx/progress
curl.exe http://127.0.0.1:8765/v1/jobs/fb_xxx/result

# 5) 下载产物（CSV 等；?download=1 得到 attachment）
curl.exe -OJ "http://127.0.0.1:8765/v1/jobs/fb_xxx/artifacts/facebook-reactor-users.csv?download=1"
```

一条命令跑完提交、轮询、导出：`.venv\Scripts\python.exe examples\capture_requests.py --url "<帖子URL>" --output reel.csv`。完整参数、五类 URL、批量与恢复语义见 [API 调用与 Python 客户端](API_USAGE.md) 和 [可运行示例](examples/README.md)。

**运行前提**：模拟器已按本文件后续章节准备好并人工登录 Facebook；没有设备时服务仍可启动，但任务不会被领取。

## 后端 v1.1.1（FastAPI + 数据库 + 设备感知调度）

当前版本范围见 [v1.1.1 发行说明](docs/backend-modernization/RELEASE_NOTES_v1.1.1.md)。HTTP 层为 **FastAPI + Uvicorn + Pydantic v2**，28 条路由声明（含 5 条只读运行态接口），历史 22 条业务路由的状态码、错误体、产物响应头与静态资源逐字节保持不变（同一 fake manager 下新旧服务器对比测试锁定）。Uvicorn 固定单进程：多进程会产生多个调度者。

```powershell
# 新入口（默认；start_facebook_capture_api.ps1 已默认走它）
.venv\Scripts\python.exe -m facebook_backend --host 127.0.0.1 --port 8765 --serial emulator-5554

# 旧入口（回滚用，无需改代码）
.venv\Scripts\python.exe facebook_capture_api.py
# 或 .\start_facebook_capture_api.ps1 -Legacy
```

### 状态存储：一个权威来源

| 配置 | 行为 |
|---|---|
| `FACEBOOK_JOB_STORE=auto`（默认） | 全新安装且没有历史清单时，自动创建 `data/facebook_backend.db` 并迁移到当前 head；检测到历史清单则**拒绝静默切换**，要求先做离线迁移与核验 |
| `FACEBOOK_JOB_STORE=database` | 始终要求数据库已迁移；启动校验 Alembic 版本、表、主键、索引、外键与一致性 |
| `FACEBOOK_JOB_STORE=manifest` | 显式回退到历史 JSON 清单模式，仍走原有执行循环 |

数据库模式下，SQLite 是任务、批次、尝试、事件与租约的权威状态来源；内存对象只作为兼容读视图。**不存在长期双写两个权威状态的设计。** 批次受理把父行、子任务、位置关系与创建事件放进同一事务；重启时批次成员关系只认数据库里已提交的关系。迁移与回退命令见[数据库与迁移](docs/backend-modernization/DATABASE.md)。

### 调度：单一执行主体

- **`ProductionScheduler`** 是数据库模式的生产调度与执行主体，只有一个线程；任务领取顺序由 SQL（`created_at, batch_id, batch_index, id` + 设备绑定）决定。
- **`JobManager`** 保留为兼容门面与运行时管理：HTTP 语义、内存读视图、主备切换、关停协调。
- **`NativeAttempt`** 负责一次执行的证据收集：进程登记、取消传播、停止与传输清理凭据、结果构建。
- **原生执行器**（Windows PowerShell runner、macOS Python runner）继续负责真实设备操作，采集算法未改动。
- **显式 manifest 回退**仍使用历史执行循环，两条路径不会同时派发。

详见[调度说明](docs/backend-modernization/SCHEDULER.md)与[架构与分层](docs/backend-modernization/ARCHITECTURE.md)。

### Worker 与并发

当前 `max_concurrent_jobs=1`：**只有一个活跃执行槽**。配置里可以有多台 Worker，但备用 Worker 只用于既定故障接管顺序（确认原设备故障、停止与清理完成后才交接），不代表多设备并行执行。多设备真实并发尚未通过实机验收，因此不宣传为生产级并行能力。详见[Worker 与设备](docs/backend-modernization/WORKER.md)。

### 恢复与取消的判定条件

- **进程身份**：Windows 用 `GetProcessTimes` 并在打开的句柄上复核，同时用 Job Object 证明整棵自有进程树退出；macOS 用内核提供的亚秒级出生时间。身份 `unknown` 一律阻塞。
- **租约过期不等于可释放**：TTL 只是观察，释放必须有停止与清理证据。
- **取消请求不等于已取消**：`cancelling` 必须等到进程真实停止、传输恢复、结果落库才进入 `cancelled`。
- **重启不伪装续跑**：只有「可证明从未启动」的尝试会被重新排队，其余保留租约与诊断，等待人工核对。

详见[架构评审](docs/backend-modernization/V1.1-FINAL-ARCHITECTURE-REVIEW.md)与[排障](docs/backend-modernization/TROUBLESHOOTING.md)。

### 产物完整性

`facebook_backend/artifacts/integrity.py` 在写入方声明完成后才取样，使用流式 SHA-256（1 MiB 分块）写入 `artifact_integrity` 基线；校验先比 `size:mtime_ns:inode`，只有 stamp 变化才重算，因此进度轮询不会反复读盘。文件变化报告 `changed` 而不自动「修复」；历史文件没有可信摘要时保留 `unknown`，不会伪造历史完整性证明。API 的文件名、路径语义与响应头保持不变。

### 可观测性

结构化日志带 `job_id/batch_id/worker_id/device_serial/attempt_id/trace_id/request_id`；W3C `traceparent` 从 HTTP 请求贯穿到数据库与执行线程；`GET /metrics` 输出 Prometheus 文本，标签限基数（未匹配路径统一 `unmatched`、非标准方法统一 `OTHER`），不把 job_id 或 URL 作为标签。敏感字段不进入日志、指标或导出的 span。见 [PHASE-06](docs/backend-modernization/PHASE-06-OBSERVABILITY.md)。

### 部署形态

| 形态 | 用途 | 边界 |
|---|---|---|
| Windows 原生 | 真实采集 | PowerShell runner + 本机模拟器 |
| macOS 原生（Apple Silicon） | 真实采集 | Python runner + ARM64 模拟器 |
| Docker | **只读控制面** | 无 ADB/SDK/模拟器；健康、指标、任务/批次/设备/租约只读查询可用，提交返回 503 |

Docker 不是 Android 执行环境，容器内没有真实 Android 采集能力；设备执行必须在原生宿主完成。详见[部署说明](docs/backend-modernization/DEPLOYMENT.md)。

### 能力索引

| 能力 | 文档 |
|---|---|
| 架构与分层 | [ARCHITECTURE](docs/backend-modernization/ARCHITECTURE.md) |
| API 兼容 | [API_COMPATIBILITY](docs/backend-modernization/API_COMPATIBILITY.md) |
| 数据库与迁移 | [DATABASE](docs/backend-modernization/DATABASE.md) |
| 调度 | [SCHEDULER](docs/backend-modernization/SCHEDULER.md) |
| Worker/执行器 | [WORKER](docs/backend-modernization/WORKER.md) |
| 可观测性 | [PHASE-06](docs/backend-modernization/PHASE-06-OBSERVABILITY.md) |
| 部署 | [DEPLOYMENT](docs/backend-modernization/DEPLOYMENT.md) |
| 测试 | [TESTING](docs/backend-modernization/TESTING.md)：本机 Windows 全量 1330 项通过（9 项跳过；v1.1.1 发布提交为 1323） |
| 排障 | [TROUBLESHOOTING](docs/backend-modernization/TROUBLESHOOTING.md) |
| 性能 | [PERF_REPORT](docs/backend-modernization/PERF_REPORT.md) · [v1.1 实测](docs/backend-modernization/V1.1-PERFORMANCE.md) |
| 最终架构评审 | [V1.1-FINAL-ARCHITECTURE-REVIEW](docs/backend-modernization/V1.1-FINAL-ARCHITECTURE-REVIEW.md) |

## 延伸阅读

- 环境与版本基线：[已验证环境与 APK/证书基线](STABLE_BASELINE.md)
- 交给另一位 Codex 接手：[Codex 接手指南](CODEX_PROJECT_GUIDE.md)
- 真实浏览器样本与「名单 vs 仅数字」：[FACEBOOK_BROWSER_SAMPLES.md](FACEBOOK_BROWSER_SAMPLES.md)
- 历史快照（不代表今天的在线状态）：[2026-09-16 验证记录](VALIDATION_2026-09-16.md) · [2026-09-09 状态](CURRENT_STATUS_2026-09-09.md)

## 平台入口

| 平台 | 推荐入口 | 运行边界 |
| --- | --- | --- |
| Windows 10/11 x64 | [Windows 部署与运行](WINDOWS_DEPLOYMENT.md)，然后 `START.cmd` | Android 35 Google APIs x86_64；主备串行，不是并行租户 |
| Apple Silicon Mac | [M4 一键配置](macos/QUICKSTART.md)，然后 `START.command` | Android 35 Google APIs ARM64；真实 M4/Facebook 深抓仍需实机验收 |

Windows 的逐项命令、Frida/CA/Helper 准备、停止和故障处理见 [WINDOWS_DEPLOYMENT.md](WINDOWS_DEPLOYMENT.md)。macOS 的 Codex 执行清单见 [macos/CODEX_DEPLOYMENT_RUNBOOK.md](macos/CODEX_DEPLOYMENT_RUNBOOK.md)；`macos/README.md` 中的旧适配器仅用于兼容性诊断。

## 目录速览

`facebook_local.py`/`facebook_setup.py` 负责本机配置、依赖和环境诊断；`facebook_capture_api.py` 与 `facebook_api_client.py` 提供本地 API；`facebook_android_capture.py` 是 Android 采集执行器；`web/` 和 `web_v2/` 是页面；`macos/` 保存 Apple Silicon 配置与诊断层。`api-jobs/`、`api-batches/`、`runs/` 是本机运行状态和证据目录，不属于发布源码。

## 系统架构

网页或 Python 客户端把 URL 提交给本地 HTTP API；JobManager 持久化任务并通过 capture lease 保证串行执行。Windows 调用 PowerShell runner，macOS 调用 Python Android runner；执行器负责 ADB 导航、采集代理、分页和清理。解析器随后校验目标、来源及分页终点，生成单帖 CSV；批次汇总以 `job_id` 为帖子边界，同一个用户出现在不同任务时分别保留。Worker 接管必须先确认旧进程停止及传输状态恢复。

## 下载后如何开始

Windows 10/11 x64 使用现有 PowerShell 采集链路；Apple Silicon Mac 使用独立 Python Android 执行器，仍待 M4 实机验收。建议 Python 3.12 或 3.13、16 GB 内存（双模拟器建议 24 GB）。**Mac 接收者直接按 [一键配置说明](macos/QUICKSTART.md) 操作，下面的 START.cmd 和 x86_64 步骤仅供 Windows 使用。**

1. 安装 [Python 3.12 或 3.13](https://www.python.org/downloads/)，勾选 Add python.exe to PATH；安装 [Android Studio / SDK](https://developer.android.com/studio)。代码下限是 Python 3.12，3.12/3.13 是当前验收版本。
2. 解压仓库到自己的可写目录，双击 **START.cmd**（或直接双击 **START_API_AND_EMULATOR.cmd**）。首次自动创建 `.venv`、安装 `requirements.txt`、生成本机配置，成功后在窗口打印 `http://127.0.0.1:8766/`，请手动打开该地址；API/UI 后台窗口隐藏，Android 模拟器窗口保持可见。安装失败会保留提示，修复网络后重试即可。
3. 按下节准备 Android 环境并手动登录 Facebook。页面能打开不代表模拟器已准备好。双击 **CHECK.cmd** 查看 Python、SDK、AVD、代理和端口检查结果。
4. 在页面中每行输入一个 URL，点击“开始抓取”。任务会进入单执行器串行队列；其它启用 Worker 只是主备候选。选择帖子行查看流程、分页、人数和产物；“停止批次”可取消任务。

Mac M1–M4 首次配置可直接双击 **CONFIGURE.command**。它会创建本机配置、检查或安装 Android 35 ARM64 组件、只创建缺失的专用 AVD，并按官方 SHA-256 校验 Frida；合法取得的 Facebook APK 放进 `setup-input/` 后再次运行即可。已有 AVD、账号和登录状态不会覆盖。安装完成后在模拟器内手动登录 Facebook，再双击 START.command。若已有环境只想启动页面，直接双击 START.command；不要把 `facebook.setup.local.json`、APK、Frida 文件或 `.setup-cache/` 上传到 GitHub。

连续提交多个 URL 时，同一 Worker 会复用已经启动的模拟器，Windows 优先附加已有 Facebook 主进程；进程不存在时才启动，故障恢复仍可能重启应用。每条任务独立建立采集代理、输出文件和目标校验，避免上一帖数据混入。项目导航指定 Facebook 包名，直接进入 Facebook；Mac 配置入口还设置 Android 的 Facebook 链接打开偏好。分页节流、限流冷却和完整性检查仍然保留。

v2 页面和 `/v1/jobs`、`/v1/batches` API 默认执行全量 Reaction 分页，持续到现有 `terminal` 终点逻辑；缺省或 `null` 的 `reaction_scroll_limit` 不会设置有限上限。仅当请求显式提供 `1..300` 的整数时，任务才按 bounded sample 运行并标记为非全量结果。

Windows 手动排错时可给 `run_facebook_capture.ps1` 加 `-ColdStartFacebook`（别名 `-ColdStart`）恢复原先冷启动行为。日常页面批量抓取使用默认热启动，无需设置此参数。

Windows `START.cmd` 完成后，API/UI 后台进程会继续运行，Android 模拟器窗口保持可见。正常停止请先在页面取消正在运行的批次，等待其收尾，再双击 **STOP.cmd**；它只停止本项目识别到的 API/UI、采集辅助进程、watchdog 和已配置 AVD，不停止 ADB server 或其他模拟器。macOS `START.command` 运行 API 的终端会保持前台，此时先取消任务并确认清理，再用 Ctrl+C 收尾。下次直接双击对应入口，不会重复安装未变更的依赖，也不会覆盖已有配置。Windows 仅安装依赖可运行 `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly`，Mac 对应为 `bash setup.sh --install-only`。

## 本机配置

首次启动生成 `facebook.local.json` 和 `facebook_workers.json`，这两个文件不进入 Git。

```json
{
  "sdk_root": "",
  "avd_home": "",
  "port": 8765,
  "open_browser": true,
  "network": {"mode": "direct", "upstream_url": ""}
}
```

空路径由 `ANDROID_SDK_ROOT`/`ANDROID_HOME`、Android Studio 默认位置和 `ANDROID_AVD_HOME` 解析；也可改成接收者自己的绝对路径。Windows 的旧配置若没有 `network` 字段会保留原 7897 上游模式，新配置建议显式选择 `direct` 或填写无凭据的 `upstream_url`。配置只影响本项目及其子进程，不修改系统 PATH 或代理/TUN 设置。

首次生成的 Worker 使用 capture `18081`，避开常见的 `8081` 服务冲突。已有 Worker 配置完全保留。每台的 `worker_id`、`serial`、`avd_name`、capture/native 端口必须唯一。API 端口不要和 Worker 端口重复。

## 首台模拟器配置（首次需要完成）

仓库不附带 Android SDK、Facebook APK、账号、证书私钥或模拟器磁盘。以下步骤在你自己的 Windows 环境执行；Facebook 版本和页面布局变化可能需要重新验收。

1. 在 SDK Manager 安装 **Android SDK Platform-Tools、Android Emulator、Android SDK Command-line Tools (latest)、Android SDK Platform 35、Android 35 Google APIs x86_64 系统镜像**。选择可 `adb root` 的 Google APIs 镜像，不能用禁止 root 的 Google Play 镜像。按安装器提示接受 SDK 许可。只有需要构建可选 UI Helper 时才需要额外安装 **Build-Tools 35.0.0**。
2. 在 Device Manager 创建名为 `fb-x86_64-api35` 的 AVD，建议 1080×1920、4 CPU、3072 MB RAM；名称需和 Worker 配置一致。只创建新的 AVD，勿覆盖其他账号的磁盘。
3. 新加坡等可直接访问 Facebook 的环境保留 `network.mode: direct` 即可。需要上游 HTTP 代理时，在 `facebook.local.json` 中设置 `network.mode: upstream` 和自己的 `upstream_url`，例如 `http://127.0.0.1:7897`。工具不会自动启用 TUN。
4. 安装合法取得、与 x86_64 AVD ABI 一致的 Facebook APK 并人工登录。可使用 SDK 的 `adb -s emulator-5554 install 路径.apk`，拆分 APK 用同一版本的完整 x86_64 `install-multiple` 集合；不要混用 arm64 split。账号验证码和设备确认在模拟器内手工完成。
5. 本机 `.venv\Scripts\frida.exe --version` 与 Android 的 frida-server 必须版本相同。本依赖清单使用 17.17.0；从 [Frida Releases](https://github.com/frida/frida/releases) 取得对应 Android x86_64 ELF，解压后用 `adb root`、`adb push` 放到 `/data/local/tmp/fb-fs17` 并 `chmod 755`。采集 runner 会在需要 SSL unpin 时以 daemon 方式启动并检查 `pidof fb-fs17`；不要直接运行无参数 server 占住前台窗口。既有账号环境应先核对版本，勿覆盖正在运行的工具。
6. 如需构建项目的可选 UI Helper：安装 JDK 和 **Build-Tools 35.0.0** 并设置 JAVA_HOME，使 `javac` 和 `keytool` 可用。执行下面的命令生成本机 debug 签名并构建 APK（已有 keystore 不要重新生成）。构建只编译仓库自带的 Java 源码；默认 Mac runner 使用 ADB XML，不依赖 Helper。

```powershell
# 仅在该文件不存在时执行 keytool；不是 Facebook 的签名密钥。
keytool -genkeypair -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android -keyalg RSA -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
powershell -ExecutionPolicy Bypass -File .\android-ui-dump-helper\build-helper.ps1 -SdkRoot "<your Android SDK root>"
```

7. 初始化自己本机的 mitmproxy CA：执行 `.venv\Scripts\mitmdump.exe --listen-host 127.0.0.1 --listen-port 18089`，确认启动后 Ctrl+C，公开证书位于 `%USERPROFILE%\.mitmproxy`。安装 OpenSSL（Git for Windows 通常附带），保证 `openssl` 可用。不要复制其他人的 CA 私钥；任务使用前还须由证书脚本把公开证书安装并验证到 Android system/Conscrypt store。
8. 从配置启动当前 SDK/AVD 的 PowerShell 环境，安装并验证本机公开 CA；UI Helper 仅在选择该可选路径时构建和安装。可先用 `.venv\Scripts\python.exe facebook_local.py shell` 进入已加载配置的终端，再执行：

```powershell
. .\facebook_transport.ps1
. .\facebook_certificate.ps1
# AVD 必须使用 writable system；已有 AVD 正常关闭后再执行该启动脚本。
.\start_facebook_stable.ps1 -Serial emulator-5554 -AvdName fb-x86_64-api35 -CapturePort 18081 -NativePort 18766 -WritableSystem -SkipFacebookUiCheck
adb -s emulator-5554 root
Ensure-FacebookMitmCertificate -Serial emulator-5554
# 只有已构建可选 Helper 时才执行；默认链路可用 ADB/UI XML。
Ensure-FacebookUiDumpHelper -Serial emulator-5554 -BasePath $PWD.Path
```

Android 15 的系统与 Conscrypt CA 挂载由证书脚本验证，重启后采集启动器会恢复。当前端勾选 PCAP 时，还要求模拟器可执行 `tcpdump`；如果没有安装 tcpdump，先取消 PCAP，CSV、解析报告、MITM 与进度仍可用。

9. 先用一个自己可访问的小帖子做低频验证：

```powershell
.\probe_facebook_reaction_health.ps1 -Serial emulator-5554 -PostUrl "https://www.facebook.com/作者/posts/数字帖子ID" -PostId "数字帖子ID" -ProxyPort 18081 -EnableFridaSslUnpin
```

实际参数以 `Get-Help .\probe_facebook_reaction_health.ps1 -Full` 为准。Probe 需要 `initial-ready`、准确的来源帖子和真实记录。随后在页面提交一条小任务，到达终止页、严格目标匹配并生成 CSV 才算采集链路完成。

## 增加独立 Worker

首台已就绪后，在 `.venv\Scripts\python.exe facebook_local.py shell` 打开的环境中执行 `add_facebook_worker.cmd`，详见 [FACEBOOK_WORKER_BOOTSTRAP.md](FACEBOOK_WORKER_BOOTSTRAP.md)。新增工具从在线源设备复制程序文件，创建独立数据目录；新账号由使用者手动登录，Probe 验收后才启用。每个帖子固定在一个 Worker，不在限流后换账号继续同一任务。

## URL 与完成状态

标准数字帖子、`share/p`、`share/r`、`story.php` 有历史完整任务；普通 `pfbid`、规范 `pfbid` 变化、群组多图、群组 permalink、Reel 和原始 Watch URL 均已有真实 Android `initial-ready` 证据。`videos`、`share/v` 等仍只有解析、代码测试或较弱历史证据。各类 Probe 只证明目标、入口和首屏成功，逐类全量仍需到达分页终点，详见 [FACEBOOK_LINK_SUPPORT.md](FACEBOOK_LINK_SUPPORT.md)。

页面接受 URL 并不保证名单可枚举。已删除、当前账号无权查看、只有媒体身份或页面不展示名单的情况会失败或返回不可枚举信息。完成必须满足目标匹配、来源一致、到达分页终点和完整性校验。`terminal` 表示当前账号可见列表到终点。限流结果保留为部分结果，不等同于完整成功。

## 文件、维护与排错

- 任务记录：`api-jobs/`；批次记录：`api-batches/`；结果：`runs/`；新增模拟器状态：`worker-bootstrap/`。从页面查看具体目录和下载 CSV。原始记录可能含会话信息，仅留在本机。
- 服务不可达：检查启动窗口；8765 被其他服务占用时修改本机配置端口。已有服务仅在识别到本项目 health 时复用。
- 模拟器 offline：检查 SDK/AVD 配置及 Android 启动状态；不要把离线误判为 Facebook 限流。
- capture 端口被占用：先识别占用者，修改对应 Worker 端口；不要杀掉未知服务。
- 1675004：平台限流，保留部分数据并冷却；不要反复提交。冷却按 Worker 记录。
- API 重启后历史任务可加载；排队任务、以及确认旧采集进程已停止的运行中任务可以重新排队，保留此前 attempt/checkpoint。旧进程仍存在或身份无法确认时拒绝接管。重新排队会重验目标并去重，不表示从旧分页游标继续，详见 [API 恢复语义](API_USAGE.md#取消和重启恢复)。
- 官方默认页面是 `web/`，独立 v2 页面可通过 `start_ui_v2.ps1` 启动在 8766，但需要先配置并启动主 API。
- 当前完整静态/单元门禁以 [`.github/workflows/check.yml`](.github/workflows/check.yml) 的 Windows/macOS 矩阵为准；它包含依赖检查、平台合约、API/取消/导航/产物/队列测试、UI 合约、Node 语法和源码导出。下载后的接收者还必须按本平台完成设备、人工登录、CA/Frida 和真实终点 CSV 验收；CI 通过或历史 release-gate 产物都不能替代实机证据。

## 本地测试

测试文件集中在 `tests/`；独立 PowerShell XML 夹具在 `tests/manual/`。完整命令与目录约定见 [测试说明](tests/README.md)。在项目根目录运行 `python -m unittest discover -s tests -t .` 可发现全套 Python 测试（当前 1330 项、9 项平台跳过），原来的单模块命令改用 `tests.test_xxx`。

先按平台安装依赖，在项目根目录运行基础回归：

```powershell
# Windows
.venv\Scripts\python.exe -m unittest tests.test_facebook_capture_api tests.test_facebook_cancel_transport tests.test_analyze_facebook_capture tests.test_facebook_capture_orchestrator
.venv\Scripts\python.exe -m pip check
```

```bash
# macOS
.venv/bin/python -m unittest tests.test_facebook_capture_api tests.test_analyze_facebook_capture tests.test_facebook_android_capture tests.test_facebook_mac_device tests.test_facebook_macos
.venv/bin/python -m pip check
```

安装 Node.js 后，两端都可运行 `node --check web/app.js` 和 `node --check web_v2/app.js`。以上是基础集合；发布时还须执行 [CI 工作流](.github/workflows/check.yml) 对应平台的全部命令。测试使用隔离夹具，不代表已通过当前账号的真实分页验收。真实任务、取消后恢复和 CSV 验证步骤见平台部署文档及 [验证记录](VALIDATION_2026-09-16.md)。

## GitHub 发布准备

本地工作目录混有个人文档与采集证据。执行 `python tools/export_source.py`，根据 `release-files.txt` **明确文件清单**生成 `dist/facebook-source-时间.zip`，只包含可交付源码、模板和说明。解压到新目录验证后，再把审查过的清单文件提交到现有仓库的工作分支；不要上传整个历史工作目录。导出器拒绝软链接、不存在的文件和不合法路径，同时输出 SHA-256 清单以便核对。

[项目仓库](https://github.com/taoduo0428/facebook-reaction-capture) 已公开，可直接克隆或通过 Code → Download ZIP 下载。仓库当前未附带开源许可证。固定版本源码及发行记录见 [GitHub Releases](https://github.com/taoduo0428/facebook-reaction-capture/releases)。
