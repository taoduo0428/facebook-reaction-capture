$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')
$serial = 'emulator-5554'
$out = Join-Path $Base 'current-ui-probe.xml'
$snapshot = Invoke-FacebookUiSnapshot -Serial $serial -LocalPath $out -RemotePath '/sdcard/facebook-current-ui.xml' -LegacyTimeoutSeconds 10 -LegacyRetryCount 0 -FastTimeoutSeconds 12
Write-Output ("success=" + $snapshot.Success)
Write-Output ("source=" + $snapshot.Source)
Write-Output ("package=" + $snapshot.RootPackage)
Write-Output ("nodes=" + $snapshot.NodeCount)
Write-Output ("error=" + $snapshot.Error)
if ($snapshot.Success) {
    $raw = [IO.File]::ReadAllText($out)
    Write-Output ("blocking=" + (Get-FacebookUiBlockingState -Raw $raw))
    Write-Output ("surface=" + (Get-FacebookUiSurfacePackage -Raw $raw))
}
