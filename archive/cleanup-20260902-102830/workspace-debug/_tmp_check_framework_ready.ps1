$ErrorActionPreference='Continue'
. (Join-Path $PSScriptRoot 'facebook_transport.ps1')
$serial='emulator-5554'
$result=Ensure-FacebookFrameworkServicesReady -Serial $serial -TimeoutSeconds 10
Write-Output ('framework=' + $result)
