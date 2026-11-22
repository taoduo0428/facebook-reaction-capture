$ErrorActionPreference='Continue'
$base=$PSScriptRoot
. (Join-Path $base 'facebook_transport.ps1')
$serial='emulator-5554'
Write-Output ('adb=' + (Get-FacebookAdbState -Serial $serial))
$r1=Invoke-FacebookAdbCommand -Serial $serial -Arguments @('shell','service','check','connectivity') -TimeoutSeconds 8
Write-Output ('connectivity.success=' + $r1.Success)
Write-Output ('connectivity.stdout=' + ([string]$r1.Stdout).Trim())
Write-Output ('connectivity.stderr=' + ([string]$r1.Stderr).Trim())
$r2=Invoke-FacebookAdbCommand -Serial $serial -Arguments @('shell','service','list') -TimeoutSeconds 12
Write-Output ('services.success=' + $r2.Success)
if($r2.Success){ Write-Output (([string]$r2.Stdout) -split "`r?`n" | Select-Object -First 40) }
