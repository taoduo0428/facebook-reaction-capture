$ErrorActionPreference = 'Stop'

$env:ANDROID_HOME = 'D:\Android\SdkManual'
$env:ANDROID_SDK_ROOT = 'D:\Android\SdkManual'
$env:ANDROID_USER_HOME = 'D:\Android\.android-manual'
$env:ANDROID_AVD_HOME = 'D:\Android\AVDManual'

$emulator = 'D:\Android\SdkManual\emulator\emulator.exe'
$adb = 'D:\Android\SdkManual\platform-tools\adb.exe'
& $adb kill-server | Out-Null
& $adb start-server | Out-Null
& $emulator `
    -avd fb-x86_64-api35 `
    -no-snapshot `
    -no-boot-anim `
    -shell `
    -feature -WiFiPacketStream `
    -dns-server 8.8.8.8 `
    -cores 4 `
    -memory 3072

exit $LASTEXITCODE
