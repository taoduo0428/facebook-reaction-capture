[CmdletBinding()]
param(
    [int]$UiPort = 8766,
    [int]$ApiPort = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $env:SystemRoot 'py.exe'
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}

$BackendRunning = $false
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health" -TimeoutSec 1 -ErrorAction SilentlyContinue
    if ($health) { $BackendRunning = $true }
}
catch {}

if (-not $BackendRunning) {
    Write-Host "[OmniCapture v2] Backend API on port $ApiPort is not detected." -ForegroundColor Yellow
    Write-Host "[OmniCapture v2] Starting backend API in background..." -ForegroundColor Cyan
    $apiScript = Join-Path $Base 'start_facebook_capture_api.ps1'
    if (Test-Path $apiScript) {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $apiScript, "-Port", "$ApiPort") -WindowStyle Minimized -PassThru
        Write-Host "[OmniCapture v2] Backend API started (PID: $($p.Id)). Waiting for ready..." -ForegroundColor Green
        Start-Sleep -Seconds 2
    }
}

$serverScript = Join-Path $Base 'start_ui_v2.py'
$argsList = @($serverScript, "--port", "$UiPort", "--api-target", "http://127.0.0.1:$ApiPort")
if ($NoBrowser) {
    $argsList += "--no-browser"
}

Write-Host "[OmniCapture v2] Launching OmniCapture Console v2 on http://127.0.0.1:$UiPort/ ..." -ForegroundColor Green
& $PythonExe @argsList
