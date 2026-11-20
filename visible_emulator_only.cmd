@echo off
set ANDROID_HOME=D:\Android\SdkManual
set ANDROID_SDK_ROOT=D:\Android\SdkManual
set ANDROID_USER_HOME=D:\Android\.android-manual
set ANDROID_AVD_HOME=D:\Android\AVDManual
"D:\Android\SdkManual\emulator\emulator.exe" -avd fb-x86_64-api35 -port 5554 -no-snapshot -no-boot-anim -feature -WiFiPacketStream -dns-server 8.8.8.8 -cores 4 -memory 3072 -writable-system 1>"C:\Users\lenovo\Desktop\facebook\visible-emulator.stdout.log" 2>"C:\Users\lenovo\Desktop\facebook\visible-emulator.stderr.log"
