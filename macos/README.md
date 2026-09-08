# Facebook 双模拟器 macOS 适配层

新 M4 部署请先看 [QUICKSTART.md](QUICKSTART.md)，使用根目录 START.command 和统一页面。以下描述的是旧的独立启动适配器，仅作为兼容性说明，不是新执行器的入口。

此目录是独立的 macOS 启动与 ADB transport 层。它不读取或覆盖根目录的
`facebook_workers.json`，不调用现有 Windows PowerShell，也不复制任何模拟器、
Facebook 账号、Cookie 或登录状态。

## 当前能力边界

已实现：

- 校验两个 Mac AVD 的 worker id、serial、AVD 名和端口互不冲突；
- 为 Apple Silicon (`arm64-v8a`) 和 Intel (`x86_64`) 提供环境诊断；
- 启动指定 AVD，等待 Android boot 完成；
- 恢复每台设备各自配置的 capture port 的 `adb reverse`；
- 兼容旧配置时才设置 Android 全局 HTTP 上游代理；代理地址由配置提供，不默认要求 `7897`；
- 检查 `com.facebook.katana` 是否安装；
- 在 Windows 上只生成结构化命令，不执行 Mac 或 Android 命令。

未实现：现有 `run_facebook_capture.ps1` 中的完整 Facebook UI 自动化、Frida、
mitmproxy 生命周期与 Reaction 深抓尚未重写为 macOS runner。CLI 会明确输出
`deep_capture_runner: not-implemented-for-macos`，不会假报抓取完成。

## 1. Windows 上模拟检查

在项目根目录执行：

```powershell
python macos/facebook_macos.py doctor --simulate
python macos/facebook_macos.py simulate --worker all
python -m unittest test_facebook_macos.py
```

`executed` 必须为 `false`。这只验证配置、架构识别和命令/端口隔离，不会操作
当前 Windows 模拟器。

## 2. Mac 环境准备

安装 Python 3、Android command-line tools，并让以下两个文件可执行：

```sh
export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
export PATH="$ANDROID_SDK_ROOT/emulator:$ANDROID_SDK_ROOT/platform-tools:$PATH"
chmod +x macos/start_facebook_workers.sh
python3 macos/facebook_macos.py doctor
```

Apple Silicon 安装 ARM 镜像：

```sh
sdkmanager "platform-tools" "emulator" \
  "platforms;android-35" "system-images;android-35;google_apis;arm64-v8a"
```

Intel Mac 安装 x86_64 镜像：

```sh
sdkmanager "platform-tools" "emulator" \
  "platforms;android-35" "system-images;android-35;google_apis;x86_64"
```

不要在 Apple Silicon 上创建 x86_64 AVD 作为正式运行方案。Rosetta 不能代替
Android Emulator 的原生虚拟化兼容性验证。

## 3. 创建两个互相隔离的 AVD

先按主机架构设置镜像标识。以下以 Apple Silicon 为例：

```sh
IMAGE="system-images;android-35;google_apis;arm64-v8a"
printf 'no\n' | avdmanager create avd -n fb-macos-api35-worker-01 -k "$IMAGE"
printf 'no\n' | avdmanager create avd -n fb-macos-api35-worker-02 -k "$IMAGE"
cp macos/facebook_workers.macos.example.json macos/facebook_workers.macos.json
```

两台 AVD 分别固定使用 `emulator-5554`/`emulator-5556` 和
`18081`/`18082` capture port。`worker-03` 默认禁用。

配置只描述运行槽位，不保存账号信息。首次启动后应分别在两台模拟器中手工安装
Facebook 并手工登录；不要复制 Windows AVD 或另一台 AVD 的数据目录。

## 4. 启动与 transport

```sh
./macos/start_facebook_workers.sh start --worker worker-01
./macos/start_facebook_workers.sh start --worker worker-02
./macos/start_facebook_workers.sh transport --worker all
```

这一旧 transport 适配层只有在你明确选择上游代理时才需要本地代理服务；`7897` 只是旧配置的示例，不是固定要求。新 M4 执行器默认使用直连，请按 [QUICKSTART.md](QUICKSTART.md) 配置 `network.mode`。capture port 是每个 worker
独占的预留端口，供后续 Mac 深抓 runner 使用。`native_port` 当前只做全局隔离校验，
本适配层不会监听该端口。

## 5. 真实 Mac 验收

以下结果都成功后，才能称为 Mac 启动/transport 已实机通过：

```sh
python3 macos/facebook_macos.py doctor
adb devices
adb -s emulator-5554 shell getprop sys.boot_completed
adb -s emulator-5556 shell getprop sys.boot_completed
adb -s emulator-5554 reverse --list
adb -s emulator-5556 reverse --list
adb -s emulator-5554 shell settings get global http_proxy
adb -s emulator-5556 shell settings get global http_proxy
adb -s emulator-5554 shell pm path com.facebook.katana
adb -s emulator-5556 shell pm path com.facebook.katana
```

还必须另行实测 macOS Hypervisor、Keychain/CA 权限、Frida server 架构、mitmproxy
证书信任、Facebook 登录以及一次真实的严格目标抓取。Windows 模拟无法证明这些
项目；当前也不能据此声称完整抓取链路已经支持 macOS。
