[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$ProxyPort = 8081,
    [string]$PostUrl = "",
    [string]$PostId = "",
    [Parameter(Mandatory = $true)]
    [string]$CaptureJsonl,
    [Parameter(Mandatory = $true)]
    [string]$OutputDir,
    [int]$WaitSeconds = 8,
    [int]$CaptureMinLine = 1,
    [switch]$KeepFacebookProcess
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $Base 'facebook_runtime.ps1'
if (-not (Test-Path $RuntimeHelper)) { throw "Missing runtime helper: $RuntimeHelper" }
. $RuntimeHelper
$PythonExe = Resolve-FacebookPythonExe -BasePath $Base
if (-not (Test-Path $PythonExe)) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue }
    if (-not $pythonCommand) { throw 'Python executable could not be resolved.' }
    $PythonExe = [string]$pythonCommand.Source
}
$TransportHelper = Join-Path $Base 'facebook_transport.ps1'
if (Test-Path $TransportHelper) { . $TransportHelper }
$Out = [IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$DiagnosticPath = Join-Path $Out "target-open-diagnostics.json"
$ResolvedUrl = $PostUrl
$Method = ""
$UiPath = Join-Path $Out "target-open-ui.xml"
$VerificationMinLine = [Math]::Max(1, $CaptureMinLine)
$VerificationAttempts = 0
$VerificationElapsedMs = 0
$VerificationTimeoutSeconds = [Math]::Max(8, [Math]::Min(20, $WaitSeconds + 8))
$ResolvedPostId = ''
$ResolvedAuthorId = ''
$ResolvedLegacyStoryId = ''

function Get-CaptureJsonlLineCount {
    if (-not (Test-Path $CaptureJsonl)) { return 0 }
    return [int](@([IO.File]::ReadLines($CaptureJsonl)).Count)
}

function Set-VerificationBoundary {
    $nextLine = (Get-CaptureJsonlLineCount) + 1
    $script:VerificationMinLine = [Math]::Max([Math]::Max(1, $CaptureMinLine), $nextLine)
    Write-Host "[target-open] verification window starts at capture line $script:VerificationMinLine"
}

function Write-Diagnostic([bool]$Verified, [string]$Reason, [int]$AnalyzerExit = -1) {
    [ordered]@{
        serial = $Serial
        requested_post_id = $PostId
        requested_post_url = $PostUrl
        resolved_url = $ResolvedUrl
        method = $Method
        verified = $Verified
        reason = $Reason
        analyzer_exit = $AnalyzerExit
        verification_min_line = $VerificationMinLine
        verification_attempts = $script:VerificationAttempts
        verification_elapsed_ms = $script:VerificationElapsedMs
        verification_timeout_seconds = $VerificationTimeoutSeconds
        resolved_post_id = $script:ResolvedPostId
        resolved_author_id = $script:ResolvedAuthorId
        resolved_legacy_api_story_id = $script:ResolvedLegacyStoryId
        ui_path = $UiPath
        timestamp = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $DiagnosticPath
}

function Dump-Ui([string]$Path) {
    $remote = "/sdcard/codex-facebook-target-ui.xml"
    if (Get-Command Invoke-FacebookUiSnapshot -ErrorAction SilentlyContinue) {
        $snapshot = Invoke-FacebookUiSnapshot `
            -Serial $Serial `
            -LocalPath $Path `
            -RemotePath $remote `
            -LegacyTimeoutSeconds 12 `
            -LegacyRetryCount 1 `
            -FastTimeoutSeconds 10
        if (-not $snapshot.Success) {
            throw "UI dump failed via legacy and instrumentation fallback: $($snapshot.Error)"
        }
        if (Get-Command Get-FacebookUiBlockingState -ErrorAction SilentlyContinue) {
            $raw = [IO.File]::ReadAllText($Path)
            $blockingState = Get-FacebookUiBlockingState -Raw $raw
            $anrClose = Get-FacebookUiNodeCenterByResourceId -Raw $raw -ResourceId 'android:id/aerr_close'
            if ($anrClose -or $blockingState -eq 'system-ui-not-responding') {
                Write-Warning '[target-open] Android ANR dialog detected; closing the blocking app before the outer strict target retry.'
                $anrDismissed = $false
                $close = $anrClose
                if ($close) {
                    $anrDismissed = [bool](Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell','input','tap',[string]$close.X,[string]$close.Y) -TimeoutSeconds 5)
                }
                if (-not $anrDismissed) {
                    $anrDismissed = [bool](Dismiss-FacebookSystemUiAnr -Serial $Serial -Raw $raw)
                }
                if (-not $anrDismissed) {
                    throw 'System UI ANR could not be dismissed during target-open'
                }
                Start-Sleep -Milliseconds 2000
                $snapshot = Invoke-FacebookUiSnapshot `
                    -Serial $Serial `
                    -LocalPath $Path `
                    -RemotePath $remote `
                    -LegacyTimeoutSeconds 12 `
                    -LegacyRetryCount 1 `
                    -FastTimeoutSeconds 10
                if (-not $snapshot.Success) {
                    throw "UI dump failed after target-open ANR recovery: $($snapshot.Error)"
                }
            }
        }
        return
    }
    # Legacy command shape: shell uiautomator dump <remote>.
    $dumpResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('shell', 'uiautomator', 'dump', $remote) -TimeoutSeconds 12
    if (-not $dumpResult.Success) { throw "UI dump failed: $($dumpResult.Stderr)" }
    $pullResult = Invoke-FacebookAdbCommand -Serial $Serial -Arguments @('pull', $remote, $Path) -TimeoutSeconds 12
    if (-not $pullResult.Success) { throw "UI dump pull failed: $($pullResult.Stderr)" }
}

function Get-UiNodes([string]$Path) {
    $raw = [IO.File]::ReadAllText($Path)
    foreach ($tag in [regex]::Matches($raw, '<node\b[^>]*>')) {
        $attrs = @{}
        foreach ($a in [regex]::Matches($tag.Value, '([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"')) {
            $attrs[$a.Groups[1].Value] = $a.Groups[2].Value
        }
        $m = [regex]::Match([string]$attrs['bounds'], '^\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]$')
        if (-not $m.Success) { continue }
        $label = if ($attrs['text']) { [string]$attrs['text'] } else { [string]$attrs['content-desc'] }
        [pscustomobject]@{
            Label = $label
            ResourceId = [string]$attrs['resource-id']
            Package = [string]$attrs['package']
            Class = [string]$attrs['class']
            Clickable = ([string]$attrs['clickable'] -eq 'true')
            Enabled = ([string]$attrs['enabled'] -eq 'true')
            X1 = [int]$m.Groups[1].Value
            Y1 = [int]$m.Groups[2].Value
            X2 = [int]$m.Groups[3].Value
            Y2 = [int]$m.Groups[4].Value
            Bounds = [string]$attrs['bounds']
        }
    }
}

function Tap-Node($Node) {
    if (-not $Node) { return $false }
    $x = [int](($Node.X1 + $Node.X2) / 2)
    $y = [int](($Node.Y1 + $Node.Y2) / 2)
    if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'tap', [string]$x, [string]$y))) {
        return $false
    }
    Start-Sleep -Seconds 2
    return $true
}

