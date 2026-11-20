$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @('facebook_transport.ps1','start_facebook_stable.ps1','run_facebook_capture.ps1','open_facebook_raw_capture.ps1')
foreach ($file in $files) {
    $errors = $null
    $tokens = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Base $file), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        Write-Output "$file FAIL"
        $errors | ForEach-Object { Write-Output $_.Message }
        exit 1
    }
    Write-Output "$file OK"
}
. (Join-Path $Base 'facebook_transport.ps1')
$serial = 'emulator-5554'
$state = Get-FacebookAdbState -Serial $serial
Write-Output "adb_state=$state"
if ($state -eq 'device') {
    $framework = Test-FacebookFrameworkServicesReady -Serial $serial
    Write-Output "framework_ready=$framework"
    if (-not $framework) { exit 2 }
}
