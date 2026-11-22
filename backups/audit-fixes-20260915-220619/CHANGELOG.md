# Changes

## 2026-09-15 — 连续任务复用与导航等待优化

- 成功任务收尾先原地核对 Facebook 页面健康，健康时保持进程与页面；异常情况保留原有恢复路径。
- 已确认安装成功的常驻注入组件按设备启动标识、PID、进程启动时间、脚本 SHA-256 复用；UI 检查前后身份需一致，证据缺失或身份变化仍完整初始化。注入失败不会留下旧的可复用状态。
- 点开 Reaction 和展开严格帖子详情后先做短延迟新快照，已就绪就继续，未就绪按原预算补足等待；评论排除、来源校验、初始分页冷却和限流退避均保留。
- 真实连续两帖保持同一 PID，第二帖跳过重复注入；两帖均到达终页，未观测到限流。第二帖开始到目标确认约 39.2→25.1 秒，目标确认到列表约 21.2→15.1 秒。页面数据和网络状态会变化，不作为统一耗时承诺。
- 保留原有 MITM 启动 3 秒等待及打开帖子后 8 秒等待。详细本地证据见 `evidence/latency-20260915/README.md`，回滚源文件保存在 `backups/latency-optimization-20260915/`。

## 2026-09-15 — 帖子 Reaction 导航修复与 API 调用交付

- Reaction 汇总图标识别改用 Facebook 滚动容器可见区域与同排赞/评论/分享结构，修复按钮上移到 `y=752` 后被固定 `y>=900` 过滤的问题；保留评论排除与来源校验。真实目标 `share/p/1Jetj5opcE/` 已经由 API 完整取得 167 个唯一用户，终点和来源一致性通过。
- 框架就绪已有条件轮询，移除其前方重复的固定 8 秒等待；保留超时失败机制。
- 进度说明按任务状态与已选产物的实际存在情况显示，不再对失败任务或默认轻量证据声称 PCAP/MITM 均已保存。
- 新增 `facebook_api_client.py` 与 `API_USAGE.md`，复用 `/v1/jobs` 完成提交、轮询、结果和原子下载，失败/取消/超时明确退出，任务 ID 可用于恢复查询；新增 requests 依赖及 HTTP 测试。
- 将已有 STOP/可见会话启动脚本纳入明确发布清单；可见模拟器适配从本机配置解析 SDK、AVD 与 Worker，消除旧命令文件中的开发机绝对路径依赖。
- 保留进入本轮前的未提交恢复/启动/轻量证据改动。详细基线、回滚路径与本轮实测见 `STABILIZATION_2026-09-15.md`；模拟证据不代表 macOS 或全新机器实测。

## 2026-09-13 — Codex 可执行的 macOS 部署手册

- 新增 `macos/CODEX_DEPLOYMENT_RUNBOOK.md`，逐步覆盖 Apple Silicon 主机、Android 35 ARM64、APK、Frida、CA、单/主备 Worker、API、分页、产物、重启与升级。
- 为每个关键步骤写明预期结果、停止条件和故障处理，并明确人工登录/验证码边界以及敏感状态不得提交。
- 将 Windows 本地测试、GitHub `macos-latest` 契约检查和真实 M1-M4 实机验收分开记录，避免用 CI 代替真实 Facebook 深分页证据。
- Windows `START.cmd` 现在委托 `START_API_AND_EMULATOR.cmd`，统一启动 API、V2 UI 与主模拟器，并保留入口退出码。

## 2026-09-09 — 文档与运行证据校准

- 新增 `CURRENT_STATUS_2026-09-09.md`，统一解释 `initial-ready`、`partial/open-tail` 和 `terminal-complete`。
- README 补入群组 permalink、Reel、Watch 和第二个 `pfbid` 的真实移动端首屏证据，删除“群组 permalink 尚缺实机证据”的过时表述。
- 明确 9 月 8 日至 9 日的新 Probe 均未分页；完整分页证据仍为 `reaction-full-smoke-20260902-120331`。
- 对齐历史记录、计划文档和 macOS M4 未实机验收边界。

## 2026-09-09 — pfbid 规范地址变化兼容

- `pfbid` 目标验证现在允许 Facebook 将 `www.facebook.com` 规范化为根域名，并在返回新 `pfbid` token 时使用同一导航请求中的唯一稳定 post/author/legacy story ID 绑定目标。
- 错误或缺失导航哈希、多帖子响应、普通 Feed 查询和目标 post ID 不匹配仍按 `not-matched` 处理。
- 原始 IsmailiGoldenLight `pfbid` URL 已重新通过 Android ProbeOnly：严格匹配 post `122106880826437311`，Reaction 总数 `3537`，首屏 31 个用户、2 条查询、零限流且无分页。

## 2026-09-08 — Mac 一键配置与连续帖子启动优化

- 新增 CONFIGURE.command 和 facebook.setup.example.json，将 Android ARM64 镜像、AVD、Frida、证书、APK 和链接默认打开方式纳入可重复运行的配置流程；账号仍由用户登录。
- 新 Worker 初始化时自动避让模拟器控制/ADB 端口对，既有配置保持不变。
- Windows 采集优先使用现有 Facebook 主进程；每帖保持独立采集边界和完整性校验。
- Mac shell 入口固定 LF；源码导出 ZIP 保留可执行权限和对应 SHA-256。
- 新增部署失败路径与热启动模拟测试。实际 M4 抓取和速度收益仍需接收者实机验证。

## 2026-09-08 — 本地部署与源码交付

- 新增 START.cmd / CHECK.cmd、隔离依赖安装、配置模板与本地启动器。
- SDK/AVD/ADB 路径可由本机配置传递；保留历史默认及已存在的 Worker 配置。
- 修复虚拟环境内 mitmdump 发现路径与显式 Python 优先级。
- 修复 v2 HTML 的 UTF-8 字节损坏、标签和属性闭合。
- v2 静态资源使用白名单，禁止读取页面目录外的文件；主页面增加首次配置指南入口。
- 新增源码发布清单、导出包、默认拒绝跟踪的 Git 规则与首次安装说明。
- 保留历史采集、模拟器数据与账号。真实 Facebook / 新 Windows / Mac 验收另行记录，不以本地单测代替。