function Open-InlineFeedCardDetail([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    $detailComposer = $nodes | Where-Object {
        $_.Class -match 'AutoCompleteTextView' -and $_.Clickable -and $_.Y1 -ge 1600
    } | Select-Object -First 1
    $detailClose = $nodes | Where-Object {
        $_.Clickable -and $_.Y1 -le 220 -and $_.Label -match '^\s*(?:Close|\u5173\u95ed|\u95dc\u9589)\s*$'
    } | Select-Object -First 1
    if ($detailComposer -and $detailClose) { return $false }

    # A web permalink can land on the target as an inline "From your messages"
    # feed card. Its timestamp is the bounded route into that same post detail.
    $timestamp = $nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Label -match '(?i)(?:Shared with:|Shared to:)' -and
        $_.Y1 -ge 180 -and $_.Y1 -le 1500
    } | Select-Object -First 1
    if (-not $timestamp) { return $false }

    Set-VerificationBoundary
    if (-not (Tap-Node $timestamp)) { return $false }
    Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds))
    Dump-Ui $Path
    Write-Host '[target-open] promoted verified inline feed card to post detail via timestamp.'
    return $true
}

function Test-PostSpecificFacebookUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }
    return [bool]($Url -match '(?i)(?:pfbid[A-Za-z0-9]+|[?&]story_fbid=|/posts/(?:[0-9]+|pfbid[A-Za-z0-9]+)|/permalink/[0-9]+|/reel/[0-9]+|/videos/[0-9]+|/watch/\?v=[0-9]{1,32}(?:[&#]|$))')
}

