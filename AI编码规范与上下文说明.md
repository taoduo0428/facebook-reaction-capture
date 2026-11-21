# AI 编码规范与上下文说明

> 当前状态入口（2026-09-09）：工程规范继续适用；会变化的 URL 支持、运行证据、测试和 macOS 验收结论以 `CURRENT_STATUS_2026-09-09.md` 为准。

> 适用范围：`C:\Users\lenovo\Desktop\facebook`
>
> 汇总日期：2026-09-02（Asia/Shanghai）
>
> 资料范围：当前工作区、项目交接文档、项目记忆、代码与测试，以及可用的历史对话记忆。

## 0. 文档定位与事实等级

本文用于开发环境迁移和后续 AI/工程师接手。所有结论按以下优先级解释：

1. **当前实测**：2026-09-02 在本机直接查询或运行得到；
2. **当前代码/文档**：工作区现有实现和权威交接文档明确规定；
3. **历史证据**：过去运行产生的结果，只能证明当时状态；
4. **未确认**：当前没有版本锁或直接证据，不允许猜测。

发生冲突时，以当前代码和最新权威文档为准。例如，旧历史中出现过 guest 使用 `10.0.2.2:7897/8081`，但当前交接文档已经明确：正式基线是 guest `127.0.0.1` 配合 `adb reverse`，`10.0.2.2` 仅是旧方案。

## 1. 项目目标与边界

### 1.1 项目目标

该工作区用于在受控 Android 模拟器中，通过真实 Facebook App UI 触发 App 自身的 GraphQL 请求，再由 mitmproxy/mitmdump 被动观察响应，最终输出当前登录账号可见、可枚举的 Reaction/Share 结构化结果。

目标交付形态是本地或私网可访问的异步捕获服务：

```text
Facebook Post URL + exact post_id
-> 本地 Capture API
-> 单 Android worker 队列
-> 严格打开并验证目标帖子
-> 只读触发 Reaction/Share UI
-> mitmdump 捕获 App GraphQL
-> analyzer 严格按 source_post_id 过滤
-> API 返回带边界说明的结构化结果
```

### 1.2 工作区不是普通单仓库

- `C:\Users\lenovo\Desktop\facebook` 当前不是 Git 仓库，且未发现嵌套 `.git`。
- 它是研发现场和证据工作区，混合了代码、文档、运行结果、抓包、模拟器工具、归档和备份。
- 不要把工作区根目录当成可直接提交的产品仓库。
- 成熟能力应抽取到单独的 `facebook-capture-service` 私有仓库；当前研发现场原样保留。

### 1.3 明确不在目标内的行为

- 不破解 TLS 密钥；只在受控测试环境中让 App 流量经过受控代理。
- 不绕过登录、访问控制、CAPTCHA、风控或限流。
- 不偷取、伪造或重放 token/cookie，不轮换账号、IP、代理或设备指纹来规避限制。
- 不承诺获取 Facebook 全站或全部用户，只报告当前账号实际可见和可枚举的结果。

## 2. 针对本项目的具体架构要求

### 2.1 分层与文件职责

#### 启动与运行时解析层

- `start_facebook_stable.ps1`：稳定启动 `fb-x86_64-api35`，尽早建立 ADB reverse，并验证 Android 默认网络和 Facebook UI。
- `facebook_runtime.ps1`：统一解析 Python/PowerShell 运行时，管理捕获锁和子进程调用。
- 入口脚本不得各自硬编码一套 Python 查找逻辑；应复用运行时解析函数。

#### 设备与传输层

- `facebook_transport.ps1`：ADB、默认网络、reverse 映射、UI dump、ANR 识别、原生滚动等共享能力的唯一主要实现位置。
- `facebook_network_watch.ps1`：只负责修复已存在模拟器的网络；它不是 AVD 启动器。
- watchdog 必须使用互斥锁，避免多个实例同时修复同一设备。
- 捕获过程独占模拟器 ADB 通道时，应暂停后台 watchdog，避免其与 UI dump、代理切换或 App 启动并发。

#### 目标打开与 UI 探索层

- `open_facebook_post_readonly.ps1`：有界打开目标帖子，并在 fresh capture boundary 后严格验证目标。
- `explore_facebook_readonly.ps1`：只读触发 Reaction/Share/Comments UI，并负责深分页状态机。
- `run_facebook_capture.ps1`：总编排器；创建 run 目录、启动 mitmdump、打开目标、运行 explorer、分析结果，并在 `finally` 中恢复原代理和运行状态。

