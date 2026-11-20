[CmdletBinding()]
param(
    [string]$Serial = 'emulator-5554',
    [string]$TaskName = 'FacebookEmulatorNetworkWatchdog'
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$Watch = Join-Path $Base 'facebook_network_watch.ps1'
if (-not (Test-Path $Watch)) { throw "Missing watchdog: $Watch" }

$powerShellExe = (Get-Command powershell.exe).Source
$taskCommand = '"{0}" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}" -Serial "{2}"' -f $powerShellExe, $Watch, $Serial
$installed = $false
try {
    & schtasks.exe /Create /TN $TaskName /SC ONLOGON /TR $taskCommand /F 2>$null | Out-Null
    $installed = ($LASTEXITCODE -eq 0)
} catch {
    $installed = $false
}

if (-not $installed) {
    $startup = [Environment]::GetFolderPath('Startup')
    if ([string]::IsNullOrWhiteSpace($startup)) { throw 'Could not resolve the current-user Startup folder.' }
    $fallback = Join-Path $startup 'FacebookEmulatorNetworkWatchdog.cmd'
    $cmd = '@echo off' + "`r`n" + 'start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Watch + '" -Serial "' + $Serial + '"' + "`r`n"
    Set-Content -Encoding ASCII -Path $fallback -Value $cmd
    Write-Host "Startup fallback installed: $fallback"
} else {
    Write-Host "Scheduled task installed: $TaskName"
}

. (Join-Path $Base 'facebook_transport.ps1')
$pidStarted = Start-FacebookTransportWatch -Serial $Serial -BasePath $Base
Write-Host "Watchdog active pid=$pidStarted"
