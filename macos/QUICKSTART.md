# M4 Mac 首次安装与验收

项目截至 2026-09-09 的统一状态和真实运行边界见 [../CURRENT_STATUS_2026-09-09.md](../CURRENT_STATUS_2026-09-09.md)。本指南描述可执行安装流程，不代表已经在接收者 M4 上完成真实 Facebook 深度抓取验收。

本指南面向 Apple Silicon（M1–M4），在自己的账号下运行。当前 Python 采集执行器正在跨平台验收；Windows 模拟与 GitHub macOS 主机测试不能替代 M4 上的 Android、Facebook 与证书验收。不要把 Windows AVD 复制到 Mac。

## 1. 推荐：一键配置

第一次先安装 [Python 3.12 或 3.13](https://www.python.org/downloads/macos/)（官方 universal2 安装包）与 [Android Studio](https://developer.android.com/studio)。打开 Android Studio → SDK Manager → SDK Tools，勾选 **Android SDK Command-line Tools (latest)**；JDK 可使用 Android Studio 自带的运行时。随后：

1. 下载仓库 ZIP，解压到自己的可写目录。
2. 把合法取得的 Facebook **Android ARM64 APK** 放入仓库的 `setup-input` 文件夹（没有就创建；拆分包的全部 APK 一起放入）。已有 Facebook 安装会保留。
3. 双击 **CONFIGURE.command**。第一次按官方提示阅读并接受 Android SDK 许可；等待镜像下载与模拟器启动。
4. 在模拟器中手动登录 Facebook、处理验证码。配置入口完成后自动打开本地页面，在页面中每行粘贴一个帖子 URL。

默认配置适合新加坡直连，无需填写代理、端口、Frida 版本或模拟器名称。工具会初始化 `facebook.local.json`、`facebook_workers.json` 和 `facebook.setup.local.json`；已有文件不覆盖。

下载的 `.command` 如果无法双击，在终端进入仓库目录执行一次：

```sh
bash CONFIGURE.command
```

CONFIGURE 会校验/安装缺失的 SDK 组件、只创建缺失的 ARM64 AVD、校验官方 Frida 下载、安装公开采集证书、启动 Frida 服务，并设置“Facebook 链接用 Facebook 打开”。失败时按提示修复后再次运行，已有设备和账号不重建。它不自动登录，不保证当前账号能看到每个帖子的用户名单。

同一次模拟器运行期间，后续双击 **START.command** 即可。**Mac 或模拟器重启后重新双击 CONFIGURE.command**，恢复运行时证书挂载与 Frida 服务。终端需保持打开。下面第 3、4 节是高级手动配置参考；一键配置完成后无需重复执行。

特殊情况才需编辑 `facebook.setup.local.json`：`facebook_apks` 可填写 APK 的完整路径数组；无法访问 GitHub 时，将自己校验过的 Android ARM64 Frida server 完整路径填入 `frida_server`，并设置 `download_frida: false`。不要把本机配置、APK、下载缓存或证书私钥交给其他人；分享 GitHub 仓库即可。

## 2. 网络和端口

新加坡直连 Facebook 的电脑使用 `facebook.local.json` 中的默认配置：

```json
"network": {"mode": "direct", "upstream_url": ""}
```

无需 Clash、小火箭或 7897 端口。mitmproxy 是本机采集代理，直接连互联网，不是翻墙代理。如果确需上游 HTTP 代理，改为 `{"mode":"upstream","upstream_url":"http://127.0.0.1:你的端口"}`；暂不支持嵌入用户名密码。

`port` 是网页/API 的本机监听端口；每个 Worker 的 `capture_port` 是流量采集端口，`native_port` 为独占预留端口，`serial` 中的偶数是模拟器控制端口。首次生成配置会避开已占用的 API/capture/native 端口；这不是永久端口预约，之后仍需检查冲突。已有配置不自动更改；冲突时修改对应 JSON。均不需要公网端口或路由器转发。

## 3. ARM64 Android

Android Studio SDK Manager 安装 platform-tools、emulator、command-line tools、platforms android-35、build-tools 35.0.0，以及 **Android 35 Google APIs ARM64-v8a** 镜像（不要用禁止 root 的 Google Play 镜像）。M4 不使用 x86_64 镜像。

Device Manager 创建 AVD，名称与根目录 `facebook_workers.json` 中的 `avd_name` 完全一致，默认 `fb-macos-api35-worker-01`。建议 1080×1920、4 核、3GB RAM。配置 `sdk_root` 为自己的 SDK 目录（通常 `~/Library/Android/sdk`，JSON 中可以使用完整路径）。

在项目目录运行 `.venv/bin/python facebook_local.py shell`，让终端加载本机 SDK/AVD 路径。启动模拟器：

```sh
emulator -avd fb-macos-api35-worker-01 -port 5554 -writable-system -no-snapshot
```

安装合法取得的 Android ARM64 Facebook APK，并在模拟器内人工登录。多 APK 包使用 `adb install-multiple`。不要把桌面浏览器登录直接当作模拟器登录。

## 4. 采集证书与 Frida

从 https://github.com/frida/frida/releases 取得与 `.venv/bin/frida --version` 一致的 **Android arm64** frida-server，解压为 ELF 文件。项目当前固定 Frida 17.17.0。不要下载 darwin server 或 x86_64 server。

```sh
adb -s emulator-5554 root
.venv/bin/python macos/prepare_device.py --serial emulator-5554 \
  --frida-server /完整路径/frida-server-17.17.0-android-arm64 \
  --install-ca
```

安装器只把公开 CA 证书推到模拟器，不推私钥；会校验 Android 15 Conscrypt 和应用 mount namespace。初次启动 mitmproxy CA 会留在本机 `~/.mitmproxy`。挂载随模拟器重启失效，需要重新执行证书安装并验证。

Frida 安装与启动是不同步骤。运行下面命令让 server 前台运行，另开终端继续操作：

```sh
adb -s emulator-5554 shell /data/local/tmp/frida-server
```

保持 Facebook 已打开且已登录，然后启动页面。当前 Mac 执行器使用 ADB XML 滚动，不依赖 UI Helper 的 native scroll。`prepare_device.py` 还会检查可选 UI Helper；若仅此项失败，不能称“所有前置检查通过”，但不妨碍不使用 Helper 的执行器测试。

## 5. 实际验收

先从 [浏览器样本](../FACEBOOK_BROWSER_SAMPLES.md) 选择名单可见的帖子，用接收者自己的账号检查 Reaction 列表。网页可见不代表 Android 一定可见。

在本地页面每行粘贴一个 URL，先单条低频验证。Mac 当前导出 CSV、解析报告、MITM 和进度，PCAP 尚不支持。成功必须同时有严格目标匹配、同一来源帖子、ALL 列表分页终止与完整性校验；没有增长或只显示好友姓名会保留为部分/受限结果。不要在限流后重复提交。

通过 CHECK.command、真实小帖任务和模拟器重启后再验收，才可判断 M4 部署成功。请保留退出码和脱敏诊断；不分享 Cookie、CA 私钥或模拟器磁盘。

旧 `facebook_macos.py` 是独立历史启动/模拟工具，使用它的配置不会自动改变新控制台的根目录配置。新部署使用本指南和 START.command。