#### 抓包与安全存储层

- `facebook_capture_addon_safe.py`：正式安全抓包插件；对 header、cookie、authorization、token、request body 等敏感字段做裁剪或脱敏。
- `facebook_raw_flow_writer.py`：独立原始 HTTP flow 归档，仅用于受控调试；其产物默认视为敏感数据。
- `safe-facebook-http-capture.jsonl` 是最终 analyzer 的主要事实来源。
- `facebook-live-progress.json` 仅是轻量实时快照，供性能优化和进度判断使用，不能替代最终 JSONL。

#### 分析与汇总层

- `analyze_facebook_capture.py`：解析 GraphQL、目标匹配、source 过滤、用户和帖子导出。
- `facebook_capture_progress.py`：按 family/group 汇总实时分页状态。
- `facebook_batch_summary.py`、`facebook_release_gate.py`、`facebook_release_report.py`：批次、发布门禁与报告。
- analyzer 必须再做一次 source 过滤，防止历史 capture boundary 或错误帖子污染结果。

#### API 与任务层

- `facebook_capture_api.py`：本地异步 API、任务持久化、单 worker 队列、取消、恢复和结果构建。
- API 默认只允许绑定 `127.0.0.1`、`localhost` 或 `::1`。
- 接口保持异步：

```text
POST /v1/jobs
GET  /v1/jobs/{id}
GET  /v1/jobs/{id}/result
POST /v1/jobs/{id}/cancel
GET  /health
```

- 同一时间只允许一个 Android worker 操作模拟器；其他任务排队。
- queued/running/completed/failed/cancelled 状态必须持久化。
- 重启时未完成任务不能误报成功；运行中断应成为明确的失败/中断状态并保留部分证据路径。
- 取消 running 任务必须终止 capture 子进程树；重复取消 terminal 状态只能返回明确 no-op，不破坏已有结果。

### 2.2 网络拓扑必须保持双模式

正式日常网络：

```text
Facebook App
-> guest 127.0.0.1:7897
-> adb reverse tcp:7897 tcp:7897
-> host 127.0.0.1:7897
-> Clash / mihomo
-> Internet
```

临时抓包网络：

```text
Facebook App
-> guest 127.0.0.1:8081
-> adb reverse tcp:8081 tcp:8081
-> host mitmproxy/mitmdump :8081
-> upstream host 127.0.0.1:7897
-> Clash / mihomo
-> Internet
```

约束：

- 当前 Windows 基线依赖 Clash/mihomo 的 TUN、`strict-route` 和端口 `7897`。
- 抓包结束必须恢复原代理；异常路径也必须进入清理逻辑。
- 端口监听、HTTP 200、Burp 连接数或 UI 计数都不足以证明抓包成功；必须存在与目标帖子绑定的 fresh GraphQL 响应。
- 当前正式抓包器是 mitmproxy/mitmdump，不是 Burp Suite。

### 2.3 严格目标验证是不可删的正确性保护

进入 Reaction/Share 深分页前必须满足：

1. 输入是可验证的 canonical post-level permalink；
2. 具有 exact numeric `post_id`；
3. 打开动作之后建立新的 capture boundary；
4. resolved post ID 与期望 `PostId` 精确匹配；
5. family 首个 fresh 响应的 `source_post_id` 与目标一致；
6. analyzer 最终再次按 source 过滤。

以下内容不能单独作为目标证明：

- `/photo/?fbid=...` 媒体 URL；
- generic `story.php`；
- `SearchResultsGraphQL` 中出现目标数字；
- 手工拼接的 `/<author>/posts/<post>`；
- `fb://feed_story_permalink_non_encoded_id/<author_id>_<post_id>` fallback；
- UI 上恰好出现相同 Reaction 数字。

一旦 source mismatch：不接受增长、不继续翻页、不写入目标结果，并安全中止。

### 2.4 分页、限流与完成语义

