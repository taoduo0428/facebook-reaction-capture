[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$ProxyPort = 8081,
    [int]$ReactionNativeScrollPort = 18766,
    [switch]$EnableFridaSslUnpin,
    [string]$PostUrl = "",
    [string]$PostId = "",
    [int]$TargetOpenWaitSeconds = 8
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path $PowerShellExe)) {
    $powerShellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if (-not $powerShellCommand) { throw 'Windows PowerShell executable could not be resolved.' }
    $PowerShellExe = [string]$powerShellCommand.Source
}

if ([string]::IsNullOrWhiteSpace($PostUrl) -and [string]::IsNullOrWhiteSpace($PostId)) {
    throw "PostUrl or PostId is required."
}

$argsList = @(
    '-Serial', $Serial,
    '-ProxyPort', [string]$ProxyPort,
    '-ReactionNativeScrollPort', [string]$ReactionNativeScrollPort,
    '-LaunchFacebook',
    '-OpenReactionList',
    '-ReactionProbeOnly',
    '-ScrollCount', '0',
    '-ReactionScreenScrollCount', '0',
    '-ReadOnlyWaitSeconds', '1',
    '-ReactionListReadyDelaySeconds', '1',
    '-PostActionGraceSeconds', '0',
    '-TargetOpenWaitSeconds', [string]$TargetOpenWaitSeconds
)
if ($EnableFridaSslUnpin) {
    $argsList += '-EnableFridaSslUnpin'
}
if (-not [string]::IsNullOrWhiteSpace($PostUrl)) {
    $argsList += @('-PostUrl', $PostUrl)
}
if (-not [string]::IsNullOrWhiteSpace($PostId)) {
    $argsList += @('-PostId', $PostId)
}

$RunsRoot = Join-Path $Base 'runs'
$beforeRuns = @{}
if (Test-Path $RunsRoot) {
    Get-ChildItem -Path $RunsRoot -Directory | ForEach-Object { $beforeRuns[$_.FullName] = $true }
}

Write-Host '[probe] Opening only the initial Reaction list. No page2 pagination, category traversal, or fallback path will run.'
$child = Invoke-FacebookPowerShellFileCaptured -PowerShellExe $PowerShellExe -ScriptPath (Join-Path $Base 'run_facebook_capture.ps1') -ScriptArguments $argsList -WorkingDirectory $Base
Write-FacebookChildProcessOutput -Result $child
$probeExitCode = [int]$child.ExitCode

if (Test-Path $RunsRoot) {
    $newRun = Get-ChildItem -Path $RunsRoot -Directory |
        Where-Object { -not $beforeRuns.ContainsKey($_.FullName) } |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if ($newRun) {
        $diagPath = Join-Path $newRun.FullName 'readonly-ui\exploration-diagnostics.json'
        $capturePath = Join-Path $newRun.FullName 'safe-facebook-http-capture.jsonl'
        if (Test-Path $diagPath) {
            try {
                $diag = Get-Content -Raw -Encoding UTF8 $diagPath | ConvertFrom-Json
                $screen = $diag.reaction_screening
                $page2Requests = 0
                if (Test-Path $capturePath) {
                    $page2Requests = @(Select-String -Path $capturePath -SimpleMatch 'FeedbackReactorsGraphService_At_Connection_Pagination').Count
                }
                $paginationRuns = @($diag.pagination_runs).Count
                Write-Host ("[probe-result] run={0} status={1} users={2} queries={3} session_restricted={4} rate_limited={5} pagination_runs={6} page2_requests={7}" -f `
                    $newRun.Name,
                    [string]$screen.probe_status,
                    [int]$screen.user_count,
                    [int]$screen.query_records,
                    [bool]$screen.session_restricted,
                    [bool]$screen.latest_rate_limited,
                    $paginationRuns,
                    $page2Requests)
                if ($page2Requests -gt 0 -or $paginationRuns -gt 0) {
                    Write-Warning '[probe] ProbeOnly invariant violated: pagination activity was observed.'
                    if ($probeExitCode -eq 0) { $probeExitCode = 2 }
                }
            }
            catch {
                Write-Warning "[probe] Could not summarize diagnostics: $($_.Exception.Message)"
            }
        }
    }
}

exit $probeExitCode
