[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$DurationSeconds = 90,
    [int]$ScrollCount = 4,
    [int]$ScrollDelaySeconds = 4,
    [int]$ProxyPort = 8081,
    [int]$MaxFlows = 300,
    [int]$MaxBodyBytes = 6291456,
    [switch]$LaunchFacebook,
    [switch]$OpenShareList,
    [switch]$OpenComments
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path $Base (Join-Path "runs" $Timestamp)
$Addon = Join-Path $Base "facebook_capture_addon_safe.py"
$Jsonl = Join-Path $RunDir "safe-facebook-http-capture.jsonl"
$StdoutLog = Join-Path $RunDir "mitmdump.stdout.log"
$StderrLog = Join-Path $RunDir "mitmdump.stderr.log"
$AnalysisDir = Join-Path $RunDir "analysis"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$previousProxy = ""
$mitm = $null

function Dump-Ui([string]$Path) {
    $remote = "/sdcard/codex-facebook-ui.xml"
    & adb -s $Serial shell uiautomator dump $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UI dump failed: $Path" }
    & adb -s $Serial pull $remote $Path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UI dump pull failed: $Path" }
}

try {
    & adb -s $Serial get-state | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "ADB device is not ready: $Serial" }
    if (-not (Get-Command mitmdump -ErrorAction SilentlyContinue)) { throw "mitmdump is not on PATH" }
    $previousProxy = ((& adb -s $Serial shell settings get global http_proxy | Out-String).Trim())

    $env:FACEBOOK_CAPTURE_DIR = $RunDir
    $env:FACEBOOK_CAPTURE_MAX_FLOWS = [string]$MaxFlows
    $env:FACEBOOK_CAPTURE_MAX_BODY_BYTES = [string]$MaxBodyBytes
    $mitmArgs = @(
        "--quiet",
        "--listen-host", "0.0.0.0",
        "--listen-port", [string]$ProxyPort,
        "--mode", "upstream:http://127.0.0.1:7897",
        "-s", $Addon
    )
    $mitm = Start-Process -FilePath "mitmdump" -ArgumentList $mitmArgs -WorkingDirectory $RunDir -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog -PassThru
    Start-Sleep -Seconds 3
    if ($mitm.HasExited) { throw "mitmdump exited early; inspect $StderrLog" }

    & adb -s $Serial shell settings put global http_proxy ("10.0.2.2:{0}" -f $ProxyPort)
    if ($LaunchFacebook) {
        & adb -s $Serial shell am force-stop com.facebook.katana
        & adb -s $Serial shell monkey -p com.facebook.katana 1 | Out-Null
        Start-Sleep -Seconds 5
    }

    for ($i = 0; $i -lt $ScrollCount; $i++) {
        & adb -s $Serial shell input swipe 540 1700 540 650 600
        Dump-Ui (Join-Path $RunDir ("ui-scroll-{0:00}.xml" -f ($i + 1)))
        Start-Sleep -Seconds $ScrollDelaySeconds
    }
    if ($OpenShareList -or $OpenComments) {
        $readonlyArgs = @("-Serial", $Serial, "-WaitSeconds", "4")
        if ($OpenShareList) { $readonlyArgs += "-OpenShareList" }
        if ($OpenComments) { $readonlyArgs += "-OpenComments" }
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Base "explore_facebook_readonly.ps1") @readonlyArgs
    }
    Start-Sleep -Seconds ([Math]::Max(0, $DurationSeconds - ($ScrollCount * $ScrollDelaySeconds)))
    Write-Output "Capture finished: $RunDir"
}
finally {
    if ($mitm -and -not $mitm.HasExited) {
        Stop-Process -Id $mitm.Id -Force -ErrorAction SilentlyContinue
        $mitm.WaitForExit()
    }
    if ($previousProxy -and $previousProxy -ne "null") {
        & adb -s $Serial shell settings put global http_proxy $previousProxy
    } else {
        & adb -s $Serial shell settings put global http_proxy :0
    }
    Remove-Item Env:FACEBOOK_CAPTURE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_CAPTURE_MAX_FLOWS -ErrorAction SilentlyContinue
    Remove-Item Env:FACEBOOK_CAPTURE_MAX_BODY_BYTES -ErrorAction SilentlyContinue
}

if (Test-Path $Jsonl) {
    python (Join-Path $Base "analyze_facebook_capture.py") --input $Jsonl --out-dir $AnalysisDir
    python (Join-Path $Base "build_network_data.py") --analysis-dir $AnalysisDir --out-dir $AnalysisDir
}