- `ReactionProbeOnly` 只能检查首屏健康状态：禁用 page2、类别遍历、屏幕滚动和 fallback。
- Probe 必须得到 `probe_status=initial-ready`，才能进入 page2 或 3000+ 深抓取。
- `placeholder-or-unknown` 既不证明恢复，也不证明账号受限；应先恢复设备，再做一次有界 ProbeOnly。
- rate limit/401/403/429、ANR、source mismatch 必须显式记录，不能隐藏或降级成成功。
- 默认生产参数中的 `DurationSeconds=0`、`ReactionAbsoluteCap=0`、`MaxFlows=0` 表示不使用固定上限强制结束，不表示无限制地无退避重试。
- 限流退避必须有界、递增并带断路条件；禁止通过账号、IP 或指纹轮换规避限流。
- `terminal=true` 只表示当前账号/App 入口的可见列表已枚举到终点，不表示全 Facebook 用户齐全。
- `absolute-cap`、`partial-open-tail`、无 source proof 或 UI total 差异都不得标记为全量完成。

### 2.5 结果 Schema 必须表达边界

禁止使用会暗示“全站完整”的字段名：

```text
all_users
all_reactors
all_sharers
```

结果至少应保留：

```text
target_validation
source_post_id
terminal
limited
ui_total
visible_count
rate_limit_http_seen
auth_http_error_seen
```

UI total 与 `visible_count` 不同是平台可见性边界，不应强行校正。历史样本曾出现 UI 显示 8 次分享、响应仅返回 3 个 actor 且 `has_next_page=false`。

### 2.6 代码、运行数据和秘密必须分离

产品仓库只应包含：

- 源代码和测试；
- 安装/启动脚本；
- 无秘密的配置样例；
- 迁移与运维文档。

以下内容只属于受控运行数据目录，不得进入 Git：

- Facebook 密码、Cookie、token、authorization header；
- 登录后的 AVD 用户数据；
- 真实抓包 JSONL/MITM flow；
- 真实 Reaction/Share 用户 CSV；
- `runs/`、`batches/`、个人截图；
- mitm 私钥、真实 `.env` secrets。

### 2.7 跨平台迁移要求

- 当前 Windows 是 x86_64 host + x86_64 AVD；Apple Silicon 必须新建 arm64-v8a AVD，不能复制当前 x86_64 AVD 直接运行。
- Windows 路径、PowerShell 和 `adb reverse` 逻辑要与平台无关的业务解析层分离。
- Mac 目标建议支持：

```text
NETWORK_MODE=home_direct
Android -> mitmdump -> 家庭宽带 -> Facebook
```

以及可选：

```text
NETWORK_MODE=upstream_proxy
```

- Mac 代码目录与运行数据目录分离，例如：

```text
~/facebook-capture-service/
~/Library/Application Support/FacebookCaptureService/
```

- 24 小时运行必须包含自动启动、Emulator/ADB/mitmdump watchdog、登录失效检测、job timeout/cancel、stale job 恢复、日志轮转、数据保留期、磁盘告警、`/health`、优雅关闭。
- 远程访问使用 Tailscale、WireGuard、SSH Tunnel 或企业私网；不要裸露家庭公网 API 端口。

## 3. 个人代码风格与协作偏好

### 3.1 通用原则

- 执行导向：先检查真实状态，再实现、验证并报告证据；能安全完成时不要停在计划。
- 改动最小化：遵循现有架构和命名，不做无关重构，不扩大文件删除或配置修改范围。
- 事实导向：区分历史结果、当前实测、局部样本和全量结论；不夸大通过状态或数据覆盖。
- 失败关闭：目标、source、身份、代理或运行状态不明确时，中止并保留诊断，不猜测成功。
- 可恢复：代理、watchdog、子进程、job manifest 和输出目录都要有明确恢复/清理路径。
- 默认用简体中文沟通；代码标识符和技术注释沿用项目现有英文风格。

### 3.2 Python 风格

- 模块、函数、局部变量：`snake_case`。
- 类：`PascalCase`。
- 常量：`UPPER_SNAKE_CASE`。
- 测试类以 `Tests` 结尾；测试方法使用描述性 `test_<行为>_<条件>_<结果>`。
- 新代码优先使用 `pathlib.Path`，避免手工拼接 Windows 路径。
- 较新模块使用 `from __future__ import annotations` 和具体类型提示，例如 `dict[str, Any]`、`Path | None`。
- 标准库优先；目前正式第三方运行依赖主要是 `mitmproxy`。不要仅为简单工作增加框架。
- 文件写入涉及状态/manifest 时使用原子写入；并发共享状态用显式锁。
- 时间戳使用 UTC、可序列化格式；输出 JSON 时保持字段语义稳定。
- 测试使用标准库 `unittest`，不是 pytest；当前环境未安装 pytest。

### 3.3 PowerShell 风格

