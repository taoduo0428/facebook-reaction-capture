<#
.SYNOPSIS
    Fetch Facebook reactions/shares over plain HTTP from an existing capture.

.DESCRIPTION
    Reuses the OAuth token and persisted-query templates already present in a
    raw mitmproxy archive, then pages the connection with direct HTTP instead of
    driving the UI.  Output is written in the same JSONL shape the mitmproxy
    addon produces, so analyze_facebook_capture.py consumes it unchanged.

    The UI path stays untouched: this script is additive and only reads a
    capture that some earlier run already produced.

.PARAMETER RawMitm
    Path to raw-facebook-http.mitm from a previous capture.

.PARAMETER PostId
    Numeric post id stamped on every record (required for source binding).

.PARAMETER OutputDir
    Directory for the generated JSONL, CSVs and summary.

.PARAMETER SleepMs
    Delay between pages.  Default 1000ms; lowering it raises rate-limit risk.

.PARAMETER Workers
    Concurrent posts (only meaningful for batch mode).

.PARAMETER IncludeShares
    Also page ResharesOfContentFeedQuery.

.EXAMPLE
    .\run_direct_capture.ps1 -RawMitm runs\live-verify-full\raw-facebook-http.mitm `
        -PostId 1691321539667513 -OutputDir runs\direct-only -IncludeShares
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RawMitm,
    [string]$PostId = '',
    [string]$PostIds = '',
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [int]$SleepMs = 1000,
    [int]$Workers = 2,
    [int]$MaxPages = 200,
    [switch]$IncludeShares,
    [switch]$Append,
    [switch]$SkipAnalyze,
    [switch]$NoResume,
    [string]$TemplateJson = ''
)

$ErrorActionPreference = 'Stop'
$Base = $PSScriptRoot
$python = Join-Path $Base '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $python = [string]$pythonCommand.Source
}
$direct = Join-Path $Base 'facebook_graphql_direct.py'
$analyzer = Join-Path $Base 'analyze_facebook_capture.py'
if (-not (Test-Path -LiteralPath $direct)) { throw "Missing direct module: $direct" }
if (-not (Test-Path -LiteralPath $analyzer)) { throw "Missing analyzer: $analyzer" }
if (-not (Test-Path -LiteralPath $RawMitm)) { throw "Raw capture not found: $RawMitm" }

# Accept either a single -PostId or a comma/space separated -PostIds batch.
$postList = @()
foreach ($candidate in @($PostId, $PostIds)) {
    if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
    foreach ($piece in ($candidate -split '[,\s]+')) {
        $trimmed = $piece.Trim()
        if ($trimmed) { $postList += $trimmed }
    }
}
$postList = @($postList | Select-Object -Unique)
if ($postList.Count -eq 0) { throw 'Provide -PostId or -PostIds.' }
foreach ($candidate in $postList) {
    if ($candidate -notmatch '^[0-9]{1,32}$') { throw "post id must be numeric: $candidate" }
}
$primaryPostId = $postList[0]
$batchIds = $postList -join ','

$out = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $out | Out-Null
$jsonl = Join-Path $out 'safe-facebook-http-capture.jsonl'
if (-not $Append -and (Test-Path -LiteralPath $jsonl)) { Remove-Item -LiteralPath $jsonl -Force }

$sleepSeconds = [Math]::Round([Math]::Max(0, $SleepMs) / 1000.0, 3)
$stateDir = Join-Path $out 'state'
if ($NoResume -and (Test-Path -LiteralPath $stateDir)) {
    Remove-Item -LiteralPath $stateDir -Recurse -Force
    Write-Host '[direct] -NoResume: cleared saved cursors, paging restarts at page 1'
}
$clock = [System.Diagnostics.Stopwatch]::StartNew()

function Invoke-DirectFamily {
    param([string]$Friendly, [string]$Label)

    $arguments = @(
        $direct,
        '--mitm', $RawMitm,
        '--friendly', $Friendly,
        '--post-ids', $batchIds,
        '--workers', [string][Math]::Max(1, $Workers),
        '--max-pages', [string]$MaxPages,
        '--sleep', [string]$sleepSeconds,
        '--jsonl-out', $jsonl,
        '--source-post-id', $primaryPostId,
        '--state', $stateDir,
        '--out', (Join-Path $out ("direct-{0}.csv" -f $Label))
    )
    if (-not [string]::IsNullOrWhiteSpace($TemplateJson) -and (Test-Path -LiteralPath $TemplateJson)) {
        $arguments += @('--template-json', $TemplateJson)
    }
    $familyClock = [System.Diagnostics.Stopwatch]::StartNew()
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Direct $Label fetch failed with exit code $LASTEXITCODE" }
    Write-Host ("[direct] {0} finished in {1:N1}s" -f $Label, $familyClock.Elapsed.TotalSeconds)
}

Invoke-DirectFamily -Friendly 'FeedbackReactorsGraphService' -Label 'reactions'

if ($IncludeShares) {
    # Shares need their own template family; skip cleanly when neither the
    # capture nor the persisted template file knows ResharesOfContentFeedQuery
    # (Reels never produce one).
    $probeArgs = @($direct, '--mitm', $RawMitm, '--list')
    if (-not [string]::IsNullOrWhiteSpace($TemplateJson) -and (Test-Path -LiteralPath $TemplateJson)) {
        $probeArgs += @('--template-json', $TemplateJson)
    }
    $probe = & $python @probeArgs 2>&1
    if (($probe -join "`n") -match 'ResharesOfContentFeedQuery') {
        Invoke-DirectFamily -Friendly 'ResharesOfContentFeedQuery' -Label 'shares'
    }
    else {
        Write-Warning '[direct] capture has no ResharesOfContentFeedQuery; shares skipped.'
    }
}

