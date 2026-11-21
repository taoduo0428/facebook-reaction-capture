[CmdletBinding()]
param(
    [string]$Serial = 'emulator-5554',
    [switch]$Reboot,
    [bool]$DisableDigitalWellbeing = $true
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')

if (-not (Ensure-FacebookAdbReady -Serial $Serial -TimeoutSeconds 15 -RecoverOffline -AllowServerRestart)) {
    throw "ADB device is not ready: $Serial"
}

$wellbeingPackage = 'com.google.android.apps.wellbeing'
$packages = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','pm','list','packages',$wellbeingPackage) -TimeoutSeconds 10
$wellbeingInstalled = $packages.Success -and ([string]$packages.Stdout -match [regex]::Escape($wellbeingPackage))
$forceStopped = $false
$disabled = $false
$disableOutput = ''

if ($wellbeingInstalled) {
    $forceStop = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','am','force-stop',$wellbeingPackage) -TimeoutSeconds 10
    $forceStopped = [bool]$forceStop.Success
    if ($DisableDigitalWellbeing) {
        $disable = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','pm','disable-user','--user','0',$wellbeingPackage) -TimeoutSeconds 12
        $disableOutput = (([string]$disable.Stdout) + ' ' + ([string]$disable.Stderr)).Trim()
        $disabled = [bool]$disable.Success -and ($disableOutput -match '(?i)disabled-user|new state: disabled')
    }
}

$dismissed = $false
$tempUi = Join-Path $Base 'emulator-health-ui.xml'
try {
    $snapshot = Invoke-FacebookUiSnapshot -Serial $Serial -LocalPath $tempUi -RemotePath '/sdcard/facebook-emulator-health-ui.xml' -LegacyTimeoutSeconds 10 -LegacyRetryCount 1 -FastTimeoutSeconds 8
    if ($snapshot.Success -and (Test-Path $tempUi)) {
        $raw = [IO.File]::ReadAllText($tempUi)
        $blocking = Get-FacebookUiBlockingState -Raw $raw
        if ($blocking -eq 'system-ui-not-responding') {
            $dismissed = [bool](Dismiss-FacebookSystemUiAnr -Serial $Serial -Raw $raw)
        }
    }
}
catch {}
finally {
    Remove-Item -Force $tempUi -ErrorAction SilentlyContinue
}

$rebootIssued = $false
if ($Reboot) {
    $result = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('reboot') -TimeoutSeconds 10
    $rebootIssued = [bool]$result.Success
}

[ordered]@{
    serial = $Serial
    wellbeing_installed = $wellbeingInstalled
    wellbeing_force_stopped = $forceStopped
    wellbeing_disabled = $disabled
    wellbeing_disable_output = $disableOutput
    blocking_anr_dismissed = $dismissed
    reboot_issued = $rebootIssued
} | ConvertTo-Json -Depth 4