- 函数使用 PowerShell `Verb-Noun`，例如 `Get-FacebookAdbState`、`Invoke-FacebookAdbCommand`。
- 参数和局部变量使用 `PascalCase`；参数显式标注 `[string]`、`[int]`、`[switch]` 等类型。
- 主脚本使用 `$ErrorActionPreference = "Stop"`，关键错误不得静默吞掉。
- 路径使用 `Join-Path`、`[IO.Path]::GetFullPath()` 和 `$PSScriptRoot`/脚本基路径，避免依赖调用者当前目录。
- 共享 ADB、UI、Python 解析逻辑放入 `facebook_transport.ps1` 或 `facebook_runtime.ps1`，不要复制到多个入口。
- 外部进程必须有超时、退出码检查和有界重试；避免超长的 ADB/uiautomator 组合轮询。
- 兼容 Windows PowerShell 5.1 是当前脚本基线；即使机器装有 PowerShell 7，也不能擅自改用只在 pwsh 7 可用的语法。

### 3.4 命名与语义偏好

- 名称必须表达“当前可见/可枚举”，不使用暗示全量的平台级词汇。
- 状态名应直接反映原因，例如 `source-proof-not-matched`、`page2-not-triggered`、`blocked-session-restriction`，不要用模糊的 `failed` 覆盖关键分类。
- 输出目录使用可排序时间戳或稳定 job ID；API job 使用 `fb_<id>` 形式。
- 文件名体现职责：`probe_` 表示低压探测，`verify_` 表示验证，`test_` 表示单元/静态测试，`*_report` 表示汇总而不是事实源。

### 3.5 注释与文档习惯

- 注释重点解释“为什么存在此保护”“失败时为什么不能继续”“平台行为有什么陷阱”。
- 不写逐行复述代码的注释；复杂 source guard、capture boundary、代理恢复、ANR 或 fallback 前应有短注释说明约束。
- 文档必须可执行：给出入口、路径、前置条件、验证命令、成功标准和限制，不只写状态回顾。
- 报告数量时写清日期与范围，例如“本次 198 tests，4 个 release-gate 失败”，不能把历史通过数字当成当前状态。
- 归属只按已确认 Git identity 或文件证据；不能把他人的提交、模型辅助审查或平台可见子集写成个人全量成果。
- Markdown 保持 UTF-8、代码围栏配对；Windows PowerShell 中检查 Markdown 时优先使用 `rg -F`，避免反引号和嵌套正则的脆弱转义。

## 4. 工具链与版本基线

### 4.1 2026-09-02 当前实测

| 工具/组件 | 当前版本或状态 | 迁移要求 |
|---|---:|---|
| Windows | Windows 11，内核 `10.0.26200.0` | 当前研发主机 |
| Windows PowerShell | `5.1.26100.9168` | 核心 `.ps1` 兼容基线 |
| PowerShell (pwsh) | `7.6.4` | 可用于辅助操作，不得破坏 5.1 兼容性 |
| Python | `3.13.12` | 当前主运行时 |
| pytest | 未安装 | 项目当前使用 `unittest` |
| mitmproxy/mitmdump | `12.2.3` | 正式抓包器 |
| OpenSSL（mitmproxy 环境） | `4.0.0` | 随当前 Python/mitmproxy 环境 |
| project-local ADB | `1.0.41` / `37.0.1-15733141` | 优先使用工作区 `platform-tools\adb.exe` |
| PATH 中 ADB | `1.0.41` / `37.0.0-14910828` | 与项目本地版不同，迁移后需避免路径漂移 |
| Android Emulator | `37.1.11.0`，build `15917651` | 当前 AVD 运行时 |
| Android AVD | Android `15` / API `35`，x86_64 | AVD `fb-x86_64-api35`，serial `emulator-5554` |
| Facebook App | `555.0.0.49.59`，versionCode `470015241` | UI/GraphQL 行为与版本相关，升级后必须回归 |
| OpenJDK | `21.0.12` LTS | Android UI helper/Java 工具链参考 |
| Git | `2.54.0.windows.1` | 根工作区当前非 Git 仓库 |
| RTK | `0.42.0` | 当前 Codex shell 命令要求使用 `rtk` 前缀 |
| psutil | `7.2.2` | 当前 Python 环境已安装 |
| requests | `2.33.1` | 当前 Python 环境已安装；核心模块多使用标准库 urllib |
| Graph API 常量 | `v26.0` | 代码中的 avatar URL 刷新版本，升级需单独验证 |