if (-not (Test-Path -LiteralPath $jsonl)) { throw 'Direct fetch produced no JSONL records.' }
$recordCount = (Get-Content -LiteralPath $jsonl | Measure-Object -Line).Lines
if ($recordCount -le 0) { throw 'Direct fetch produced an empty JSONL.' }

$analysis = Join-Path $out 'analysis'
if (-not $SkipAnalyze) {
    & $python $analyzer --input $jsonl --out-dir $analysis --expected-post-id $primaryPostId --focus-source-post-id $primaryPostId --min-line 1
    if ($LASTEXITCODE -ne 0) { throw "Analyzer failed with exit code $LASTEXITCODE" }
}

$reactorCsv = if ($SkipAnalyze) {
    Join-Path $out 'direct-reactions.csv'
}
else {
    Join-Path $analysis 'facebook-reactor-users.csv'
}
$shareCsv = if ($SkipAnalyze) {
    Join-Path $out 'direct-shares.csv'
}
else {
    Join-Path $analysis 'facebook-share-users.csv'
}
$reactorRows = if (Test-Path -LiteralPath $reactorCsv) { [Math]::Max(0, (Get-Content -LiteralPath $reactorCsv | Measure-Object -Line).Lines - 1) } else { 0 }
$shareRows = if (Test-Path -LiteralPath $shareCsv) { [Math]::Max(0, (Get-Content -LiteralPath $shareCsv | Measure-Object -Line).Lines - 1) } else { 0 }

$summary = [ordered]@{
    post_id = $primaryPostId
    post_ids = $postList
    post_count = $postList.Count
    raw_mitm = $RawMitm
    jsonl = $jsonl
    records = $recordCount
    reactor_rows = $reactorRows
    share_rows = $shareRows
    sleep_seconds = $sleepSeconds
    workers = [Math]::Max(1, $Workers)
    analyzed = (-not $SkipAnalyze)
    elapsed_seconds = [Math]::Round($clock.Elapsed.TotalSeconds, 1)
    completed_at = (Get-Date).ToUniversalTime().ToString('o')
}
($summary | ConvertTo-Json -Depth 3) | Set-Content -LiteralPath (Join-Path $out 'direct-summary.json') -Encoding UTF8
Write-Host ("[direct] done posts={0} records={1} reactors={2} shares={3} elapsed={4:N1}s" -f $postList.Count, $recordCount, $reactorRows, $shareRows, $clock.Elapsed.TotalSeconds)
