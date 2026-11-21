# 一键配置与连续任务实施计划

## 2026-09-09 当前核对

一键配置、热启动/冷启动分支、模拟测试、README、Mac 指南、发布清单和 CI 已落地；Windows 与 CI 静态验证已完成。真实 M4 的 Android 虚拟化、人工登录、证书、Frida 和 Facebook 深度抓取仍待接收者实机验收，因此不勾选“全部跨平台完成”。详见 `CURRENT_STATUS_2026-09-09.md`。

目标：Mac 下载者只编辑可选输入文件并运行 CONFIGURE.command；连续帖子复用 Facebook，避免应用选择器。

架构：复用 facebook_local 的配置初始化和 macos.prepare_device 的证书/架构校验；新增独立部署入口，不覆盖现有 Worker。采集仍为每帖独立产物和传输生命周期，保持来源隔离。

- [ ] 新增 facebook_setup.py、facebook.setup.example.json、CONFIGURE.command：SDK 工具检查、镜像安装、专用 AVD、公开 Frida 下载校验、APK 输入、CA、默认链接和就绪报告。
- [ ] Windows 优先复用已有 PID；保留显式冷启动选项。检查现有导航包名，避免选择器。
- [ ] 新增模拟测试：首次/再次配置、已有设备身份错误、下载失败、配置保护、热启动/冷启动分支。
- [ ] 更新 README、Mac 快速指南、发布清单和 CI；运行定向测试及发布门禁后提交并同步远端。

失败处理：下载和子进程有上限；无账号登录只报告待登录；端口占用不杀进程；已有 AVD 不覆盖；每帖重新建立采集边界，不放宽分页终止/限流要求。真实 M4 性能与完整抓取留待实机验收。