### 4.2 当前关键路径

```text
工作区              C:\Users\lenovo\Desktop\facebook
AVD home            D:\Android\AVDManual
Android emulator    D:\Android\SdkManual\emulator\emulator.exe
project-local ADB   C:\Users\lenovo\Desktop\facebook\platform-tools\adb.exe
系统 Python         C:\Users\lenovo\AppData\Local\Programs\Python\Python313\python.exe
本地 API            http://127.0.0.1:8765
```

### 4.3 尚未锁定的依赖

- 工作区根目录未发现 `requirements.txt`、`pyproject.toml`、lockfile 或标准安装清单。
- Clash/mihomo 的精确版本本次未确认。
- Android SDK/Build Tools 的完整版本集合本次未确认。
- 迁移时应先根据上表复现已确认版本，再为抽取后的产品仓库补充最小依赖清单和版本锁；不得在本文中猜版本。

## 5. 测试、验证与完成标准

### 5.1 当前实测基线

2026-09-02 本次实际执行：

```text
python -m unittest discover -p 'test_*.py'
Ran 198 tests in 0.739s
FAILED (failures=4)
```

4 个失败均位于 `test_facebook_release_gate.py`，表现为当前 release-gate 原因/历史 fixture 预期不一致：

- `blocked-session-restriction` 实际成为 `source-proof-not-matched`；
- 历史 3000+ terminal fixture 当前未通过 full gate；
- 历史 Share visible-subset terminal fixture 当前未通过 regression gate；
- `blocked-rate-limit-or-restriction` 实际成为 `page2-not-triggered`。

因此当前状态不能写成“全测通过”。这 4 个失败是迁移前后对比基线，不应在没有诊断的情况下改测试预期来消除红色。

PowerShell 本次实测：

```text
verify_transport_runtime.ps1
facebook_transport.ps1 OK
start_facebook_stable.ps1 OK
run_facebook_capture.ps1 OK
open_facebook_raw_capture.ps1 OK
adb_state=device
framework_ready=True

根目录 28 个 .ps1：parser 全部通过
```

### 5.2 每类修改的最低验证

- Python 解析/API 修改：运行对应 `unittest` 文件，再运行完整 discover。
- PowerShell 修改：至少做 AST parser 校验；涉及设备时再跑 `verify_transport_runtime.ps1`。
- source guard/目标打开修改：必须包含 mismatch、无 source proof、Search 假阳性和 fresh boundary 回归。
- 抓包插件修改：必须验证 safe 输出不含 cookie/token/auth/request body；raw 输出必须单独隔离。
- API/job 修改：覆盖 queued、running、cancel、repeat cancel、restart restore、partial evidence。
- 网络修改：验证 ADB=`device`、reverse 映射、默认网络、Facebook UI、目标 GraphQL 和异常后的代理恢复。
- 发布声称“可用”前，必须用一个严格确认的 canonical post 做 fresh、低压、post-specific 验证；监听端口或 HTTP 200 不算端到端通过。

## 6. 开发中的绝对禁忌

### 6.1 项目正确性禁忌

- **不得删除、绕过或弱化 `source_post_id` guard、exact target-open、fresh capture boundary 或 analyzer 二次过滤。**
- **不得在 source mismatch 后继续翻页或把错误用户写入目标结果。**
- **不得用 `/photo/?fbid=`、generic `story.php`、Search 结果、手拼 URL 或 UI 数字冒充目标验证。**
- **不得把 UI total 当成必然可枚举人数。**
- **不得把 `absolute-cap`、`partial-open-tail`、无 page2 或无 source proof 写成 terminal/full。**
- **不得隐藏 401/403/429、限流、ANR、登录失效或账号限制证据。**
- **不得为了速度取消限流退避、source guard 或安全恢复。**
- **不得并发运行多个 API capture 抢同一个模拟器。**
- **不得把 watchdog 当作 AVD 启动器，也不得让 watchdog 与捕获同时竞争 ADB。**
- **不得把 Burp 或旧 `10.0.2.2` 拓扑恢复为当前正式基线。**

### 6.2 数据、安全与账号禁忌

