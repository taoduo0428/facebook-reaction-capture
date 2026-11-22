param([Parameter(Mandatory=$true)][string]$Source)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$node = $tree.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Scroll-ReactionAdaptive' }, $true)
if (-not $node) { throw 'Missing pagination function' }
$body = @'
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
function Get-CaptureProgress { return $script:ProgressFixture }
function Test-ExpectedSourceGuard { $script:GuardCalls++; return $script:GuardFixture }
function Scroll-And-Dump { $script:ScrollCalls++ }
function Get-ReactionGroup { throw 'PASSED_INITIAL_GUARDS' }
$rows = @()
foreach ($cap in @(0, 5)) {
    $ReactionAbsoluteCap = $cap
    foreach ($mode in @('missing-strict', 'missing-legacy', 'mismatch', 'valid')) {
        $ExpectedSourcePostId = if ($mode -eq 'missing-legacy') { '' } else { '123' }
        $script:ProgressFixture = if ($mode.StartsWith('missing')) { $null } else { [pscustomobject]@{latest_line=1} }
        $script:GuardFixture = ($mode -eq 'valid')
        $script:GuardCalls = 0; $script:ScrollCalls = 0
        $result = $null; $continued = $false
        try { $result = Scroll-ReactionAdaptive -Prefix 'fixture' -Count 3 -BaselineProgress $null -PreferredReactionKey '__ALL__' }
        catch {
            if ($_.Exception.Message -ne 'PASSED_INITIAL_GUARDS') { throw }
            $continued = $true
        }
        if ($mode -eq 'valid') {
            if (-not $continued -or $script:GuardCalls -ne 1 -or $script:ScrollCalls -ne 0) { throw 'Valid route changed' }
        } else {
            $expectedReason = switch ($mode) { 'missing-strict' {'strict-progress-unavailable'} 'missing-legacy' {'legacy-fixed-scroll'} 'mismatch' {'target-mismatch'} }
            $legacy = $mode -eq 'missing-legacy'
            $expectedAttempts = if ($legacy) { 3 } else { 0 }
            $expectedGuardCalls = if ($mode -eq 'mismatch') { 1 } else { 0 }
            $expectedScrollCalls = if ($legacy) { 1 } else { 0 }
            if ($result.stop_reason -ne $expectedReason -or $result.attempts -ne $expectedAttempts -or
                $result.final_budget -ne $expectedAttempts -or $result.terminal_seen -or $result.open_seen -or
                $script:GuardCalls -ne $expectedGuardCalls -or $script:ScrollCalls -ne $expectedScrollCalls) { throw "Early return changed: $mode" }
        }
        $rows += [ordered]@{mode=$mode; cap=$cap; result=$result; continued=$continued; guard_calls=$script:GuardCalls; scroll_calls=$script:ScrollCalls}
    }
}
ConvertTo-Json -InputObject $rows -Depth 8 -Compress
'@
# Load only defaults and the tested function; never run script initialization or ADB.
$test = [scriptblock]::Create($tree.ParamBlock.Extent.Text + "`n" + $node.Extent.Text + "`n" + $body)
& $test
