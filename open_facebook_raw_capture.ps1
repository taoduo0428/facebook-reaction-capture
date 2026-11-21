[CmdletBinding()]
param(
    [string]$CaptureFile = "",
    [int]$WebPort = 8082
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper

if ([string]::IsNullOrWhiteSpace($CaptureFile)) {
    $candidate = Get-ChildItem -Path (Join-Path $Base 'runs') -Filter 'raw-facebook-http.mitm' -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $candidate) { throw 'No raw-facebook-http.mitm capture exists under runs yet.' }
    $CaptureFile = $candidate.FullName
}
elseif (-not [IO.Path]::IsPathRooted($CaptureFile)) {
    $CaptureFile = Join-Path $Base $CaptureFile
}

$CaptureFile = [IO.Path]::GetFullPath($CaptureFile)
if (-not (Test-Path $CaptureFile -PathType Leaf)) { throw "Raw capture not found: $CaptureFile" }
if ([IO.Path]::GetExtension($CaptureFile) -ne '.mitm') { throw "Expected a .mitm raw flow archive: $CaptureFile" }

$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
$MitmwebExe = Join-Path (Split-Path -Parent $PythonExe) 'Scripts\mitmweb.exe'
if (-not (Test-Path $MitmwebExe)) {
    $command = Get-Command mitmweb.exe -ErrorAction SilentlyContinue
    if (-not $command) { throw 'mitmweb executable could not be resolved.' }
    $MitmwebExe = [string]$command.Source
}

$arguments = @(
    '--set', 'server=false',
    '--web-host', '127.0.0.1',
    '--web-port', [string]$WebPort,
    '-r', $CaptureFile
)
$process = Start-Process -FilePath $MitmwebExe -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $CaptureFile) -PassThru
Write-Host "Raw capture: $CaptureFile"
Write-Host "mitmweb PID: $($process.Id)"
Write-Host "Viewer: http://127.0.0.1:$WebPort/"