function Test-FacebookShareNavigationUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { return $false }
    try {
        $uri = [Uri]$Url
        if ($uri.Host -notmatch '^(?:(?:www|m)\.)?facebook\.com$') { return $false }
        return [bool]($uri.AbsolutePath -match '^/share/(?:p|r|v)/[A-Za-z0-9._-]+/?$')
    } catch {
        return $false
    }
}

function Resolve-LocalPostUrl([string]$Id) {
    if ([string]::IsNullOrWhiteSpace($Id)) { return "" }
    $candidates = @()
    $current = Join-Path $Base 'analysis-current\facebook-posts.csv'
    if (Test-Path $current) { $candidates += Get-Item $current }
    $runs = Join-Path $Base 'runs'
    if (Test-Path $runs) {
        $candidates += Get-ChildItem $runs -Filter 'facebook-posts.csv' -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    }
    foreach ($file in $candidates) {
        try {
            $row = Import-Csv $file.FullName | Where-Object { [string]$_.post_id -eq $Id -and $_.url } | Select-Object -First 1
            if ($row -and $row.url -and (Test-PostSpecificFacebookUrl ([string]$row.url))) {
                return [string]$row.url
            }
        } catch {
            continue
        }
    }
    return ""
}

function Convert-ToFacebookIntentUrl([string]$Url) {
    $value = [string]$Url
    if ([string]::IsNullOrWhiteSpace($value)) { return $value }
    try {
        $uri = [Uri]$value
        if ($uri.Host -notmatch '^(?:(?:www|m)\.)?facebook\.com$') {
            return $value
        }
        if ($uri.AbsolutePath -match '^/watch/?$') {
            $video = [regex]::Match($uri.Query, '(?:^|[?&])v=([0-9]{1,32})(?:&|$)', 'IgnoreCase')
            if ($video.Success) {
                return "https://www.facebook.com/reel/$($video.Groups[1].Value)/"
            }
            return $value
        }
        if ($uri.AbsolutePath -notmatch '^/story\.php$') {
            return $value
        }
        $story = [regex]::Match($uri.Query, '(?:^|[?&])story_fbid=(pfbid[A-Za-z0-9]+|[0-9]{1,32})(?:&|$)', 'IgnoreCase')
        $owner = [regex]::Match($uri.Query, '(?:^|[?&])id=(\d+)(?:&|$)', 'IgnoreCase')
        if ($story.Success -and $owner.Success) {
            return "https://www.facebook.com/$($owner.Groups[1].Value)/posts/$($story.Groups[1].Value)"
        }
    } catch {
        return $value
    }
    return $value
}

