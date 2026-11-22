param([Parameter(Mandatory = $true)][int]$ExpectedListenerPid)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$apiScript = Join-Path $projectRoot 'facebook_capture_api.py'
$apiPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 15
if (($health.worker -ne 'idle' -and -not ($health.worker -eq 'blocked' -and $health.queued_jobs -eq 0 -and $health.cancelling_jobs -eq 0 -and $health.capture_lease.state -eq 'uncertain')) -or $health.queued_jobs -ne 0 -or $health.cancelling_jobs -ne 0 -or ($health.capture_lease.state -ne 'available' -and $health.capture_lease.reason -notlike '*watchdog could not be started*')) {
    throw 'API is not idle with an available capture lease; refusing restart.'
}
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8765)
if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne '127.0.0.1' -or $listeners[0].OwningProcess -ne $ExpectedListenerPid) {
    throw 'API listener identity changed; refusing restart.'
}
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$ExpectedListenerPid"
if (-not $process -or $process.CommandLine -notlike "*$apiPython*" -or $process.CommandLine -notlike "*$apiScript*") {
    throw 'The listener is not the expected project API interpreter.'
}
$emulatorsBefore = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^qemu.*\.exe$' } | Select-Object -ExpandProperty ProcessId)
# Recheck current ownership immediately before the one-process stop.
if ((Get-NetTCPConnection -State Listen -LocalPort 8765).OwningProcess -ne $ExpectedListenerPid) {
    throw 'API port owner changed before stop.'
}
Stop-Process -Id $ExpectedListenerPid -ErrorAction Stop
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (-not (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 250
}
if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) { throw 'API port did not become free.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$launcher = Join-Path $projectRoot 'start_facebook_capture_api.ps1'
$started = Start-Process -FilePath 'powershell.exe' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $launcher + '"'), '-Port', '8765'
) -RedirectStandardOutput (Join-Path $PSScriptRoot "api-$stamp.stdout.log") -RedirectStandardError (Join-Path $PSScriptRoot "api-$stamp.stderr.log")
$ready = $null
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    try { $ready = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 3; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw "API did not become ready; launcher PID=$($started.Id)" }
$emulatorsAfter = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^qemu.*\.exe$' } | Select-Object -ExpandProperty ProcessId)
if (@(Compare-Object $emulatorsBefore $emulatorsAfter).Count -ne 0) { throw 'Emulator process inventory changed during API restart.' }
[ordered]@{
    old_api_pid = $ExpectedListenerPid
    new_api_pid = (Get-NetTCPConnection -State Listen -LocalPort 8765).OwningProcess
    emulator_pids_preserved = $emulatorsAfter
    worker = $ready.worker
    active_worker_id = $ready.active_worker_id
    queued_jobs = $ready.queued_jobs
    cancelling_jobs = $ready.cancelling_jobs
    capture_lease = $ready.capture_lease
} | ConvertTo-Json -Depth 4
