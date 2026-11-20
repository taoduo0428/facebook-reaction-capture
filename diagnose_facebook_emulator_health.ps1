[CmdletBinding()]
param([string]$Serial = 'emulator-5554')

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')

$packagesResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','pm','list','packages') -TimeoutSeconds 12
$wellbeingPackages = @(([string]$packagesResult.Stdout -split "`r?`n") | Where-Object { $_ -match '(?i)wellbeing|digitalwellbeing' } | ForEach-Object { ($_ -replace '^package:', '').Trim() })
$stateResult = Invoke-AdbCommandBounded -Arguments @('-s',$Serial,'get-state') -TimeoutSeconds 6
$focusResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','dumpsys','window','windows') -TimeoutSeconds 12
$focus = @(([string]$focusResult.Stdout -split "`r?`n") | Where-Object { $_ -match 'mCurrentFocus|mFocusedApp' } | Select-Object -First 4)
$anrResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell','dumpsys','activity','processes') -TimeoutSeconds 12
$anr = @(([string]$anrResult.Stdout -split "`r?`n") | Where-Object { $_ -match '(?i)not responding|anr' } | Select-Object -First 8)

[ordered]@{
    serial = $Serial
    adb_state = ([string]$stateResult.Stdout).Trim()
    wellbeing_packages = @($wellbeingPackages)
    focus = @($focus)
    anr_lines = @($anr)
    package_query_ok = [bool]$packagesResult.Success
    focus_query_ok = [bool]$focusResult.Success
    anr_query_ok = [bool]$anrResult.Success
} | ConvertTo-Json -Depth 5
