# Changes

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
