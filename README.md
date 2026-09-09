# Facebook Reaction 本地采集工具

当前代码、真实运行和历史记录的统一口径见 [2026-09-09 当前状态](CURRENT_STATUS_2026-09-09.md)。

**Mac M4 / 新加坡直连部署：**见 [M4 安装与验收](macos/QUICKSTART.md)。新版本加入 START.command、独立 Python Android 执行器和可选上游代理；Mac 真实 Facebook 链路仍需实机验收。新配置默认直连，已有 Windows 无 network 字段的配置保留原 7897 上游模式。端口在首次初始化时避让冲突，已有账号配置不覆盖。

可查看用户名单与仅显示数字的区别，见 [真实浏览器样本](FACEBOOK_BROWSER_SAMPLES.md)。下方 Windows 7897 配置仅适用于选择 upstream 模式的用户，直连模式不需要 Clash。

输入 Facebook 帖子 URL，在本机 Android 模拟器中打开帖子，采集当前登录账号可见的 Reaction 列表，导出 CSV，并显示分页进度、限流状态和文件位置。支持多个独立 Worker 分别处理不同帖子。

## 下载后如何开始

Windows 10/11 x64 使用现有 PowerShell 采集链路；Apple Silicon Mac 使用独立 Python Android 执行器，仍待 M4 实机验收。建议 Python 3.12 或 3.13、16 GB 内存（双模拟器建议 24 GB）。**Mac 接收者直接按 [一键配置说明](macos/QUICKSTART.md) 操作，下面的 START.cmd 和 x86_64 步骤仅供 Windows 使用。**

