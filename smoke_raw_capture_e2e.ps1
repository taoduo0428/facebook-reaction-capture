$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'facebook_runtime.ps1')
$python = Resolve-FacebookPythonExe -BasePath $Base
$mitmdump = Resolve-FacebookMitmdumpExe -PythonExe $python
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$run = Join-Path $Base ("runs\raw-e2e-$stamp")
New-Item -ItemType Directory -Force -Path $run | Out-Null
$raw = Join-Path $run 'raw-facebook-http.mitm'
$index = Join-Path $run 'raw-facebook-http-index.jsonl'
$summary = Join-Path $run 'raw-summary.json'
$serverOut = Join-Path $run 'server.stdout.log'
$serverErr = Join-Path $run 'server.stderr.log'
$mitmOut = Join-Path $run 'mitmdump.stdout.log'
$mitmErr = Join-Path $run 'mitmdump.stderr.log'
$env:FACEBOOK_CAPTURE_DIR = $run
$env:FACEBOOK_RAW_CAPTURE_FILE = $raw
$env:FACEBOOK_RAW_INDEX_FILE = $index
$server = $null
$mitm = $null
try {
    $server = Start-Process -FilePath $python -ArgumentList @('-m','http.server','18180','--bind','127.0.0.1') -WorkingDirectory $run -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr -PassThru
    Start-Sleep -Seconds 1
    $mitmArgs = @('--quiet','--listen-host','127.0.0.1','--listen-port','18181','--mode','reverse:http://127.0.0.1:18180','--set','keep_host_header=true','-s',(Join-Path $Base 'facebook_raw_flow_writer.py'))
    $mitm = Start-Process -FilePath $mitmdump -ArgumentList $mitmArgs -WorkingDirectory $run -RedirectStandardOutput $mitmOut -RedirectStandardError $mitmErr -PassThru
    Start-Sleep -Seconds 2
    if ($mitm.HasExited) { throw "mitmdump exited early code=$($mitm.ExitCode)" }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:18181/graphql?doc_id=local-smoke' -Headers @{ Host='facebook.com'; 'x-fb-friendly-name'='LocalSmokeQuery' } -TimeoutSec 10 | Out-Null
    }
    catch {
        # A 404 from the local HTTP server is acceptable; mitmproxy still receives a response flow.
        if ($_.Exception.Response -eq $null) { throw }
    }
    Start-Sleep -Seconds 1
}
finally {
    if ($mitm -and -not $mitm.HasExited) { Stop-Process -Id $mitm.Id -Force -ErrorAction SilentlyContinue }
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
}
if (-not (Test-Path -LiteralPath $raw)) {
    if (Test-Path -LiteralPath $mitmErr) { Get-Content -Raw $mitmErr | Write-Output }
    throw 'raw mitm archive was not created'
}
$inspectOut = Join-Path $run 'inspect.stdout.log'
$inspectErr = Join-Path $run 'inspect.stderr.log'
$inspectArgs = @((Join-Path $Base 'inspect_facebook_raw_capture.py'),'--input',$raw,'--out',$summary)
$inspect = Start-Process -FilePath $python -ArgumentList $inspectArgs -WorkingDirectory $Base -RedirectStandardOutput $inspectOut -RedirectStandardError $inspectErr -Wait -PassThru
if ($inspect.ExitCode -ne 0) {
    $detail = if (Test-Path -LiteralPath $inspectErr) { Get-Content -Raw $inspectErr } else { '' }
    throw "raw inspector failed exit=$($inspect.ExitCode): $detail"
}
$report = Get-Content -Raw -Encoding UTF8 $summary | ConvertFrom-Json
Write-Output "run=$run"
Write-Output "raw=$raw"
Write-Output "http_flow_count=$($report.http_flow_count)"
Write-Output "graphql_like_count=$($report.graphql_like_count)"
Write-Output "hosts=$(@($report.hosts) -join ',')"
if ([int]$report.http_flow_count -lt 1) { throw 'expected at least one captured HTTP flow' }
if ([int]$report.graphql_like_count -lt 1) { throw 'expected at least one graphql-like captured flow' }
