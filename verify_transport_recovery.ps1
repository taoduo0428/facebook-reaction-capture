$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_transport.ps1')

$fixture = Join-Path $Base 'runs\20260901-013239\ui-ready-01.xml'
if (-not (Test-Path $fixture)) { throw "Missing ANR fixture: $fixture" }
$raw = [IO.File]::ReadAllText($fixture)
$state = Get-FacebookUiBlockingState -Raw $raw
Write-Output "anr_state=$state"
if ($state -ne 'system-ui-not-responding') { throw "Expected system-ui-not-responding, got: $state" }

$transportText = [IO.File]::ReadAllText((Join-Path $Base 'facebook_transport.ps1'))
$oldPattern = "'connect-network','AndroidWifi','open')"
$newPattern = "'connect-network','AndroidWifi','open','-r','none')"
$oldCount = ([regex]::Matches($transportText, [regex]::Escape($oldPattern))).Count
$newCount = ([regex]::Matches($transportText, [regex]::Escape($newPattern))).Count
Write-Output "legacy_wifi_connect_count=$oldCount"
Write-Output "stable_wifi_connect_count=$newCount"
if ($oldCount -ne 0) { throw "Legacy AndroidWifi connect command remains: $oldCount" }
if ($newCount -lt 3) { throw "Expected at least 3 stable AndroidWifi connect commands, got: $newCount" }

$errors = $null
$tokens = $null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Base 'facebook_transport.ps1'), [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Output $_.Message }
    throw "facebook_transport.ps1 parser errors: $($errors.Count)"
}
Write-Output 'facebook_transport.ps1 parser=OK'