1. 安装 [Python 3.12 或 3.13](https://www.python.org/downloads/)，勾选 Add python.exe to PATH；安装 [Android Studio / SDK](https://developer.android.com/studio)。
2. 解压仓库到自己的可写目录，双击 **START.cmd**。首次自动创建 `.venv`、安装 `requirements.txt`、生成本机配置，随后打开 `http://127.0.0.1:8765/`。安装失败会保留提示，修复网络后重试即可。
3. 按下节准备 Android 环境并手动登录 Facebook。页面能打开不代表模拟器已准备好。双击 **CHECK.cmd** 查看 Python、SDK、AVD、代理和端口检查结果。
4. 在页面中每行输入一个 URL，点击“开始抓取”。多个 Worker 使用各自模拟器处理任务。选择帖子行查看流程、分页、人数和产物；“停止批次”可取消任务。

Mac M1–M4 首次配置可直接双击 **CONFIGURE.command**。它会创建本机配置、检查或安装 Android 35 ARM64 组件、只创建缺失的专用 AVD，并按官方 SHA-256 校验 Frida；合法取得的 Facebook APK 放进 `setup-input/` 后再次运行即可。已有 AVD、账号和登录状态不会覆盖。安装完成后在模拟器内手动登录 Facebook，再双击 START.command。若已有环境只想启动页面，直接双击 START.command；不要把 `facebook.setup.local.json`、APK、Frida 文件或 `.setup-cache/` 上传到 GitHub。

连续提交多个 URL 时，同一 Worker 会复用已经启动的模拟器，Windows 优先附加已有 Facebook 主进程；进程不存在时才启动，故障恢复仍可能重启应用。每条任务独立建立采集代理、输出文件和目标校验，避免上一帖数据混入。项目导航指定 Facebook 包名，直接进入 Facebook；Mac 配置入口还设置 Android 的 Facebook 链接打开偏好。分页节流、限流冷却和完整性检查仍然保留。

Windows 手动排错时可给 `run_facebook_capture.ps1` 加 `-ColdStartFacebook`（别名 `-ColdStart`）恢复原先冷启动行为。日常页面批量抓取使用默认热启动，无需设置此参数。

启动窗口需要保持打开。正常停止请先在页面取消正在运行的批次，等待其收尾，再按 Ctrl+C。下次直接双击 START.cmd，不会重复安装未变更的依赖，也不会覆盖已有配置。仅安装依赖可运行 `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly`。

## 本机配置

首次启动生成 `facebook.local.json` 和 `facebook_workers.json`，这两个文件不进入 Git。

```json
{
  "sdk_root": "C:/Users/你的用户名/AppData/Local/Android/Sdk",
  "avd_home": "C:/Users/你的用户名/.android/avd",
  "port": 8765,
  "open_browser": true
}
```

使用 `/` 或 JSON 转义后的 `\\`。空 SDK 路径会依次尝试 Android 环境变量、既有 `D:/Android/SdkManual` 和 Android Studio 默认位置；既有项目继续使用原 AVD。配置只影响本项目及其子进程，不修改系统 PATH 或代理/TUN 设置。

首次生成的 Worker 使用 capture `18081`，避开常见的 `8081` 服务冲突。已有 Worker 配置完全保留。每台的 `worker_id`、`serial`、`avd_name`、capture/native 端口必须唯一。API 端口不要和 Worker 端口重复。

## 首台模拟器配置（首次需要完成）

仓库不附带 Android SDK、Facebook APK、账号、证书私钥或模拟器磁盘。以下步骤在你自己的 Windows 环境执行；Facebook 版本和页面布局变化可能需要重新验收。

1. 在 SDK Manager 安装 **Android SDK Platform-Tools、Android Emulator、Android SDK Command-line Tools (latest)、Android SDK Platform 35、Build-Tools 35.0.0、Android 35 Google APIs x86_64 系统镜像**。选择可 `adb root` 的 Google APIs 镜像，不能用禁止 root 的 Google Play 镜像。按安装器提示接受 SDK 许可。
2. 在 Device Manager 创建名为 `fb-x86_64-api35` 的 AVD，建议 1080×1920、4 CPU、3072 MB RAM；名称需和 Worker 配置一致。只创建新的 AVD，勿覆盖其他账号的磁盘。
3. 新加坡等可直接访问 Facebook 的环境保留 `network.mode: direct` 即可。需要上游 HTTP 代理时，在 `facebook.local.json` 中设置 `network.mode: upstream` 和自己的 `upstream_url`，例如 `http://127.0.0.1:7897`。工具不会自动启用 TUN。
4. 安装合法取得的 Facebook APK 并人工登录。可使用 SDK 的 `adb -s emulator-5554 install 路径.apk`，拆分 APK 用 `install-multiple`。账号验证码和设备确认在模拟器内手工完成。
5. 本机 `.venv\Scripts\frida.exe --version` 与 Android 的 frida-server 必须版本相同。本依赖清单使用 17.17.0；从 [Frida Releases](https://github.com/frida/frida/releases) 取得对应 Android x86_64 server，解压后用 `adb push` 放到 `/data/local/tmp/fb-fs17` 并 `chmod 755`。既有账号环境应先核对版本，勿覆盖正在运行的工具。
6. 构建项目的 UI Helper：安装 JDK 并设置 JAVA_HOME，使 `javac` 和 `keytool` 可用。执行下面的命令生成本机 debug 签名并构建 APK（已有 keystore 不要重新生成）。构建只编译仓库自带的 Java 源码。

```powershell
# 仅在该文件不存在时执行 keytool；不是 Facebook 的签名密钥。
keytool -genkeypair -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android -keyalg RSA -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
powershell -ExecutionPolicy Bypass -File .\android-ui-dump-helper\build-helper.ps1 -SdkRoot "C:\Users\你的用户名\AppData\Local\Android\Sdk"
```

7. 初始化自己本机的 mitmproxy CA：执行 `.venv\Scripts\mitmdump.exe --listen-host 127.0.0.1 --listen-port 18089`，确认启动后 Ctrl+C，CA 位于 `%USERPROFILE%\.mitmproxy`。安装 OpenSSL（Git for Windows 通常附带），保证 `openssl` 可用。不要复制其他人的 CA 私钥。
8. 从配置启动当前 SDK/AVD 的 PowerShell 环境，安装本机 CA 与 UI Helper。可先用 `.venv\Scripts\python.exe facebook_local.py shell` 进入已加载配置的终端，再执行：

```powershell
. .\facebook_transport.ps1
. .\facebook_certificate.ps1
# AVD 必须使用 writable system；已有 AVD 正常关闭后再执行该启动脚本。
.\start_facebook_stable.ps1 -Serial emulator-5554 -AvdName fb-x86_64-api35 -CapturePort 18081 -NativePort 18766 -WritableSystem -SkipFacebookUiCheck
adb -s emulator-5554 root
Ensure-FacebookMitmCertificate -Serial emulator-5554
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
- API 重启后历史完成任务可加载；运行中/排队任务会标为 interrupted，不会自动恢复深分页。
- 官方默认页面是 `web/`，独立 v2 页面可通过 `start_ui_v2.ps1` 启动在 8766，但需要先配置并启动主 API。
- 当前核心验证命令：`python -m unittest test_facebook_local test_facebook_macos test_facebook_capture_orchestrator test_facebook_capture_api test_facebook_worker_bootstrap`。历史 release-gate 测试依赖本机旧 run，不作为下载后的验收要求。

## GitHub 发布准备

本地工作目录混有个人文档与采集证据。执行 `python tools/export_source.py`，根据 `release-files.txt` **明确文件清单**生成 `dist/facebook-source-时间.zip`，只包含可交付源码、模板和说明。解压到新目录再创建 GitHub 仓库；不要上传整个历史工作目录。导出器拒绝软链接、不存在的文件和不合法路径，同时输出 SHA-256 清单以便核对。

项目仓库：[taoduo0428/facebook-reaction-capture](https://github.com/taoduo0428/facebook-reaction-capture)。当前为私有仓库，受邀协作者可克隆或通过 Code → Download ZIP 下载。公开发布前由仓库所有者选择许可证与可见性；当前未附带开源许可证。
