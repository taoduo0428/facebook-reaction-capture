$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')
$serial = 'emulator-5554'
$probe = Join-Path $Base 'current-ui-probe.xml'
if (-not (Test-Path -LiteralPath $probe)) { throw "Missing probe: $probe" }
$raw = [IO.File]::ReadAllText($probe)
$state = Get-FacebookUiBlockingState -Raw $raw
Write-Output "before=$state"
if ($state -notin @('system-ui-not-responding','app-not-responding')) { exit 0 }
$ok = Dismiss-FacebookAnrWait -Serial $serial -Raw $raw
Write-Output "dismissed=$ok"
if (-not $ok) { exit 2 }
Start-Sleep -Seconds 4
$out = Join-Path $Base 'current-ui-after-anr.xml'
$snapshot = Invoke-FacebookUiSnapshot -Serial $serial -LocalPath $out -RemotePath '/sdcard/facebook-current-ui-after.xml' -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
Write-Output "snapshot=$($snapshot.Success)"
if ($snapshot.Success) {
    $after = [IO.File]::ReadAllText($out)
    Write-Output "after_blocking=$(Get-FacebookUiBlockingState -Raw $after)"
    Write-Output "after_surface=$(Get-FacebookUiSurfacePackage -Raw $after)"
}