- **不得把密码、Cookie、token、authorization header、mitm 私钥或真实 `.env` 写入代码、文档、日志、Git 或 AI 回复。**
- **不得提交真实抓包、用户 CSV、登录 AVD 数据、`runs/`、`batches/` 或个人截图。**
- **不得擅自重置/清空 AVD、登出 Facebook、删除账号状态或替换用户控制的代理/认证路径。**
- **不得尝试 CAPTCHA 绕过、登录绕过、token 窃取/伪造/重放、账号轮换、IP 轮换、指纹伪装或限流规避。**
- **不得把 non-login Web 的公开元数据能力夸大成 reaction-user 枚举能力；服务端未返回的数据不能靠推断补齐。**
- **不得盲删旧 run、备份或历史证据；删除前必须枚举精确目标并确认用户指定的范围。**

### 6.3 工作区与 Git 禁忌

- **不得在 `C:\Users\lenovo\Desktop\facebook` 直接执行 `git init` 后 `add all`。**
- **不得把工作区根目录误判为 Git 仓库；Git 操作前先定位真实 `.git` 和 checkout。**
- **不得覆盖、回滚或清理与当前任务无关的用户修改、未跟踪文件、归档、备份和证据。**
- **不得用 `git reset --hard`、`git checkout --` 等破坏性命令，除非用户明确要求精确范围。**
- **不得把“Git 状态一致”“端口监听”“格式检查通过”写成运行时端到端验证通过。**

### 6.4 Codex/AI 全局禁忌

- **保持 `sub2api` 为配置 provider，除非用户明确要求更改。**
- **不得伪造模型上下文窗口或强制自定义 compaction threshold。**
- **修改现有 `.codex` 配置/运行时文件前，必须创建带时间戳的回滚副本，并记录到 `CODEX-CHANGELOG.md`。**
- **不得删除 Codex sessions、memories、backups 或 project records，除非用户明确要求该精确删除范围。**
- **shell 命令按当前全局要求加 `rtk` 前缀；输出必须有界，避免扫描或输出完整 `runs/`、JSONL、CSV、依赖目录和大日志。**
- **不要自动加载与任务无关的历史记忆；当前用户指令和当前项目证据优先。**
- **不要增加自定义的二次审批层；用户已明确授权的范围内直接执行并验证。**

## 7. AI 接手任务时的固定检查清单

### 开始前

- [ ] 确认当前路径是否真的是 Git checkout；不要根据 cwd 猜测。
- [ ] 阅读本文件、`PROJECT_MEMORY.md` 和 `2026-08-15_handoff-facebook-report.md` 的最新相关章节。
- [ ] 区分当前实测、历史 run 和用户希望验证的新状态。
- [ ] 检查 ADB serial、AVD、代理、reverse 映射和已有运行进程。
- [ ] 明确任务是否只读、是否允许触发登录账号、是否涉及真实抓包或删除。

### 修改时

- [ ] 保持单 worker 和设备独占模型。
- [ ] 保持 strict target、fresh boundary、source guard 和 analyzer 二次过滤。
- [ ] 保持敏感字段脱敏和运行数据隔离。
- [ ] 只修改请求范围内的文件，不整理无关目录。
- [ ] 注释说明约束和原因，不复述代码。

### 完成前

- [ ] 运行与风险相称的 Python tests 和 PowerShell parser/runtime 验证。
- [ ] 核对代理、watchdog、子进程和模拟器状态是否恢复。
- [ ] 报告实际命令结果，包括失败项；不把部分通过写成全部通过。
- [ ] 对 live Facebook 结论标明日期、目标 post 和证据来源。
- [ ] 确认输出中没有秘密、个人账号数据或不该提交的 runtime artifact。

## 8. 权威入口

迁移和接手优先阅读：

1. `AI编码规范与上下文说明.md`：AI/工程规范与迁移总览；
2. `2026-08-15_handoff-facebook-report.md`：最完整的运行、架构、历史验证和交接文档；文件名日期较旧，但内容持续更新到后续阶段；
3. `PROJECT_MEMORY.md`：启动、代理和已验证 UI 入口的短索引；
4. `FACEBOOK_RESEARCH_GUIDE.md`：日常研究与运行手册；
5. `facebook_capture_api.py`、`run_facebook_capture.ps1`、`facebook_transport.ps1`：当前实现事实源；
6. `test_*.py`、`verify_*.ps1`：当前行为契约和迁移回归入口。

本文不替代实时检查。Facebook App、GraphQL、账号状态、AVD、Clash 和网络环境都可能变化；涉及“现在是否可用/是否受限”的结论必须重新做一次有界、低压、严格目标验证。
