@echo off
set ANDROID_HOME=D:\Android\SdkManual
set ANDROID_SDK_ROOT=D:\Android\SdkManual
set ANDROID_USER_HOME=D:\Android\.android-manual
set ANDROID_AVD_HOME=D:\Android\AVDManual
"D:\Android\SdkManual\emulator\emulator.exe" -avd fb-x86_64-api35-worker-02 -port 5556 -no-snapshot -no-boot-anim -feature -WiFiPacketStream -dns-server 8.8.8.8 -cores 4 -memory 3072 1>"C:\Users\lenovo\Desktop\facebook\visible-emulator-worker02.stdout.log" 2>"C:\Users\lenovo\Desktop\facebook\visible-emulator-worker02.stderr.log"