function Open-Url([string]$Url) {
    # A verified GraphQL prefetch is not enough if Facebook keeps an older story
    # surface on screen.  Stop only the Facebook app, keep mitmdump/proxy/login
    # state intact, then establish a fresh capture boundary for this URL launch.
    if ($KeepFacebookProcess) {
        Write-Host '[target-open] preserving pre-injected Facebook process.'
    }
    elseif (Get-Command Invoke-FacebookAdbQuiet -ErrorAction SilentlyContinue) {
        $stopped = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @(
            'shell', 'am', 'force-stop', 'com.facebook.katana'
        ) -TimeoutSeconds 10
        if (-not $stopped) { throw "Facebook force-stop failed or timed out before target URL launch" }
    }
    else {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10)) {
            throw "Facebook force-stop failed before target URL launch"
        }
    }
    if (-not $KeepFacebookProcess) { Start-Sleep -Milliseconds 500 }
    Set-VerificationBoundary
    $intentUrl = Convert-ToFacebookIntentUrl $Url
    if ($intentUrl -ne $Url) {
        Write-Host "[target-open] normalized mobile story URL for Android VIEW intent: $intentUrl"
    }
    if (Get-Command Start-FacebookUrlBounded -ErrorAction SilentlyContinue) {
        $started = Start-FacebookUrlBounded -Serial $Serial -Url $intentUrl -TimeoutSeconds 15
        if (-not $started) { throw "Facebook explicit URL intent failed or timed out" }
        "explicit Facebook URL intent started: $Url" | Set-Content -Encoding UTF8 (Join-Path $Out 'am-start.txt')
    }
    else {
        $component = 'com.facebook.katana/.IntentUriHandler'
        $flags = '0x34000000'
        $process = Start-Process -FilePath 'adb' -ArgumentList @(
            '-s', $Serial, 'shell', 'am', 'start', '-W',
            '-a', 'android.intent.action.VIEW', '-d', $intentUrl,
            '-n', $component, '-f', $flags
        ) -PassThru -WindowStyle Hidden
        if (-not $process.WaitForExit(15000)) {
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            throw "Facebook explicit URL intent timed out"
        }
        if ($process.ExitCode -ne 0) { throw "Facebook explicit URL intent failed" }
        "explicit Facebook URL intent started: $Url" | Set-Content -Encoding UTF8 (Join-Path $Out 'am-start.txt')
    }
    Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds))
}

function Open-BySearch([string]$Id) {
    if (-not $KeepFacebookProcess) {
        if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'am', 'force-stop', 'com.facebook.katana') -TimeoutSeconds 10)) {
            throw "Facebook force-stop failed before target search"
        }
    }
    else {
        Write-Host '[target-open] preserving pre-injected Facebook process for target search.'
    }
    if (-not (Start-FacebookAppBounded -Serial $Serial -TimeoutSeconds 15)) {
        throw "Facebook app launch failed during target search"
    }
    Start-Sleep -Seconds 5
    $searchHomeUi = Join-Path $Out 'search-home.xml'
    Dump-Ui $searchHomeUi
    $search = Get-UiNodes $searchHomeUi | Where-Object {
        $_.Clickable -and $_.Label -match '(?i)Search|\u641c\u7d22|\u641c\u5bfb|\u641c\u5c0b'
    } | Select-Object -First 1
    if (-not (Tap-Node $search)) { throw "Facebook search entry not found" }
    $searchUi = Join-Path $Out 'search-entry.xml'
    Dump-Ui $searchUi
    $edit = Get-UiNodes $searchUi | Where-Object { $_.Class -eq 'android.widget.EditText' } | Select-Object -First 1
    if (-not (Tap-Node $edit)) { throw "Facebook search box not found" }
    if (-not (Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'text', $Id) -TimeoutSeconds 10)) {
        throw "Facebook search text input failed"
    }
    Start-Sleep -Seconds 4
    $suggestUi = Join-Path $Out 'search-suggestion.xml'
    Dump-Ui $suggestUi
    $exact = Get-UiNodes $suggestUi | Where-Object {
        $_.Clickable -and
        $_.Label -eq $Id -and
        $_.Class -notmatch 'EditText' -and
        [string]$_.ResourceId -notmatch '(?i)search|query|input' -and
        $_.Y1 -ge 180
    } | Select-Object -First 1
    if (-not $exact) { throw "Exact post-id search suggestion not found" }
    Set-VerificationBoundary
    if (-not (Tap-Node $exact)) { throw "Exact post-id search suggestion could not be opened" }
    Start-Sleep -Seconds ([Math]::Max(3, $WaitSeconds))
}

