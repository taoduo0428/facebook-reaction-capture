# AI 编码规范与上下文说明

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

发生冲突时，以当前代码和最新权威文档为准。

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

### 1.3 明确不在目标内的行为
- 不破解 TLS 密钥；不绕过登录、访问控制、CAPTCHA、风控或限流。
- 不偷取、伪造或重放 token/cookie。
- 不承诺获取 Facebook 全站或全部用户，只报告当前账号实际可见和可枚举的结果。

## 2. 针对本项目的具体架构要求

### 2.1 分层与文件职责
- `start_facebook_stable.ps1`：稳定启动 AVD，建立 ADB reverse，验证网络。
- `facebook_runtime.ps1`：统一解析运行时，管理子进程调用。
- `facebook_transport.ps1`：ADB、网络、reverse、UI dump 共享能力。
- `open_facebook_post_readonly.ps1` / `explore_facebook_readonly.ps1`：目标验证和深分页。
- `run_facebook_capture.ps1`：总编排器。
- `facebook_capture_addon_safe.py`：安全脱敏抓包插件。
- `analyze_facebook_capture.py`：解析与导出。
- `facebook_capture_api.py`：异步任务队列 API。

### 2.2 网络拓扑必须保持双模式
- 正式日常网络：App -> guest 127.0.0.1:7897 -> host 127.0.0.1:7897 -> Clash -> Internet
- 临时抓包网络：App -> guest 127.0.0.1:8081 -> host mitmdump :8081 -> upstream host 127.0.0.1:7897 -> Clash -> Internet
- 抓包结束必须恢复原代理。

### 2.3 严格目标验证
- 进入 Reaction/Share 深分页前必须验证 `source_post_id`。一旦 mismatch，立刻安全中止。

### 2.4 结果 Schema 必须表达边界
- 禁止使用暗示“全站完整”的字段名（如 `all_users`）。保留 `ui_total`, `visible_count`, `rate_limit_http_seen` 等边界信息。

### 2.5 代码、运行数据和秘密分离
- 密码、Cookie、Token、真实抓包 JSONL 不能进 Git。

### 2.6 跨平台迁移
- Mac 目标建议支持 `NETWORK_MODE=home_direct` 或 `upstream_proxy`。

## 3. 个人代码风格与协作偏好

### 3.1 通用原则
- 执行导向，改动最小化，失败关闭，可恢复。
- Python: `snake_case` (var/func), `PascalCase` (class), 优先 `pathlib.Path`，原子写入。
- PowerShell: `Verb-Noun` 函数名，`$ErrorActionPreference = "Stop"`。兼容 PowerShell 5.1。

## 4. 工具链与版本基线 (2026-09-02)
- Windows 11, PowerShell 5.1, Python 3.13.12, mitmproxy 12.2.3, AVD (x86_64, API 35), Facebook App (555.0.0.49.59)

## 5. 开发中的绝对禁忌
- **不得删除、绕过或弱化 `source_post_id` guard 和 analyzer 二次过滤。**
- **不得在 source mismatch 后继续翻页。**
- **不得隐藏 401/403/429、限流、ANR 或账号受限证据。**
- **不得把密码/Token/真实抓包提交到代码库。**
- **不得擅自重置 AVD 或登出 Facebook。**

## 6. AI 接手任务清单
- 确认当前路径是否真的是 Git checkout。
- 区分当前实测、历史 run 和验证状态。
- 保持敏感字段脱敏和运行数据隔离。
- 运行与风险相称的 Python tests 和 PowerShell 验证。
- 报告实际命令结果，包括失败项。