if ([string]::IsNullOrWhiteSpace($PostUrl) -and [string]::IsNullOrWhiteSpace($PostId)) {
    Write-Diagnostic $false 'missing-target'
    throw "PostUrl or PostId is required"
}

$adbState = Get-FacebookAdbState -Serial $Serial
if ($adbState -ne 'device') { throw "ADB device is not ready: $Serial" }
if (Get-Command Ensure-FacebookProjectTransport -ErrorAction SilentlyContinue) {
    $transport = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
    Write-Host "[transport] target-open network=$($transport.active_default_network) proxy=$($transport.proxy) capture=$($transport.capture_active)"
}

try {
    if ($ResolvedUrl -and -not (Test-PostSpecificFacebookUrl $ResolvedUrl) -and -not (Test-FacebookShareNavigationUrl $ResolvedUrl)) {
        Write-Warning "[target-open] ignoring non-specific Facebook URL; falling back to exact PostId resolution: $ResolvedUrl"
        $ResolvedUrl = ""
    }
    if ([string]::IsNullOrWhiteSpace($ResolvedUrl) -and $PostId) {
        $ResolvedUrl = Resolve-LocalPostUrl $PostId
        if ($ResolvedUrl) { $Method = 'local-id-to-url' }
    }
    if ($ResolvedUrl -and (Test-FacebookShareNavigationUrl $ResolvedUrl)) {
        $shareResolver = Join-Path $Base 'resolve_facebook_share_url.py'
        if (Test-Path $shareResolver) {
            $resolveResult = Invoke-FacebookPythonCaptured `
                -PythonExe $PythonExe `
                -Arguments @($shareResolver, '--url', $ResolvedUrl, '--timeout-seconds', '15') `
                -WorkingDirectory $Base
            $canonicalUrl = ([string]$resolveResult.Stdout).Trim()
            if ($resolveResult.ExitCode -eq 0 -and (Test-PostSpecificFacebookUrl $canonicalUrl)) {
                Write-Host "[target-open] resolved Facebook share URL to canonical target: $canonicalUrl"
                $ResolvedUrl = $canonicalUrl
                $Method = 'share-url-http-redirect'
            }
            else {
                $resolverError = ([string]$resolveResult.Stderr).Trim()
                if (-not $resolverError) { $resolverError = "exit=$($resolveResult.ExitCode)" }
                Write-Warning "[target-open] share URL redirect resolution failed; using the original navigation URL: $resolverError"
            }
        }
        else {
            Write-Warning "[target-open] share URL resolver is missing; using the original navigation URL: $shareResolver"
        }
    }
    if ($ResolvedUrl) {
        $mobileTargetUrl = Convert-ToFacebookIntentUrl $ResolvedUrl
        if ($mobileTargetUrl -ne $ResolvedUrl) {
            Write-Host "[target-open] normalized Facebook video URL to supported mobile detail: $mobileTargetUrl"
            $ResolvedUrl = $mobileTargetUrl
            $Method = 'watch-url-canonical-reel'
        }
        if (-not $Method) {
            $Method = if (Test-FacebookShareNavigationUrl $ResolvedUrl) { 'share-url-navigation' } else { 'url-deeplink' }
        }
        Open-Url $ResolvedUrl
    } else {
        $Method = 'facebook-search-id'
        Open-BySearch $PostId
    }
    Dump-Ui $UiPath
    if (Open-InlineFeedCardDetail $UiPath) {
        $Method += '-timestamp-detail'
    }

    $captureWaitStarted = Get-Date
    $deadline = $captureWaitStarted.AddSeconds($VerificationTimeoutSeconds)
    while (-not (Test-Path $CaptureJsonl) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
    }
    $script:VerificationElapsedMs = [int][Math]::Round(((Get-Date) - $captureWaitStarted).TotalMilliseconds)
    if (-not (Test-Path $CaptureJsonl)) {
        Write-Diagnostic $false 'capture-jsonl-not-created'
        exit 4
    }

    $verifyDir = Join-Path $Out 'verification-analysis'
    $args = @(
        (Join-Path $Base 'analyze_facebook_capture.py'),
        '--input', $CaptureJsonl,
        '--out-dir', $verifyDir,
        '--min-line', [string]$VerificationMinLine,
        '--require-target-match'
    )
    if ($PostId) { $args += @('--expected-post-id', $PostId) }
    if ($ResolvedUrl) { $args += @('--expected-url', $ResolvedUrl) }
    $verifyStarted = Get-Date
    $verifyDeadline = $verifyStarted.AddSeconds($VerificationTimeoutSeconds)
    $verifyExit = 3
    while ($true) {
        $script:VerificationAttempts++
        $verifyProcess = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $args -WorkingDirectory $Base
        if (-not [string]::IsNullOrWhiteSpace([string]$verifyProcess.Stdout)) { Write-Host ([string]$verifyProcess.Stdout).TrimEnd() }
        if (-not [string]::IsNullOrWhiteSpace([string]$verifyProcess.Stderr)) { Write-Warning ([string]$verifyProcess.Stderr).TrimEnd() }
        $verifyExit = [int]$verifyProcess.ExitCode
        $script:VerificationElapsedMs = [int][Math]::Round(((Get-Date) - $verifyStarted).TotalMilliseconds)
        if ($verifyExit -eq 0) { break }
        # Exit 3 is the analyzer's strict "target not matched yet" result.  Keep
        # waiting only inside this bounded window; all other failures are final.
        if ($verifyExit -ne 3) {
            Write-Diagnostic $false 'target-verification-analyzer-failed' $verifyExit
            exit $verifyExit
        }
        if ((Get-Date) -ge $verifyDeadline) { break }
        Start-Sleep -Seconds 2
    }
    if ($verifyExit -ne 0) {
        Write-Diagnostic $false 'target-not-confirmed-in-capture' $verifyExit
        exit $verifyExit
    }
    $verificationReportPath = Join-Path $verifyDir 'facebook-sample-patterns.json'
    if (Test-Path $verificationReportPath) {
        try {
            $verificationReport = Get-Content -Raw -Encoding UTF8 $verificationReportPath | ConvertFrom-Json
            $targetVerification = $verificationReport.target_verification
            if ($targetVerification -and [string]$targetVerification.status -eq 'matched') {
                $matchedPostProperty = $targetVerification.PSObject.Properties['matched_post_id']
                if ($matchedPostProperty) { $script:ResolvedPostId = [string]$matchedPostProperty.Value }
                $expectedAuthorProperty = $targetVerification.PSObject.Properties['expected_url_author_id']
                if ($expectedAuthorProperty) { $script:ResolvedAuthorId = [string]$expectedAuthorProperty.Value }
                $resolvedAuthorProperty = $targetVerification.PSObject.Properties['resolved_author_id']
                if (-not $script:ResolvedAuthorId -and $resolvedAuthorProperty) { $script:ResolvedAuthorId = [string]$resolvedAuthorProperty.Value }
                $legacyStoryProperty = $targetVerification.PSObject.Properties['matched_legacy_api_story_id']
                if ($legacyStoryProperty) { $script:ResolvedLegacyStoryId = [string]$legacyStoryProperty.Value }
            }
        } catch {
            Write-Warning "[target-open] matched verification metadata could not be read: $($_.Exception.Message)"
        }
    }
    Write-Diagnostic $true 'target-confirmed-in-capture' $verifyExit
    Write-Output "Target confirmed: requested_post_id=$PostId resolved_post_id=$script:ResolvedPostId method=$Method url=$ResolvedUrl"
}
catch {
    Write-Diagnostic $false $_.Exception.Message
    throw
}
