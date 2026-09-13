[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [int]$ProxyPort = 8081,
    [switch]$OpenShareList,
    [Alias("OpenReactions")]
    [switch]$OpenReactionList,
    [switch]$OpenComments,
    [int]$WaitSeconds = 4,
    [int]$ShareScrollCount = 24,
    [int]$ReactionScrollCount = 24,
    [int]$ReactionTabLimit = 8,
    [int]$PaginationIdleLimit = 4,
    [int]$PaginationOpenIdleLimit = 8,
    [int]$ReactionStrategyIdleLimit = 2,
    [int]$ReactionOpenStrategyIdleLimit = 3,
    [int]$ReactionExtensionChunk = 6,
    [int]$ReactionAbsoluteCap = 0,
    [switch]$AutoScreenThenDeep,
    [switch]$ReactionProbeOnly,
    [int]$ReactionScreenScrollCount = 2,
    [int]$ReactionListReadyAttempts = 5,
    [int]$ReactionListReadyDelaySeconds = 2,
    [int]$ReactionProgressPollIntervalMs = 500,
    [int]$ReactionProgressPollTimeoutMs = 2200,
    [int]$ReactionLiveProgressPollIntervalMs = 150,
    [int]$ReactionLiveProgressPollTimeoutMs = 1200,
    [int]$ReactionSteadyNoGrowthPollTimeoutMs = 500,
    [int]$ReactionLongSwipeStartY = 1700,
    [int]$ReactionLongSwipeEndY = 250,
    [int]$ReactionLongSwipeDurationMs = 300,
    [int]$ReactionPipelineBurstSize = 2,
    [int]$ReactionPipelineSwipeGapMs = 150,
    [int]$ReactionNativeScrollEnabled = 1,
    [int]$ReactionNativeScrollTimeoutMs = 2500,
    [int]$ReactionNativeScrollPort = 18766,
    [int]$ReactionUiDumpEveryN = 64,
    [int]$ReactionMinCycleMs = 700,
    [int]$ReactionInitialPaginationCooldownSeconds = 4,
    [int]$ReactionRateLimitBackoffSeconds = 15,
    [int]$ReactionRateLimitMaxConsecutive = 3,
    [int]$ReactionRateLimitMaxBackoffSeconds = 300,
    [int]$ReactionNoGrowthUiDumpEveryN = 1,
    [int]$EntryScanScrollCount = 4,
    [int]$UiDumpTimeoutSeconds = 15,
    [int]$UiDumpRetryCount = 1,
    [string]$CaptureJsonl = "",
    [string]$ExpectedSourcePostId = "",
    [switch]$AllowSourceDiscovery,
    [int]$CaptureMinLine = 1,
    [string]$OutputDir = ""
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
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Out = if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    Join-Path $Base (Join-Path "readonly-exploration" $Stamp)
} elseif ([IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir
} else {
    Join-Path $Base $OutputDir
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$script:CaptureProgressLiveHits = 0
$script:CaptureProgressFallbackHits = 0

function Invoke-AdbTimedProcess([string[]]$Arguments, [int]$TimeoutSeconds) {
    $allArgs = @('-s', $Serial) + $Arguments
    $process = Start-Process -FilePath 'adb' -ArgumentList $allArgs -PassThru -WindowStyle Hidden
    $completed = $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
    if (-not $completed) {
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
        try { $process.WaitForExit() } catch {}
        return [pscustomobject]@{ Success = $false; TimedOut = $true; ExitCode = $null }
    }
    return [pscustomobject]@{ Success = ($process.ExitCode -eq 0); TimedOut = $false; ExitCode = $process.ExitCode }
}

function Dump-Ui([string]$Name) {
    $path = Join-Path $Out $Name
    $snapshot = Invoke-FacebookUiSnapshot `
        -Serial $Serial `
        -LocalPath $path `
        -RemotePath '/sdcard/codex-facebook-ui.xml' `
        -LegacyTimeoutSeconds $UiDumpTimeoutSeconds `
        -LegacyRetryCount $UiDumpRetryCount `
        -FastTimeoutSeconds ([Math]::Max(8, $UiDumpTimeoutSeconds))
    if (-not $snapshot.Success) {
        throw "UI dump failed via legacy and instrumentation fallback: $Name ($($snapshot.Error))"
    }
    if (Get-Command Get-FacebookUiBlockingState -ErrorAction SilentlyContinue) {
        $raw = [IO.File]::ReadAllText($path)
        if ((Get-FacebookUiBlockingState -Raw $raw) -eq 'system-ui-not-responding') {
            Write-Warning "[ui] System UI ANR detected during $Name; choosing Wait and backing off before one retry."
            if (-not (Dismiss-FacebookSystemUiAnr -Serial $Serial -Raw $raw)) {
                throw 'System UI ANR could not be dismissed with Wait'
            }
            Start-Sleep -Milliseconds 2500
            $snapshot = Invoke-FacebookUiSnapshot `
                -Serial $Serial `
                -LocalPath $path `
                -RemotePath '/sdcard/codex-facebook-ui.xml' `
                -LegacyTimeoutSeconds $UiDumpTimeoutSeconds `
                -LegacyRetryCount $UiDumpRetryCount `
                -FastTimeoutSeconds ([Math]::Max(8, $UiDumpTimeoutSeconds))
            if (-not $snapshot.Success) {
                throw "UI dump failed after System UI ANR Wait/backoff: $Name ($($snapshot.Error))"
            }
            $raw = [IO.File]::ReadAllText($path)
            if ((Get-FacebookUiBlockingState -Raw $raw) -eq 'system-ui-not-responding') {
                throw 'System UI ANR persisted after Wait/backoff'
            }
        }
    }
    if ($snapshot.Source -eq 'instrumentation') {
        Write-Warning "[ui] legacy dump unavailable; instrumentation fallback wrote $Name (package=$($snapshot.RootPackage) nodes=$($snapshot.NodeCount))."
    }
    return $path
}

function Get-UiNodes([string]$Path) {
    # Facebook occasionally emits malformed XML attributes. Parse opening node tags
    # directly so a single bad text value does not disable the safe entry-point scan.
    $raw = [IO.File]::ReadAllText($Path)
    foreach ($tag in [regex]::Matches($raw, '<node\b[^>]*>')) {
        $attributes = @{}
        foreach ($attribute in [regex]::Matches($tag.Value, '([A-Za-z][A-Za-z0-9_-]*)="([^"]*)"')) {
            $attributes[$attribute.Groups[1].Value] = $attribute.Groups[2].Value
        }
        $boundsMatch = [regex]::Match($attributes["bounds"], '^\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]$')
        if (-not $boundsMatch.Success) { continue }

        $text = [string]$attributes["text"]
        $contentDescription = [string]$attributes["content-desc"]
        $label = if (-not [string]::IsNullOrWhiteSpace($text)) { $text } else { $contentDescription }
        [pscustomobject]@{
            Label = $label
            ResourceId = [string]$attributes["resource-id"]
            Class = [string]$attributes["class"]
            Package = [string]$attributes["package"]
            Clickable = ([string]$attributes["clickable"] -eq "true")
            LongClickable = ([string]$attributes["long-clickable"] -eq "true")
            Scrollable = ([string]$attributes["scrollable"] -eq "true")
            Naf = ([string]$attributes["NAF"] -eq "true")
            X1 = [int]$boundsMatch.Groups[1].Value
            Y1 = [int]$boundsMatch.Groups[2].Value
            X2 = [int]$boundsMatch.Groups[3].Value
            Y2 = [int]$boundsMatch.Groups[4].Value
            Bounds = [string]$attributes["bounds"]
        }
    }
}

function Find-Node([string]$Path, [string]$Pattern, [switch]$ClickableOnly, [int]$MaxTop = 2147483647) {
    foreach ($node in Get-UiNodes $Path) {
        if ($ClickableOnly -and -not $node.Clickable) { continue }
        if ($node.Y1 -gt $MaxTop) { continue }
        if ($node.Label -match $Pattern) { return $node }
    }
    return $null
}

function Find-ReactionCountNode([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    foreach ($node in $nodes) {
        $isReelReactionCount = (
            $node.Package -eq 'com.facebook.katana' -and
            $node.Clickable -and
            $node.Class -eq 'android.widget.Button' -and
            $node.X1 -ge 900 -and $node.X2 -le 1080 -and
            $node.Y1 -ge 500 -and $node.Y2 -le 1550 -and
            ($node.Y2 - $node.Y1) -ge 30 -and ($node.Y2 - $node.Y1) -le 90 -and
            $node.Label -match '^\s*(?:[0-9][0-9,.\s]*(?:(?:[KkMm])|\u5343|\u767e|\u4e07|\u842c|\u4ebf|\u5104)?\s*(?:\u4e2a|\u500b)(?:\u5fc3\u60c5|\u53cd\u5e94|\u53cd\u61c9)|[0-9][0-9,.\s]*(?:[KkMm])?\s+reactions?)\s*$'
        )
        if ($isReelReactionCount) { return $node }
    }
    foreach ($node in $nodes) {
        if (-not $node.Clickable -or $node.Y1 -gt 260) { continue }
        if ($node.Class -match 'EditText') { continue }
        if ([string]$node.ResourceId -match '(?i)search|query|input') { continue }
        if ($node.ResourceId -match '(^|/)feed_like_count$') { return $node }
        if ($node.Label -match '^\s*[0-9][0-9,.\s]*(?:(?:[KkMm])|\u5343|\u767e|\u4e07|\u842c|\u4ebf|\u5104)?\s*$') { return $node }
    }
    return $null
}

function Find-ReactionSummaryIconNode([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    foreach ($candidate in @($nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Clickable -and $_.Naf -and
        $_.Class -eq 'android.view.ViewGroup' -and
        $_.X1 -ge 900 -and $_.X2 -le 1080 -and
        # Tall media posts can place the same validated action row just above
        # 1080 after the bounded detail reveal.
        $_.Y1 -ge 900 -and $_.Y2 -le 1750 -and
        ($_.Y2 - $_.Y1) -ge 70 -and ($_.Y2 - $_.Y1) -le 180
    })) {
        $rowMid = [int](($candidate.Y1 + $candidate.Y2) / 2)
        $sameRow = @($nodes | Where-Object {
            $_.Clickable -and $_.Y1 -le $rowMid -and $_.Y2 -ge $rowMid
        })
        $likeAction = $sameRow | Where-Object {
            $_.Class -eq 'android.widget.Button' -and
            $_.X1 -le 20 -and $_.X2 -le 280 -and
            $_.Label -match '(?i)(?:Like button|\u8d5e\u6309\u94ae|\u8b9a\u6309\u9215)'
        } | Select-Object -First 1
        $commentAction = $sameRow | Where-Object {
            $_.Class -eq 'android.widget.Button' -and
            $_.X1 -ge 150 -and $_.X1 -le 450 -and
            $_.Label -match '^\s*(?i:Comment|Comments|\u8bc4\u8bba|\u8a55\u8ad6)\s*$'
        } | Select-Object -First 1
        $shareAction = $sameRow | Where-Object {
            $_.Class -eq 'android.widget.Button' -and
            $_.X1 -ge 300 -and $_.X1 -le 650 -and
            $_.Label -match '(?i)(?:Share button|\u5206\u4eab\u6309\u94ae|\u5206\u4eab\u6309\u9215|\u5206\u4eab)'
        } | Select-Object -First 1
        if ($likeAction -and $commentAction -and $shareAction) {
            return [pscustomobject]@{
                Label = 'reaction-icons-summary'
                ResourceId = $candidate.ResourceId
                Class = $candidate.Class
                Package = $candidate.Package
                Clickable = $true
                Scrollable = $false
                Naf = $true
                X1 = $candidate.X1
                Y1 = $candidate.Y1
                X2 = $candidate.X2
                Y2 = $candidate.Y2
                Bounds = $candidate.Bounds
            }
        }
    }
    return $null
}

function Find-ShareCountNode([string]$Path) {
    foreach ($node in Get-UiNodes $Path) {
        $label = [string]$node.Label
        if ($label -match '^\s*(?:Share|Share post|\u5206\u4eab|\u5206\u4eab\u6309\u94ae|\u5206\u4eab\u6309\u9215)\s*$') { continue }
        $numericCount = $label -match '^\s*[0-9]+(?:[.,][0-9]+)?\s*(?:(?:[KkMmBb])|\u5343|\u4e07|\u842c|\u4ebf|\u5104)?\s*$'
        $labeledCount = $label -match '^\s*[0-9]+(?:[.,][0-9]+)?\s*(?:(?:[KkMmBb])|\u5343|\u4e07|\u842c|\u4ebf|\u5104)?\s*(?:shares?|reshares?|\u6b21\u5206\u4eab|\u6b21\u8f49\u767c|\u6b21\u8f6c\u53d1|\u4eba\u5206\u4eab)\s*$'
        if (($node.Clickable -and $node.ResourceId -match '(^|/)feed_share_count$' -and $numericCount) -or $labeledCount) {
            return $node
        }
    }
    return $null
}

function Find-ActionBarNode([string]$Path, [ValidateSet('comment', 'share')][string]$Action) {
    $nodes = Get-UiNodes $Path
    if ($Action -eq 'share') {
        return $nodes |
            Where-Object {
                $_.Clickable -and
                $_.X1 -ge 300 -and $_.X1 -lt 650 -and
                ($_.X2 - $_.X1) -ge 140 -and
                $_.Y1 -ge 700
            } |
            Select-Object -First 1
    }
    return $nodes |
        Where-Object {
            $_.Clickable -and
            $_.X1 -ge 140 -and $_.X1 -lt 450 -and
            ($_.X2 - $_.X1) -ge 140 -and
            $_.Y1 -ge 700
        } |
        Select-Object -First 1
}

function Find-CommentsEntryNode([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    # Prefer explicit comment labels before any geometry heuristic.  Several
    # Facebook surfaces place author/page-info affordances in positions that
    # resemble the three-button action row; those are unsafe normalizers.
    $labeled = $nodes | Where-Object {
        $_.Clickable -and $_.Label -match '^\s*(?:\u8bc4\u8bba|\u8a55\u8ad6|Comment|Comments)\s*$'
    } | Select-Object -First 1
    if ($labeled) { return $labeled }

    $commentCount = $nodes | Where-Object {
        $_.Clickable -and
        ([string]$_.ResourceId).EndsWith('/feed_comment_count') -and
        $_.Class -notmatch 'EditText'
    } | Select-Object -First 1
    if ($commentCount) { return $commentCount }

    $actionCandidates = @(
        $nodes | Where-Object {
            $_.Clickable -and
            $_.Y1 -ge 700 -and
            $_.X1 -lt 650 -and
            ($_.X2 - $_.X1) -ge 120 -and ($_.X2 - $_.X1) -le 260 -and
            ($_.Y2 - $_.Y1) -ge 70 -and ($_.Y2 - $_.Y1) -le 180
        }
    )
    $rowGroups = @($actionCandidates | Group-Object { "{0}|{1}" -f $_.Y1, $_.Y2 }) |
        Sort-Object { [int](($_.Name -split '\|')[0]) } -Descending
    foreach ($row in $rowGroups) {
        $ordered = @($row.Group | Sort-Object X1)
        if ($ordered.Count -lt 3) { continue }
        for ($i = 0; $i -le $ordered.Count - 3; $i++) {
            $first = $ordered[$i]
            $second = $ordered[$i + 1]
            $third = $ordered[$i + 2]
            if (
                $first.X1 -le 80 -and
                $second.X1 -ge 110 -and $second.X1 -lt 420 -and
                $third.X1 -ge 260 -and $third.X1 -lt 650 -and
                [Math]::Abs($second.X1 - $first.X2) -le 60 -and
                [Math]::Abs($third.X1 - $second.X2) -le 60
            ) {
                return $second
            }
        }
    }
    return Find-ActionBarNode $Path 'comment'
}

function Find-CommentsDetailEntryNode([string]$Path) {
    $nodes = @(Get-UiNodes $Path)

    # A real comment-count affordance is preferred because it navigates to the
    # dedicated comments/detail surface used by the proven Reaction-count path.
    # The bare Comment/评论 action only focuses the composer on Facebook 555 and
    # must never be treated as equivalent in a strict-target workflow.
    $commentCount = $nodes | Where-Object {
        $_.Clickable -and
        ([string]$_.ResourceId).EndsWith('/feed_comment_count') -and
        $_.Class -notmatch 'EditText|AutoCompleteTextView'
    } | Select-Object -First 1
    if ($commentCount) { return $commentCount }

    $labeledCount = $nodes | Where-Object {
        if (-not $_.Clickable -or $_.Class -match 'EditText|AutoCompleteTextView') { return $false }
        $label = ([string]$_.Label).Trim()
        if ($label -match '^(?i:Comment|Comments)$' -or $label -match '^\s*(?:\u8bc4\u8bba|\u8a55\u8ad6)\s*$') { return $false }
        return [bool](
            $label -match '(?i)\b[0-9][0-9,.\s]*\s*(?:comments?)\b' -or
            $label -match '[0-9][0-9,.\s]*(?:\u6761\u8bc4\u8bba|\u689d\u8a55\u8ad6|\u5219\u8bc4\u8bba|\u5247\u8a55\u8ad6|\u8bc4\u8bba|\u8a55\u8ad6)'
        )
    } | Select-Object -First 1
    return $labeledCount
}

function Get-FeedSurfaceState([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    $feedCount = $nodes | Where-Object {
        $rid = [string]$_.ResourceId
        $rid.EndsWith('/feed_like_count') -or
        $rid.EndsWith('/feed_comment_count') -or
        $rid.EndsWith('/feed_share_count')
    } | Select-Object -First 1
    if ($feedCount) { return 'standard-post' }

    $videoControl = $nodes | Where-Object {
        $label = ([string]$_.Label).ToLowerInvariant()
        $label -match 'pause|play|fullscreen|full screen|seek|\u6682\u505c|\u66ab\u505c|\u64ad\u653e|\u5168\u5c4f|\u5168\u87a2\u5e55|\u6ed1\u52a8\u5feb\u8fdb|\u6ed1\u52d5\u5feb\u9032'
    } | Select-Object -First 1
    if ($videoControl) { return 'video-player' }
    return 'unknown'
}

function Get-NormalizedPostSurfaceState([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    if ($nodes | Where-Object {
        $_.Label -match '(?i)\u516c\u5171\u4e3b\u9875\u4fe1\u606f|\u516c\u5171\u4e3b\u9801\u8cc7\u8a0a|Page\s+(?:information|transparency)'
    } | Select-Object -First 1) {
        return 'page-info'
    }
    if (Find-ShareCountNode $Path -or Find-ReactionCountNode $Path) {
        return 'post-summary-detail'
    }
    if ($nodes | Where-Object {
        $_.Label -match '(?i)^\s*(?:\u5199\u8bc4\u8bba|\u5beb\u8a55\u8ad6|Write\s+(?:a\s+)?comment)\.\.\.\s*$' -or
        $_.Label -match '(?i)most relevant.*comments|\u6700\u76f8\u5173.*\u8bc4\u8bba|\u6700\u76f8\u95dc.*\u8a55\u8ad6'
    } | Select-Object -First 1) {
        return 'comments-surface'
    }
    return 'unknown'
}

function Get-FeedShareMetricHotspotNode([string]$Path, $CommentEntry, [int]$OffsetAbove = 70) {
    if (-not $CommentEntry) { return $null }
    $width = [int]($CommentEntry.X2 - $CommentEntry.X1)
    $height = [int]($CommentEntry.Y2 - $CommentEntry.Y1)
    # Only use the visual hotspot when the detected comment entry really has
    # action-button geometry. This avoids treating a wide post/story container
    # as an action row and keeps the tap safely above the generic Share button.
    if ($width -lt 120 -or $width -gt 300 -or $height -lt 60 -or $height -gt 180) { return $null }
    if ($CommentEntry.X1 -lt 80 -or $CommentEntry.X1 -gt 450 -or $CommentEntry.Y1 -lt 600) { return $null }
    $nodes = @(Get-UiNodes $Path)
    if ($nodes.Count -eq 0) { return $null }
    $screenWidth = [int](($nodes | Measure-Object -Property X2 -Maximum).Maximum)
    if ($screenWidth -lt 700) { return $null }
    $x = [Math]::Max(1, $screenWidth - 75)
    $y = [Math]::Max(60, [int]$CommentEntry.Y1 - [Math]::Max(25, $OffsetAbove))
    if ($y -ge ([int]$CommentEntry.Y1 - 10)) { return $null }
    return [pscustomobject]@{
        Label = "feed-share-metric-hotspot-$OffsetAbove"
        ResourceId = '__feed_share_metric_hotspot__'
        Class = 'visual-hotspot'
        Clickable = $true
        X1 = $x - 8
        Y1 = $y - 8
        X2 = $x + 8
        Y2 = $y + 8
        Bounds = "[$($x - 8),$($y - 8)][$($x + 8),$($y + 8)]"
    }
}

function Find-FeedRightShareMetricNode([string]$Path, $CommentEntry) {
    if (-not $CommentEntry) { return $null }
    $nodes = @(Get-UiNodes $Path)
    if ($nodes.Count -eq 0) { return $null }

    $rowTop = [int]$CommentEntry.Y1
    $rowBottom = [int]$CommentEntry.Y2
    $screenWidth = [int](($nodes | Measure-Object -Property X2 -Maximum).Maximum)
    if ($screenWidth -lt 700) { return $null }

    $sameRow = @($nodes | Where-Object {
        $_.Clickable -and
        [Math]::Abs([int]$_.Y1 - $rowTop) -le 3 -and
        [Math]::Abs([int]$_.Y2 - $rowBottom) -le 3
    })
    $leftAction = $sameRow | Where-Object {
        $_.X1 -le 80 -and $_.X2 -ge 120 -and $_.X2 -le 320
    } | Select-Object -First 1
    $shareAction = $sameRow | Where-Object {
        $_.X1 -ge 300 -and $_.X1 -lt 800 -and
        $_.X2 -gt $_.X1 -and
        ([string]$_.Label -match '(?i)Share|\u5206\u4eab')
    } | Select-Object -First 1
    if (-not $leftAction -or -not $shareAction) { return $null }

    return $sameRow | Where-Object {
        [string]::IsNullOrWhiteSpace([string]$_.Label) -and
        $_.X1 -ge [Math]::Max(800, $screenWidth - 220) -and
        $_.X2 -ge ($screenWidth - 80) -and
        ($_.X2 - $_.X1) -ge 70 -and ($_.X2 - $_.X1) -le 220
    } | Sort-Object X1 -Descending | Select-Object -First 1
}

function Tap-Node($Node, [string]$Label) {
    if (-not $Node) {
        Write-Host "$Label not found; no tap performed"
        return $false
    }
    $x = [int](($Node.X1 + $Node.X2) / 2)
    $y = [int](($Node.Y1 + $Node.Y2) / 2)
    Write-Host "${Label}: $($Node.Label) [$($Node.Bounds)] -> tap $x,$y"
    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'tap', [string]$x, [string]$y)
    Start-Sleep -Seconds $WaitSeconds
    return $true
}

function Activate-ReactionEntry($Node) {
    if (-not $Node) {
        Write-Host 'reaction-count entry not found; no gesture performed'
        return $false
    }
    $x = [int](($Node.X1 + $Node.X2) / 2)
    $y = [int](($Node.Y1 + $Node.Y2) / 2)
    $isReelReactionCount = (
        $Node.Package -eq 'com.facebook.katana' -and
        $Node.Class -eq 'android.widget.Button' -and
        $Node.LongClickable -and
        $Node.X1 -ge 900 -and
        $Node.Label -match '(?:\u5fc3\u60c5|\u53cd\u5e94|\u53cd\u61c9|(?i:reactions?))'
    )
    if ($isReelReactionCount) {
        Write-Host "reaction-count entry: $($Node.Label) [$($Node.Bounds)] -> long-press $x,$y"
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$y, [string]$x, [string]$y, '1000')
    }
    else {
        Write-Host "reaction-count entry: $($Node.Label) [$($Node.Bounds)] -> tap $x,$y"
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'tap', [string]$x, [string]$y)
    }
    Start-Sleep -Seconds $WaitSeconds
    return $true
}

function Tap-First([string]$XmlPath, [string]$Pattern, [string]$Label) {
    Tap-Node (Find-Node $XmlPath $Pattern -ClickableOnly) $Label
}

function Open-PostDetailViaComments([string]$Path, [string]$Prefix, [switch]$RequireDetailEntry, $Entry = $null) {
    $commentEntry = if ($Entry) { $Entry } elseif ($RequireDetailEntry) { Find-CommentsDetailEntryNode $Path } else { Find-CommentsEntryNode $Path }
    if (-not $commentEntry) { return $null }
    if (-not (Tap-Node $commentEntry 'comments normalization entry')) { return $null }
    $normalizedPath = Dump-Ui ("{0}-normalized-post-detail.xml" -f $Prefix)
    return [pscustomobject]@{
        Path = $normalizedPath
        State = Get-NormalizedPostSurfaceState $normalizedPath
        Candidate = $commentEntry
    }
}

function Reveal-NormalizedPostSummary([string]$Path, [string]$Prefix, [int]$Attempts = 2) {
    $current = $Path
    for ($attempt = 1; $attempt -le [Math]::Max(1, $Attempts); $attempt++) {
        if (Find-ShareCountNode $current -or Find-ReactionCountNode $current -or Find-ReactionSummaryIconNode $current) {
            return [pscustomobject]@{ Path = $current; Attempts = ($attempt - 1); Revealed = $true }
        }
        # A small downward pull inside the comments sheet reveals the post
        # summary header in Facebook variants where the composer initially
        # covers it.  It does not touch any action button.
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '700', '540', '1120', '350')
        Start-Sleep -Seconds $WaitSeconds
        $current = Dump-Ui ("{0}-native-summary-reveal-{1:00}.xml" -f $Prefix, $attempt)
    }
    return [pscustomobject]@{
        Path = $current
        Attempts = [Math]::Max(1, $Attempts)
        Revealed = [bool](Find-ShareCountNode $current -or Find-ReactionCountNode $current -or Find-ReactionSummaryIconNode $current)
    }
}

function Find-EntryWithFeedScan(
    [ValidateSet('share', 'reaction')][string]$Family,
    [string]$InitialPath,
    [string]$Prefix,
    [int]$MaxScrolls = 4
) {
    $path = $InitialPath
    $scrolls = 0
    for ($i = 0; $i -le [Math]::Max(0, $MaxScrolls); $i++) {
        $pageState = Get-PageState $path
        if ($pageState -eq 'app-not-responding') {
            throw "Facebook app-not-responding during $Family entry scan; refusing to classify this as a content visibility or no-entry result."
        }
        $surface = Get-FeedSurfaceState $path
        $entry = if ($Family -eq 'share') { Find-ShareCountNode $path } else { Find-ReactionCountNode $path }
        $commentEntry = if ($surface -eq 'video-player') { $null } else { Find-CommentsEntryNode $path }
        if ($surface -eq 'video-player' -and -not $entry) {
            Write-Host "Feed scan ${Family}: native video-player surface detected; skipping geometry normalization and continuing bounded scan."
        }
        if ($entry) {
            return [pscustomobject]@{ Path = $path; Entry = $entry; CommentEntry = $commentEntry; Scrolls = $scrolls; Surface = $surface }
        }
        if ($Family -eq 'reaction' -and $commentEntry) {
            return [pscustomobject]@{ Path = $path; Entry = $null; CommentEntry = $commentEntry; Scrolls = $scrolls; Surface = $surface }
        }
        if ($Family -eq 'share' -and $commentEntry) {
            # The feed-card share count often sits immediately above the action row.
            # If the action row is visible but the metric row is clipped just above
            # the viewport, reveal it with one small downward pull before falling
            # back to the comments/detail route.
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '900', '540', '1260', '300')
            Start-Sleep -Seconds $WaitSeconds
            $revealPath = Dump-Ui ("{0}-feed-metric-reveal.xml" -f $Prefix)
            $revealedEntry = Find-ShareCountNode $revealPath
            $revealedComment = Find-CommentsEntryNode $revealPath
            if ($revealedEntry) {
                return [pscustomobject]@{ Path = $revealPath; Entry = $revealedEntry; CommentEntry = $revealedComment; Scrolls = $scrolls; Surface = (Get-FeedSurfaceState $revealPath) }
            }
            if ($revealedComment) {
                return [pscustomobject]@{ Path = $revealPath; Entry = $null; CommentEntry = $revealedComment; Scrolls = $scrolls; Surface = (Get-FeedSurfaceState $revealPath) }
            }
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1260', '540', '900', '300')
            Start-Sleep -Seconds $WaitSeconds
            $path = Dump-Ui ("{0}-feed-metric-restore.xml" -f $Prefix)
            $commentEntry = Find-CommentsEntryNode $path
            if ($commentEntry) {
                return [pscustomobject]@{ Path = $path; Entry = $null; CommentEntry = $commentEntry; Scrolls = $scrolls; Surface = (Get-FeedSurfaceState $path) }
            }
        }
        if ($i -ge $MaxScrolls) { break }
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1650', '540', '850', '500')
        Start-Sleep -Seconds $WaitSeconds
        $scrolls++
        $path = Dump-Ui ("{0}-entry-scan-{1:00}.xml" -f $Prefix, $scrolls)
    }
    return [pscustomobject]@{ Path = $path; Entry = $null; CommentEntry = $null; Scrolls = $scrolls; Surface = (Get-FeedSurfaceState $path) }
}

function Find-StrictTargetReactionEntry(
    [string]$InitialPath,
    [string]$Prefix,
    [int]$MaxReveals = 3
) {
    # The target was already proved by fresh target-open traffic. Do not use
    # the generic feed scan here: its upward swipe can cross into the next
    # story before the target post's feedback summary becomes visible.
    $path = $InitialPath
    $reveals = 0
    $groupRevealUsed = $false
    for ($attempt = 0; $attempt -le [Math]::Max(0, $MaxReveals); $attempt++) {
        $entry = Find-ReactionCountNode $path
        if (-not $entry) { $entry = Find-ReactionSummaryIconNode $path }
        $commentEntry = Find-CommentsDetailEntryNode $path
        if ($entry -or $commentEntry) {
            return [pscustomobject]@{
                Path = $path
                Entry = $entry
                CommentEntry = $commentEntry
                Scrolls = $reveals
                Surface = (Get-FeedSurfaceState $path)
            }
        }

        $progress = Get-CaptureProgress 'reaction'
        if (-not (Test-ExpectedSourceGuard $progress 'reaction' 'strict-target-entry-reveal')) {
            return [pscustomobject]@{
                Path = $path
                Entry = $null
                CommentEntry = $null
                Scrolls = $reveals
                Surface = (Get-FeedSurfaceState $path)
            }
        }
        if ($attempt -ge $MaxReveals) { break }

        $nodes = @(Get-UiNodes $path)
        $detailScroller = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Class -match 'RecyclerView' -and $_.Scrollable -and
            $_.X1 -le 20 -and $_.X2 -ge 1000 -and
            $_.Y1 -ge 120 -and $_.Y1 -le 400 -and $_.Y2 -ge 1450
        } | Select-Object -First 1
        $detailComposer = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Class -match 'AutoCompleteTextView' -and
            $_.Clickable -and $_.Y1 -ge 1600
        } | Select-Object -First 1
        $detailClose = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '^\s*(?:Close|\u5173\u95ed|\u95dc\u9589)\s*$'
        } | Select-Object -First 1
        $groupBack = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '^\s*(?:Back|\u8fd4\u56de|\u8fd4\u56de\u6309\u94ae|\u8fd4\u56de\u6309\u9215)\s*$'
        } | Select-Object -First 1
        $groupSearch = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '(?i)(?:^\s*Search\s+(?:in|this)\b|\bsearch\s+(?:in|this)\b|^\s*\u5728.+\u4e2d\u641c\u7d22\s*$)'
        } | Select-Object -First 1
        $groupStoryOptions = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -ge 250 -and $_.Y1 -le 1000 -and
            $_.Label -match '(?i)(?:\u5e16\u5b50.*\u66f4\u591a\u9009\u9879|\u8cbc\u6587.*\u66f4\u591a\u9078\u9805|post.*more\s+options|more\s+options.*post)'
        } | Select-Object -First 1
        $groupLargeImage = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Class -eq 'android.widget.ImageView' -and
            $_.Y1 -ge 600 -and ($_.Y2 - $_.Y1) -ge 500 -and
            $_.Label -match '^\s*(?:Photo|\u7167\u7247|\u76f8\u7247)\s*$'
        } | Select-Object -First 1
        $confirmedDetail = [bool]($detailScroller -and $detailComposer -and $detailClose)
        $confirmedGroupPermalink = [bool](
            $detailScroller -and $groupBack -and $groupSearch -and
            $groupStoryOptions -and $groupLargeImage -and -not $groupRevealUsed
        )
        if (-not $confirmedDetail -and -not $confirmedGroupPermalink) {
            Write-Warning 'Strict target Reaction surface is neither a confirmed post-detail nor a bounded group-permalink story; refusing an upward content swipe.'
            break
        }

        # We are inside a structurally confirmed post-detail RecyclerView. Move
        # the post content upward so the feedback summary below a large photo can
        # enter the viewport. This path is never used on a generic feed surface.
        # Equivalent gesture: shell input swipe 540 1500 540 650 550.
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1500', '540', '650', '550')
        Start-Sleep -Seconds $WaitSeconds
        $reveals++
        if ($confirmedGroupPermalink) { $groupRevealUsed = $true }
        $path = Dump-Ui ("{0}-strict-detail-reveal-{1:00}.xml" -f $Prefix, $reveals)
        $progress = Get-CaptureProgress 'reaction'
        if (-not (Test-ExpectedSourceGuard $progress 'reaction' 'strict-target-entry-reveal')) {
            break
        }
        if ($confirmedGroupPermalink) {
            # One reveal is the entire allowance on a group permalink. Accept a
            # reaction summary or its same-story comment action, otherwise stop.
            $groupEntry = Find-ReactionCountNode $path
            if (-not $groupEntry) { $groupEntry = Find-ReactionSummaryIconNode $path }
            $groupCommentEntry = Find-CommentsEntryNode $path
            if ($groupEntry -or $groupCommentEntry) {
                return [pscustomobject]@{
                    Path = $path
                    Entry = $groupEntry
                    CommentEntry = $groupCommentEntry
                    Scrolls = $reveals
                    Surface = (Get-FeedSurfaceState $path)
                }
            }
            break
        }
    }
    return [pscustomobject]@{
        Path = $path
        Entry = (Find-ReactionSummaryIconNode $path)
        CommentEntry = if ($groupRevealUsed) { Find-CommentsEntryNode $path } else { Find-CommentsDetailEntryNode $path }
        Scrolls = $reveals
        Surface = (Get-FeedSurfaceState $path)
    }
}

function Find-StrictTargetShareEntry(
    [string]$InitialPath,
    [string]$Prefix,
    [int]$MaxReveals = 3
) {
    # The target post was already proved by fresh target-open traffic. Keep all
    # Share-entry discovery bounded to a structurally recognized target surface.
    # An ordinary Share composer button is never accepted as a list entry.
    $path = $InitialPath
    $reveals = 0
    $groupRevealUsed = $false
    for ($attempt = 0; $attempt -le [Math]::Max(0, $MaxReveals); $attempt++) {
        $entry = Find-ShareCountNode $path
        if ($entry) {
            return [pscustomobject]@{
                Path = $path
                Entry = $entry
                CommentEntry = (Find-CommentsDetailEntryNode $path)
                Scrolls = $reveals
                Surface = (Get-FeedSurfaceState $path)
            }
        }

        $progress = Get-CaptureProgress 'share'
        if (-not (Test-ExpectedSourceGuard $progress 'share' 'strict-target-share-entry-reveal')) {
            return [pscustomobject]@{
                Path = $path
                Entry = $null
                CommentEntry = $null
                Scrolls = $reveals
                Surface = (Get-FeedSurfaceState $path)
            }
        }
        if ($attempt -ge $MaxReveals) { break }

        $nodes = @(Get-UiNodes $path)
        $detailScroller = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Class -match 'RecyclerView' -and $_.Scrollable -and
            $_.X1 -le 20 -and $_.X2 -ge 1000 -and
            $_.Y1 -ge 120 -and $_.Y1 -le 400 -and $_.Y2 -ge 1450
        } | Select-Object -First 1
        $detailComposer = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Class -match 'AutoCompleteTextView' -and
            $_.Clickable -and $_.Y1 -ge 1600
        } | Select-Object -First 1
        $detailClose = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '^\s*(?:Close|\u5173\u95ed|\u95dc\u9589)\s*$'
        } | Select-Object -First 1
        $groupBack = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '^\s*(?:Back|\u8fd4\u56de|\u8fd4\u56de\u6309\u94ae|\u8fd4\u56de\u6309\u9215)\s*$'
        } | Select-Object -First 1
        $groupSearch = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -le 220 -and
            $_.Label -match '(?i)(?:^\s*Search\s+(?:in|this)\b|\bsearch\s+(?:in|this)\b|^\s*\u5728.+\u4e2d\u641c\u7d22\s*$)'
        } | Select-Object -First 1
        $groupStoryOptions = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Y1 -ge 250 -and $_.Y1 -le 1000 -and
            $_.Label -match '(?i)(?:\u5e16\u5b50.*\u66f4\u591a\u9009\u9879|\u8cbc\u6587.*\u66f4\u591a\u9078\u9805|post.*more\s+options|more\s+options.*post)'
        } | Select-Object -First 1
        $groupLargeImage = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and $_.Clickable -and
            $_.Class -eq 'android.widget.ImageView' -and
            $_.Y1 -ge 600 -and ($_.Y2 - $_.Y1) -ge 500 -and
            $_.Label -match '^\s*(?:Photo|\u7167\u7247|\u76f8\u7247)\s*$'
        } | Select-Object -First 1

        $confirmedDetail = [bool]($detailScroller -and $detailComposer -and $detailClose)
        $confirmedGroupPermalink = [bool](
            $detailScroller -and $groupBack -and $groupSearch -and
            $groupStoryOptions -and $groupLargeImage -and -not $groupRevealUsed
        )
        if (-not $confirmedDetail -and -not $confirmedGroupPermalink) {
            Write-Warning 'Strict target Share surface is neither a confirmed post-detail nor a bounded group-permalink story; refusing an upward content swipe.'
            break
        }

        # Equivalent gesture: shell input swipe 540 1500 540 650 550.
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1500', '540', '650', '550')
        Start-Sleep -Seconds $WaitSeconds
        $reveals++
        if ($confirmedGroupPermalink) { $groupRevealUsed = $true }
        $path = Dump-Ui ("{0}-strict-detail-reveal-{1:00}.xml" -f $Prefix, $reveals)
        $progress = Get-CaptureProgress 'share'
        if (-not (Test-ExpectedSourceGuard $progress 'share' 'strict-target-share-entry-reveal')) {
            break
        }
        if ($confirmedGroupPermalink) {
            # A group permalink can cross into the next story after more vertical
            # movement. One reveal is the entire allowance: accept only a real
            # Share-count node now, otherwise stop conservatively.
            $groupEntry = Find-ShareCountNode $path
            if ($groupEntry) {
                return [pscustomobject]@{
                    Path = $path
                    Entry = $groupEntry
                    CommentEntry = (Find-CommentsDetailEntryNode $path)
                    Scrolls = $reveals
                    Surface = (Get-FeedSurfaceState $path)
                }
            }
            break
        }
    }
    return [pscustomobject]@{
        Path = $path
        Entry = (Find-ShareCountNode $path)
        CommentEntry = (Find-CommentsDetailEntryNode $path)
        Scrolls = $reveals
        Surface = (Get-FeedSurfaceState $path)
    }
}

function Scroll-And-Dump([string]$Prefix, [int]$Count, [int]$StartY = 1600, [int]$EndY = 500) {
    for ($i = 0; $i -lt [Math]::Max(0, $Count); $i++) {
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', [string]$StartY, '540', [string]$EndY, '600')
        Start-Sleep -Seconds $WaitSeconds
        Dump-Ui ("{0}-{1:00}.xml" -f $Prefix, ($i + 1)) | Out-Null
    }
}

function Get-LiveCaptureProgress([ValidateSet('share', 'reaction')][string]$Family) {
    if ([string]::IsNullOrWhiteSpace($CaptureJsonl)) { return $null }
    $runDir = Split-Path -Parent $CaptureJsonl
    if ([string]::IsNullOrWhiteSpace($runDir)) { return $null }
    $path = Join-Path $runDir 'facebook-live-progress.json'
    if (-not (Test-Path $path)) { return $null }
    try { $live = Get-Content -Raw -Encoding UTF8 $path | ConvertFrom-Json } catch { return $null }
    if (-not $live -or -not $live.families) { return $null }
    $familyProperty = $live.families.PSObject.Properties[$Family]
    if (-not $familyProperty) { return $null }
    $familyState = $familyProperty.Value
    if (-not $familyState -or -not $familyState.sources) { return $null }
    $minLine = [Math]::Max(1, $CaptureMinLine)

    $untracked = $familyState.untracked
    if ($untracked -and [int]$untracked.latest_line -ge $minLine) {
        return $null
    }

    $eligible = @()
    foreach ($property in @($familyState.sources.PSObject.Properties)) {
        $source = $property.Value
        if (-not $source) { continue }
        $latestLine = [int]$source.latest_line
        if ($latestLine -lt $minLine) { continue }
        # Mixed pre/post-boundary counters are not safe to subtract from the
        # live snapshot. Fall back to exact JSONL parsing instead of guessing.
        if ([int]$source.first_line -lt $minLine) { return $null }
        $eligible += [pscustomobject]@{ source_id = [string]$property.Name; state = $source }
    }
    if ($eligible.Count -eq 0) { return $null }

    $observed = @($eligible | ForEach-Object { [string]$_.source_id } | Select-Object -Unique)
    $selected = $null
    if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
        $selected = $eligible | Where-Object { [string]$_.source_id -eq [string]$ExpectedSourcePostId } | Select-Object -First 1
    }
    if (-not $selected) {
        $selected = $eligible | Sort-Object { [int]$_.state.latest_line } -Descending | Select-Object -First 1
    }
    if (-not $selected) { return $null }

    $source = $selected.state
    $sessionState = $live.session
    $restrictionLine = if ($sessionState -and $sessionState.PSObject.Properties['latest_temporary_feature_block_line']) { [int]$sessionState.latest_temporary_feature_block_line } else { 0 }
    $restrictionFriendlyName = if ($sessionState -and $sessionState.PSObject.Properties['latest_temporary_feature_block_friendly_name']) { [string]$sessionState.latest_temporary_feature_block_friendly_name } else { '' }
    $restrictionTargetsFamily = if ($Family -eq 'reaction') {
        $restrictionFriendlyName -match '^(?:FeedbackReactorsGraphService|FbShortsIGMediaReactorsQuery)'
    }
    else {
        $restrictionFriendlyName -match '^ResharesOfContentFeedQuery'
    }
    # Until this family returns a page, any run-wide feature block remains a
    # conservative stop signal. A successful page proves the family is usable,
    # so unrelated background-query blocks no longer abort its pagination.
    $familyPageSucceeded = [bool]([int]$source.page_count -gt 0)
    $freshSessionRestriction = [bool]($restrictionLine -gt 0 -and (-not $familyPageSucceeded -or $restrictionTargetsFamily))
    $result = [ordered]@{
        family = $Family
        source_post_id = [string]$selected.source_id
        observed_source_post_ids = $observed
        query_records = [int]$source.query_records
        user_count = [int]$source.user_count
        first_line = [int]$source.first_line
        latest_line = [int]$source.latest_line
        page_count = [int]$source.page_count
        terminal_page_seen = [bool]$source.terminal_page_seen
        open_page_seen = [bool]$source.open_page_seen
        rate_limit_count = if ($source.PSObject.Properties['rate_limit_count']) { [int]$source.rate_limit_count } else { 0 }
        latest_rate_limited = if ($source.PSObject.Properties['latest_rate_limited']) { [bool]$source.latest_rate_limited } else { $false }
        latest_graphql_error_code = if ($source.PSObject.Properties['latest_graphql_error_code']) { $source.latest_graphql_error_code } else { $null }
        session_restricted = $freshSessionRestriction
        session_restriction_line = if ($freshSessionRestriction) { $restrictionLine } else { 0 }
        session_restriction_graphql_code = if ($freshSessionRestriction -and $sessionState.PSObject.Properties['latest_temporary_feature_block_graphql_code']) { $sessionState.latest_temporary_feature_block_graphql_code } else { $null }
        session_restriction_api_error_code = if ($freshSessionRestriction -and $sessionState.PSObject.Properties['latest_temporary_feature_block_api_code']) { $sessionState.latest_temporary_feature_block_api_code } else { $null }
        session_restriction_friendly_name = if ($freshSessionRestriction) { $restrictionFriendlyName } else { $null }
        parse_errors = 0
        progress_source = 'live'
    }
    if ($Family -eq 'reaction') {
        $result['latest_group_key'] = [string]$source.latest_group_key
        $result['groups'] = $source.groups
    }
    return [pscustomobject]$result
}

function Get-CaptureProgress([ValidateSet('share', 'reaction')][string]$Family) {
    if ([string]::IsNullOrWhiteSpace($CaptureJsonl) -or -not (Test-Path $CaptureJsonl)) { return $null }
    $live = Get-LiveCaptureProgress $Family
    if ($live) {
        $script:CaptureProgressLiveHits++
        return $live
    }
    $script:CaptureProgressFallbackHits++
    $helper = Join-Path $Base 'facebook_capture_progress.py'
    if (-not (Test-Path $helper)) { return $null }
    $progressArgs = @($helper, '--input', $CaptureJsonl, '--family', $Family, '--min-line', [string]([Math]::Max(1, $CaptureMinLine)))
    $progressProcess = Invoke-FacebookPythonCaptured -PythonExe $PythonExe -Arguments $progressArgs -WorkingDirectory $Base
    $raw = ([string]$progressProcess.Stdout).Trim()
    if ($progressProcess.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) { return $null }
    try { return $raw | ConvertFrom-Json } catch { return $null }
}

function Get-ReactionGroup($Progress, [string]$ReactionKey) {
    if (-not $Progress -or [string]::IsNullOrWhiteSpace($ReactionKey) -or -not $Progress.groups) { return $null }
    $property = $Progress.groups.PSObject.Properties[$ReactionKey]
    if ($property) { return $property.Value }
    return $null
}

function Get-SafeBoolProperty($Object, [string]$Name, [bool]$Default = $false) {
    if (-not $Object) { return $Default }
    if ($Object -is [System.Collections.IDictionary]) {
        if (-not $Object.Contains($Name)) { return $Default }
        return [bool]$Object[$Name]
    }
    $property = $Object.PSObject.Properties[$Name]
    if (-not $property) { return $Default }
    return [bool]$property.Value
}

function Get-ReactionRateLimitLine($Progress) {
    if (-not $Progress) { return 0 }
    $property = $Progress.PSObject.Properties['latest_rate_limit_line']
    if ($property) { return [int]$property.Value }
    if ($Progress.PSObject.Properties['latest_line']) { return [int]$Progress.latest_line }
    return 0
}

function Get-ProgressMetric($Progress, [string]$Family, [string]$ReactionKey = '') {
    if (-not $Progress) { return '' }
    if ($Family -eq 'reaction' -and -not [string]::IsNullOrWhiteSpace($ReactionKey)) {
        $group = Get-ReactionGroup $Progress $ReactionKey
        if ($group) {
            return '{0}|{1}|{2}|{3}' -f $group.query_records, $group.page_count, $group.user_count, $group.latest_line
        }
    }
    return '{0}|{1}|{2}|{3}' -f $Progress.query_records, $Progress.page_count, $Progress.user_count, $Progress.latest_line
}

function Get-EffectiveIdleLimit($Progress, [string]$Family, [string]$ReactionKey = '') {
    $limit = [Math]::Max(1, $PaginationIdleLimit)
    if ($Family -eq 'share') {
        if ($Progress -and $Progress.open_page_seen -eq $true) {
            return [Math]::Max($limit, $PaginationOpenIdleLimit)
        }
        return $limit
    }
    if ($Family -eq 'reaction') {
        $group = Get-ReactionGroup $Progress $ReactionKey
        if ($group -and $group.open_page_seen -eq $true) {
            return [Math]::Max($limit, $PaginationOpenIdleLimit)
        }
    }
    return $limit
}

function Scroll-ShareAdaptive([string]$Prefix, [int]$Count) {
    $current = Get-CaptureProgress 'share'
    if (-not $current) {
        Scroll-And-Dump $Prefix $Count
        return
    }
    if (-not (Test-ExpectedSourceGuard $current 'share' 'share-pagination-start')) {
        Write-Host 'Adaptive share pagination stopped: target source mismatch.'
        return
    }
    $idle = 0
    $previousMetric = Get-ProgressMetric $current 'share'
    for ($i = 0; $i -lt [Math]::Max(0, $Count); $i++) {
        if ($current.terminal_page_seen -eq $true) {
            Write-Host 'Adaptive share pagination stopped: terminal page already seen.'
            break
        }
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1600', '540', '500', '600')
        Start-Sleep -Seconds $WaitSeconds
        Dump-Ui ("{0}-{1:00}.xml" -f $Prefix, ($i + 1)) | Out-Null
        $next = Get-CaptureProgress 'share'
        if (-not $next) { continue }
        if (-not (Test-ExpectedSourceGuard $next 'share' 'share-pagination')) {
            Write-Host 'Adaptive share pagination stopped: target source mismatch.'
            break
        }
        $metric = Get-ProgressMetric $next 'share'
        if ($metric -eq $previousMetric) { $idle++ } else { $idle = 0 }
        $current = $next
        $previousMetric = $metric
        if ($current.terminal_page_seen -eq $true) {
            Write-Host "Adaptive share pagination stopped: terminal page reached after $($i + 1) scroll(s)."
            break
        }
        $idleLimit = Get-EffectiveIdleLimit $current 'share'
        if ($idle -ge $idleLimit) {
            Write-Host "Adaptive share pagination stopped: no capture growth for $idle consecutive scroll(s)."
            break
        }
    }
}

function Invoke-ReactionTriggerStrategy([string]$Strategy, [string]$GuardResponse = '') {
    $guardBounds = [regex]::Match($GuardResponse, '^OK REACTION ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+)$')
    if ($guardBounds.Success) {
        $left = [int]$guardBounds.Groups[1].Value
        $top = [int]$guardBounds.Groups[2].Value
        $right = [int]$guardBounds.Groups[3].Value
        $bottom = [int]$guardBounds.Groups[4].Value
        $x = [int][Math]::Round(($left + $right) / 2.0)
        $safeTop = [Math]::Min($bottom - 140, $top + 120)
        $safeBottom = [Math]::Max($safeTop + 140, $bottom - 120)
        $middle = [int][Math]::Round(($safeTop + $safeBottom) / 2.0)
        switch ($Strategy) {
            'long' { $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$safeBottom, [string]$x, [string]$safeTop, [string]$ReactionLongSwipeDurationMs) }
            'medium' { $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$safeBottom, [string]$x, [string]$middle, '450') }
            'short' { $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$safeBottom, [string]$x, [string]([Math]::Max($safeTop, $safeBottom - 420)), '300') }
            'rebound' {
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$middle, [string]$x, [string]$safeBottom, '250')
                Start-Sleep -Seconds 1
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$safeBottom, [string]$x, [string]$safeTop, '500')
            }
            'wait-long' {
                Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds))
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', [string]$x, [string]$safeBottom, [string]$x, [string]$safeTop, [string]$ReactionLongSwipeDurationMs)
            }
            default { throw "Unknown reaction trigger strategy: $Strategy" }
        }
        return
    }
    switch ($Strategy) {
        'long' {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', [string]$ReactionLongSwipeStartY, '540', [string]$ReactionLongSwipeEndY, [string]$ReactionLongSwipeDurationMs)
        }
        'medium' {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1600', '540', '800', '450')
        }
        'short' {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1550', '540', '1120', '300')
        }
        'rebound' {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '850', '540', '1200', '250')
            Start-Sleep -Seconds 1
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1600', '540', '600', '500')
        }
        'wait-long' {
            Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds))
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', [string]$ReactionLongSwipeStartY, '540', [string]$ReactionLongSwipeEndY, [string]$ReactionLongSwipeDurationMs)
        }
        default {
            throw "Unknown reaction trigger strategy: $Strategy"
        }
    }
}

function Wait-ReactionMinCycle([datetime]$Started, [int]$MinCycleMs) {
    if ($MinCycleMs -le 0) { return }
    $elapsedMs = [int][Math]::Round(((Get-Date) - $Started).TotalMilliseconds)
    $remainingMs = $MinCycleMs - $elapsedMs
    if ($remainingMs -gt 0) {
        Start-Sleep -Milliseconds $remainingMs
    }
}

function Scroll-ReactionAdaptive([string]$Prefix, [int]$Count, $BaselineProgress, [string]$PreferredReactionKey = '') {
    $paginationStarted = Get-Date
    $baseBudget = [Math]::Max(0, $Count)
    $extensionChunk = [Math]::Max(1, $ReactionExtensionChunk)
    $unlimited = ($ReactionAbsoluteCap -le 0)
    $absoluteCap = if ($unlimited) { 0 } else { [Math]::Max($baseBudget, $ReactionAbsoluteCap) }
    $currentBudget = if ($unlimited) { $baseBudget } else { [Math]::Min($baseBudget, $absoluteCap) }
    $pollIntervalMs = [Math]::Max(100, $ReactionProgressPollIntervalMs)
    $pollTimeoutMs = [Math]::Max($pollIntervalMs, $ReactionProgressPollTimeoutMs)
    $livePollIntervalMs = [Math]::Max(50, $ReactionLiveProgressPollIntervalMs)
    $livePollTimeoutMs = [Math]::Max($livePollIntervalMs, $ReactionLiveProgressPollTimeoutMs)
    $steadyNoGrowthPollTimeoutMs = [Math]::Max($livePollIntervalMs, $ReactionSteadyNoGrowthPollTimeoutMs)
    $pipelineBurstSize = [Math]::Max(1, $ReactionPipelineBurstSize)
    $pipelineSwipeGapMs = [Math]::Max(0, $ReactionPipelineSwipeGapMs)
    $pipelineEnabled = ($pipelineBurstSize -gt 1)
    $nativeScrollEnabled = [bool]($ReactionNativeScrollEnabled -ne 0 -and (Get-Command Invoke-FacebookNativeReactionScroll -ErrorAction SilentlyContinue))
    $nativeScrollAvailable = $nativeScrollEnabled
    $nativeScrollTimeoutMs = [Math]::Max(500, $ReactionNativeScrollTimeoutMs)
    $uiDumpEveryN = if ($ReactionUiDumpEveryN -le 0) { 1 } else { $ReactionUiDumpEveryN }
    $minCycleMs = [Math]::Max(0, $ReactionMinCycleMs)
    $initialPaginationCooldownSeconds = [Math]::Max(0, $ReactionInitialPaginationCooldownSeconds)
    $rateLimitBaseBackoffSeconds = [Math]::Max(1, $ReactionRateLimitBackoffSeconds)
    $rateLimitMaxConsecutive = [Math]::Max(1, $ReactionRateLimitMaxConsecutive)
    $rateLimitMaxBackoffSeconds = [Math]::Max($rateLimitBaseBackoffSeconds, $ReactionRateLimitMaxBackoffSeconds)
    $noGrowthUiDumpEveryN = if ($ReactionNoGrowthUiDumpEveryN -le 0) { 1 } else { $ReactionNoGrowthUiDumpEveryN }
    # A visible bottom loader is explicit backpressure from Facebook's RecyclerView.
    # Do not keep swiping while it is already fetching the next connection page.
    $loaderBackpressureTimeoutMs = [Math]::Max($livePollTimeoutMs, [Math]::Min(8000, ($pollTimeoutMs * 3)))
    $budgetExtensions = 0
    $progressPollCount = 0
    $fastPollGrowthHits = 0
    $uiDumpCount = 0
    $loaderBackpressureCycles = 0
    $loaderBackpressurePolls = 0
    $loaderBackpressureGrowthHits = 0
    # Loader stalls are bounded by loaderBackpressureTimeoutMs; no extra swipe is issued during that wait.
    $livePollCycles = 0
    $fallbackPollCycles = 0
    $steadyShortPollCycles = 0
    $strategyResetsToLong = 0
    $burstCount = 0
    $burstSwipes = 0
    $burstGrowthHits = 0
    $noGrowthBursts = 0
    $nativeScrollAttempts = 0
    $nativeScrollSuccess = 0
    $nativeScrollFallbacks = 0
    $nativeScrollSwipes = 0
    $nativeScrollPartialBursts = 0
    $reactionListLosses = 0
    $reactionListRecoveries = 0
    $consecutiveRateLimits = 0
    $maxConsecutiveRateLimitsSeen = 0
    $current = Get-CaptureProgress 'reaction'
    if (-not $current) {
        Scroll-And-Dump $Prefix $Count 1600 500
        return [pscustomobject]@{
            reaction_key = ''
            stop_reason = 'legacy-fixed-scroll'
            attempts = $baseBudget
            growth_events = 0
            last_strategy = 'legacy-long'
            terminal_seen = $false
            open_seen = $false
            strategy_attempts = [ordered]@{}
            strategy_growth = [ordered]@{}
            base_budget = $baseBudget
            final_budget = $currentBudget
            extension_chunk = $extensionChunk
            budget_extensions = 0
            absolute_cap = $absoluteCap
            unlimited = $unlimited
            last_growth_attempt = 0
        }
    }
    if (-not (Test-ExpectedSourceGuard $current 'reaction' 'reaction-pagination-start')) {
        return [pscustomobject]@{
            reaction_key = ''
            stop_reason = 'target-mismatch'
            attempts = 0
            growth_events = 0
            last_strategy = ''
            terminal_seen = $false
            open_seen = $false
            strategy_attempts = [ordered]@{}
            strategy_growth = [ordered]@{}
            base_budget = $baseBudget
            final_budget = 0
            extension_chunk = $extensionChunk
            budget_extensions = 0
            absolute_cap = $absoluteCap
            unlimited = $unlimited
            last_growth_attempt = 0
        }
    }
    $baselineLine = if ($BaselineProgress) { [int]$BaselineProgress.latest_line } else { 0 }
    $reactionKey = ''
    if (-not [string]::IsNullOrWhiteSpace($PreferredReactionKey) -and (Get-ReactionGroup $current $PreferredReactionKey)) {
        $reactionKey = $PreferredReactionKey
    }
    elseif ([int]$current.latest_line -gt $baselineLine -and $current.latest_group_key) {
        $reactionKey = [string]$current.latest_group_key
    }
    $initialPaginationCooldownApplied = 0
    $sessionRestrictedNow = [bool]($current.PSObject.Properties['session_restricted'] -and $current.session_restricted -eq $true)
    if (-not $sessionRestrictedNow -and $PreferredReactionKey -eq '__ALL__' -and $reactionKey -eq '__ALL__' -and $initialPaginationCooldownSeconds -gt 0) {
        $allInitialGroup = Get-ReactionGroup $current '__ALL__'
        if ($allInitialGroup -and $allInitialGroup.open_page_seen -eq $true -and $allInitialGroup.terminal_page_seen -ne $true) {
            Write-Host "Reaction ALL-first initial pagination cooldown: waiting $initialPaginationCooldownSeconds second(s) before requesting page 2."
            Start-Sleep -Seconds $initialPaginationCooldownSeconds
            $initialPaginationCooldownApplied = $initialPaginationCooldownSeconds
            $refreshedAfterInitialCooldown = Get-CaptureProgress 'reaction'
            if ($refreshedAfterInitialCooldown) {
                if (-not (Test-ExpectedSourceGuard $refreshedAfterInitialCooldown 'reaction' 'reaction-initial-pagination-cooldown')) {
                    $current = $refreshedAfterInitialCooldown
                }
                else {
                    $current = $refreshedAfterInitialCooldown
                }
            }
        }
    }
    $strategies = @('long', 'medium', 'short', 'rebound', 'wait-long')
    $strategyIndex = 0
    $strategyIdle = 0
    $attempts = 0
    $growthEvents = 0
    $lastGrowthAttempt = 0
    $strategyAttempts = [ordered]@{}
    $strategyGrowth = [ordered]@{}
    foreach ($strategyName in $strategies) {
        $strategyAttempts[$strategyName] = 0
        $strategyGrowth[$strategyName] = 0
    }
    $lastStrategy = ''
    $stopReason = 'base-budget-complete'
    # Growth decisions follow the whole reactor request stream because Facebook can
    # change reaction_id between later pagination requests after a tab tap.
    $previousMetric = Get-ProgressMetric $current 'reaction'
    $trackedLatestLine = [int]$current.latest_line
    $lastHandledRateLimitLine = 0
    while ($unlimited -or $attempts -lt $absoluteCap) {
        if ($current.PSObject.Properties['session_restricted'] -and $current.session_restricted -eq $true) {
            $stopReason = 'session-restricted'
            Write-Warning "Facebook session is temporarily feature-restricted (graphql_code=$($current.session_restriction_graphql_code), api_error_code=$($current.session_restriction_api_error_code), line=$($current.session_restriction_line)); stopping Reaction deep pagination without another trigger."
            break
        }
        $currentRateLimitLine = Get-ReactionRateLimitLine $current
        if ($current.PSObject.Properties['latest_rate_limited'] -and $current.latest_rate_limited -eq $true -and $currentRateLimitLine -gt $lastHandledRateLimitLine) {
            $lastHandledRateLimitLine = $currentRateLimitLine
            $consecutiveRateLimits++
            $maxConsecutiveRateLimitsSeen = [Math]::Max($maxConsecutiveRateLimitsSeen, $consecutiveRateLimits)
            if ($consecutiveRateLimits -ge $rateLimitMaxConsecutive) {
                $stopReason = 'rate-limited'
                Write-Warning "Reaction GraphQL rate limit circuit breaker opened after $consecutiveRateLimits consecutive event(s) (code=$($current.latest_graphql_error_code), line=$lastHandledRateLimitLine); preserving cursor checkpoint and stopping this Reaction run."
                break
            }
            $backoffMultiplier = [Math]::Pow(2, [Math]::Max(0, $consecutiveRateLimits - 1))
            $backoffSeconds = [Math]::Min($rateLimitMaxBackoffSeconds, [int][Math]::Ceiling($rateLimitBaseBackoffSeconds * $backoffMultiplier))
            Write-Warning "Reaction GraphQL rate limit observed (code=$($current.latest_graphql_error_code), line=$lastHandledRateLimitLine, consecutive=$consecutiveRateLimits/$rateLimitMaxConsecutive); preserving cursor checkpoint and cooling down for $backoffSeconds second(s)."
            Start-Sleep -Seconds $backoffSeconds
            $refreshedAfterBackoff = Get-CaptureProgress 'reaction'
            if ($refreshedAfterBackoff) { $current = $refreshedAfterBackoff }
        }
        $group = Get-ReactionGroup $current $reactionKey
        if ($group -and $group.terminal_page_seen -eq $true) {
            Write-Host "Adaptive reaction pagination stopped: terminal page already seen for $reactionKey."
            $stopReason = 'terminal'
            break
        }

        if ($attempts -ge $currentBudget) {
            $isOpen = [bool]($group -and $group.open_page_seen -eq $true)
            $recentGrowth = ($lastGrowthAttempt -gt 0 -and ($attempts - $lastGrowthAttempt) -lt $extensionChunk)
            $recoveryStrategyPending = ($strategyIndex -lt $strategies.Count)
            if (-not $unlimited -and $currentBudget -ge $absoluteCap) {
                $stopReason = 'absolute-cap'
                Write-Host "Adaptive reaction pagination stopped: absolute cap $absoluteCap reached for $reactionKey."
                break
            }
            if ($isOpen -and ($recentGrowth -or $recoveryStrategyPending)) {
                $nextBudget = if ($unlimited) {
                    $currentBudget + $extensionChunk
                }
                else {
                    [Math]::Min($absoluteCap, $currentBudget + $extensionChunk)
                }
                if ($nextBudget -gt $currentBudget) {
                    $currentBudget = $nextBudget
                    $budgetExtensions++
                    $extensionReason = if ($recentGrowth) { 'recent-growth' } else { 'pending-recovery-strategy' }
                    Write-Host "Reaction pagination budget extended to $currentBudget (extension $budgetExtensions, reason=$extensionReason) for open key=$reactionKey."
                }
            }
            elseif ($isOpen) {
                $stopReason = 'open-no-recent-growth'
                Write-Host "Adaptive reaction pagination stopped: open key=$reactionKey had no recent growth at budget $currentBudget."
                break
            }
            else {
                $stopReason = 'base-budget-complete'
                Write-Host "Adaptive reaction pagination stopped: budget $currentBudget completed without a known-open group."
                break
            }
        }

        if ($strategyIndex -ge $strategies.Count) {
            $stopReason = 'ui-trigger-exhausted'
            Write-Host "Adaptive reaction pagination stopped: all UI trigger strategies exhausted for $reactionKey."
            break
        }

        $strategy = $strategies[$strategyIndex]
        $lastStrategy = $strategy
        $cycleStarted = Get-Date
        $usePipelineBurst = [bool](
            $pipelineEnabled -and
            $strategy -eq 'long' -and
            $current -and
            $current.PSObject.Properties['progress_source'] -and
            [string]$current.progress_source -eq 'live'
        )
        $swipesThisCycle = 1
        if ($usePipelineBurst) {
            $remainingBudget = [Math]::Max(1, $currentBudget - $attempts)
            $swipesThisCycle = [Math]::Min($pipelineBurstSize, $remainingBudget)
            if (-not $unlimited) {
                $remainingCap = [Math]::Max(1, $absoluteCap - $attempts)
                $swipesThisCycle = [Math]::Min($swipesThisCycle, $remainingCap)
            }
            $burstCount++
        }
        $nativeGuardFailed = $false
        $nativeResult = $null
        for ($burstSwipe = 0; $burstSwipe -lt $swipesThisCycle; $burstSwipe++) {
            if ($nativeScrollAvailable) {
                $nativeScrollAttempts++
                $nativeResult = Invoke-FacebookNativeReactionCheck -Serial $Serial -Port $ReactionNativeScrollPort -TimeoutMs $nativeScrollTimeoutMs
                if (-not $nativeResult.Success) {
                    $nativeGuardFailed = $true
                    break
                }
                $nativeScrollSuccess++
                $nativeScrollSwipes++
            }
            if ($nativeScrollAvailable) {
                Invoke-ReactionTriggerStrategy $strategy ([string]$nativeResult.Response)
            }
            else {
                Invoke-ReactionTriggerStrategy $strategy
            }
            if ($usePipelineBurst -and $burstSwipe -lt ($swipesThisCycle - 1) -and $pipelineSwipeGapMs -gt 0) {
                Start-Sleep -Milliseconds $pipelineSwipeGapMs
            }
            $attempts++
            $strategyAttempts[$strategy] = [int]$strategyAttempts[$strategy] + 1
            if ($usePipelineBurst) { $burstSwipes++ }
        }
        if ($nativeGuardFailed) {
            if ([string]$nativeResult.Response -match '^ERR reaction-title-not-confirmed') {
                $nativeLossPath = Dump-Ui ("{0}-{1:00}-{2}-native-loss.xml" -f $Prefix, $attempts, $strategy)
                $uiDumpCount++
                $nativeLossState = if ($nativeLossPath) { Get-PageState $nativeLossPath } else { '' }
                if ($nativeLossState -notin @('reaction-list', 'reaction-list-loading')) {
                    $reactionListLosses++
                    Write-Warning "Native Reaction guard detected that pagination left the list (state=$nativeLossState, attempt=$attempts); trying one guarded Back recovery."
                    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', 'KEYCODE_BACK')
                    Start-Sleep -Seconds 1
                    $nativeRecoveryPath = Dump-Ui ("{0}-{1:00}-{2}-native-recover.xml" -f $Prefix, $attempts, $strategy)
                    $uiDumpCount++
                    $nativeRecoveryState = if ($nativeRecoveryPath) { Get-PageState $nativeRecoveryPath } else { '' }
                    if ($nativeRecoveryState -in @('reaction-list', 'reaction-list-loading')) {
                        $reactionListRecoveries++
                        $strategyIndex = 0
                        $strategyIdle = 0
                        Write-Host "Reaction list recovered after the native title guard; resuming from the existing capture cursor."
                        Wait-ReactionMinCycle $cycleStarted $minCycleMs
                        continue
                    }
                }
            }
            $nativeScrollFallbacks++
            $stopReason = 'native-scroll-unavailable'
            Write-Warning "Native Reaction guard unavailable; stopping before any unguarded ADB fallback. response=$([string]$nativeResult.Response) error=$([string]$nativeResult.Error)"
            break
        }

        # GraphQL pages usually arrive much sooner than WaitSeconds. Poll the
        # capture stream first and reserve expensive UI dumps for no-growth or
        # periodic health checks. Source validation remains mandatory on every
        # observed family update before it can influence pagination state.
        $next = $null
        $fastGrowth = $false
        $useLiveTiming = [bool](
            $current -and
            $current.PSObject.Properties['progress_source'] -and
            [string]$current.progress_source -eq 'live'
        )
        $useSteadyShortPoll = [bool]($useLiveTiming -and $strategy -eq 'long' -and $strategyIdle -lt 2)
        $activePollIntervalMs = if ($useLiveTiming) { $livePollIntervalMs } else { $pollIntervalMs }
        $activePollTimeoutMs = if ($useSteadyShortPoll) {
            [Math]::Min($livePollTimeoutMs, $steadyNoGrowthPollTimeoutMs)
        }
        elseif ($useLiveTiming) { $livePollTimeoutMs } else { $pollTimeoutMs }
        if ($useSteadyShortPoll) { $steadyShortPollCycles++ }
        if ($useLiveTiming) { $livePollCycles++ } else { $fallbackPollCycles++ }

        # Burst mode is producer/consumer style: swipes run first while mitmproxy
        # captures independently.  If the response is already present by the
        # time the burst finishes, consume it immediately with zero sleep.
        if ($useLiveTiming) {
            $immediate = Get-CaptureProgress 'reaction'
            $progressPollCount++
            if ($immediate) {
                if (-not (Test-ExpectedSourceGuard $immediate 'reaction' 'reaction-pagination')) {
                    $current = $immediate
                    $next = $immediate
                    $stopReason = 'target-mismatch'
                }
                else {
                    $next = $immediate
                    $immediateMetric = Get-ProgressMetric $immediate 'reaction'
                    if (-not [string]::IsNullOrWhiteSpace($immediateMetric) -and $immediateMetric -ne $previousMetric) {
                        $fastGrowth = $true
                    }
                }
            }
        }
        if ($stopReason -eq 'target-mismatch') { break }
        $pollDeadline = (Get-Date).AddMilliseconds($activePollTimeoutMs)
        while (-not $fastGrowth -and (Get-Date) -lt $pollDeadline) {
            Start-Sleep -Milliseconds $activePollIntervalMs
            $progressPollCount++
            $polled = Get-CaptureProgress 'reaction'
            if (-not $polled) { continue }
            if (-not (Test-ExpectedSourceGuard $polled 'reaction' 'reaction-pagination')) {
                $current = $polled
                $next = $polled
                $stopReason = 'target-mismatch'
                break
            }
            $next = $polled
            $polledMetric = Get-ProgressMetric $polled 'reaction'
            if (-not [string]::IsNullOrWhiteSpace($polledMetric) -and $polledMetric -ne $previousMetric) {
                $fastGrowth = $true
                break
            }
        }
        if ($stopReason -eq 'target-mismatch') { break }

        $candidateKey = $reactionKey
        if ($next -and [int]$next.latest_line -gt $trackedLatestLine -and $next.latest_group_key) {
            $candidateKey = [string]$next.latest_group_key
        }
        $candidateGroup = if ($next) { Get-ReactionGroup $next $candidateKey } else { $null }
        $periodicUiCheck = (($attempts % $uiDumpEveryN) -eq 0)
        $noGrowthUiCheck = (-not $fastGrowth) -and ((($strategyIdle + 1) % $noGrowthUiDumpEveryN) -eq 0)
        $needsUiDump = (-not $next) -or $noGrowthUiCheck -or $periodicUiCheck -or [bool]($candidateGroup -and $candidateGroup.terminal_page_seen -eq $true)
        if ($needsUiDump) {
            $dumpPath = Dump-Ui ("{0}-{1:00}-{2}.xml" -f $Prefix, $attempts, $strategy)
            $uiDumpCount++
            if ($dumpPath) {
                $pageState = Get-PageState $dumpPath
                if ($pageState -notin @('reaction-list', 'reaction-list-loading')) {
                    $reactionListLosses++
                    Write-Warning "Reaction pagination left the verified Reaction list (state=$pageState, attempt=$attempts); trying one guarded Back recovery."
                    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', 'KEYCODE_BACK')
                    Start-Sleep -Seconds 1
                    $recoveryPath = Dump-Ui ("{0}-{1:00}-{2}-recover.xml" -f $Prefix, $attempts, $strategy)
                    $uiDumpCount++
                    $recoveryState = if ($recoveryPath) { Get-PageState $recoveryPath } else { '' }
                    if ($recoveryState -in @('reaction-list', 'reaction-list-loading')) {
                        $reactionListRecoveries++
                        $strategyIndex = 0
                        $strategyIdle = 0
                        Write-Host "Reaction list recovered after guarded Back; resuming from the existing capture cursor."
                        Wait-ReactionMinCycle $cycleStarted $minCycleMs
                        continue
                    }
                    $stopReason = 'reaction-list-lost'
                    Write-Warning "Reaction list recovery failed (state=$recoveryState); preserving the open cursor and stopping without claiming completion."
                    break
                }
            }
            $refreshed = Get-CaptureProgress 'reaction'
            if ($refreshed) {
                if (-not (Test-ExpectedSourceGuard $refreshed 'reaction' 'reaction-pagination')) {
                    $current = $refreshed
                    $next = $refreshed
                    $stopReason = 'target-mismatch'
                    break
                }
                $next = $refreshed
            }

            $refreshedMetric = if ($next) { Get-ProgressMetric $next 'reaction' } else { '' }
            $loaderVisible = [bool](
                (-not $fastGrowth) -and
                ([string]::IsNullOrWhiteSpace($refreshedMetric) -or $refreshedMetric -eq $previousMetric)
            )
            if ($loaderVisible -and $dumpPath) {
                $loaderNode = Get-UiNodes $dumpPath | Where-Object {
                    $_.Class -match 'ProgressBar' -and $_.Y1 -ge 300
                } | Select-Object -First 1
                $loaderVisible = [bool]$loaderNode
            }
            if ($loaderVisible) {
                $loaderBackpressureCycles++
                $loaderResolved = $false
                $loaderDeadline = (Get-Date).AddMilliseconds($loaderBackpressureTimeoutMs)
                while (-not $loaderResolved -and (Get-Date) -lt $loaderDeadline) {
                    Start-Sleep -Milliseconds $livePollIntervalMs
                    $progressPollCount++
                    $loaderBackpressurePolls++
                    $loaderProgress = Get-CaptureProgress 'reaction'
                    if (-not $loaderProgress) { continue }
                    if (-not (Test-ExpectedSourceGuard $loaderProgress 'reaction' 'reaction-loader-backpressure')) {
                        $current = $loaderProgress
                        $next = $loaderProgress
                        $stopReason = 'target-mismatch'
                        break
                    }
                    $next = $loaderProgress
                    if ($loaderProgress.PSObject.Properties['session_restricted'] -and $loaderProgress.session_restricted -eq $true) {
                        $loaderResolved = $true
                        break
                    }
                    $loaderRateLimitLine = Get-ReactionRateLimitLine $loaderProgress
                    if ($loaderProgress.PSObject.Properties['latest_rate_limited'] -and $loaderProgress.latest_rate_limited -eq $true -and $loaderRateLimitLine -gt $lastHandledRateLimitLine) {
                        $loaderResolved = $true
                        break
                    }
                    $loaderMetric = Get-ProgressMetric $loaderProgress 'reaction'
                    if (-not [string]::IsNullOrWhiteSpace($loaderMetric) -and $loaderMetric -ne $previousMetric) {
                        $loaderBackpressureGrowthHits++
                        $loaderResolved = $true
                        break
                    }
                }
            }
        }
        if ($stopReason -eq 'target-mismatch') { break }
        if (-not $next) {
            $strategyIdle++
            $strategyIdleLimit = [Math]::Max(1, $ReactionStrategyIdleLimit)
            if ($strategyIdle -ge $strategyIdleLimit) {
                Write-Host "Reaction strategy $strategy produced no capture progress for $strategyIdle attempt(s); switching strategy."
                $strategyIndex++
                $strategyIdle = 0
            }
            Wait-ReactionMinCycle $cycleStarted $minCycleMs
            continue
        }

        if ($next.PSObject.Properties['session_restricted'] -and $next.session_restricted -eq $true) {
            $current = $next
            $stopReason = 'session-restricted'
            Write-Warning "Facebook session became temporarily feature-restricted (graphql_code=$($next.session_restriction_graphql_code), api_error_code=$($next.session_restriction_api_error_code), line=$($next.session_restriction_line)); stopping Reaction deep pagination."
            break
        }

        # A throttled GraphQL response advances live-progress bookkeeping
        # (query_records/latest_line) but it does not advance the reactor
        # connection. Handle it before the generic metric comparison so a
        # 1675004 record cannot be mistaken for successful pagination.
        $nextRateLimitLine = Get-ReactionRateLimitLine $next
        $nextRateLimited = [bool](
            $next.PSObject.Properties['latest_rate_limited'] -and
            $next.latest_rate_limited -eq $true -and
            $nextRateLimitLine -gt $lastHandledRateLimitLine
        )
        if ($nextRateLimited) {
            $current = $next
            $trackedLatestLine = [Math]::Max($trackedLatestLine, [int]$next.latest_line)
            $lastHandledRateLimitLine = $nextRateLimitLine
            $consecutiveRateLimits++
            $maxConsecutiveRateLimitsSeen = [Math]::Max($maxConsecutiveRateLimitsSeen, $consecutiveRateLimits)
            if ($consecutiveRateLimits -ge $rateLimitMaxConsecutive) {
                $stopReason = 'rate-limited'
                Write-Warning "Reaction GraphQL rate limit circuit breaker opened after $consecutiveRateLimits consecutive event(s) (code=$($next.latest_graphql_error_code), line=$lastHandledRateLimitLine); preserving cursor checkpoint and stopping this Reaction run."
                break
            }
            $backoffMultiplier = [Math]::Pow(2, [Math]::Max(0, $consecutiveRateLimits - 1))
            $backoffSeconds = [Math]::Min($rateLimitMaxBackoffSeconds, [int][Math]::Ceiling($rateLimitBaseBackoffSeconds * $backoffMultiplier))
            Write-Warning "Reaction GraphQL rate limit observed (code=$($next.latest_graphql_error_code), line=$lastHandledRateLimitLine, consecutive=$consecutiveRateLimits/$rateLimitMaxConsecutive); preserving cursor checkpoint and cooling down for $backoffSeconds second(s)."
            Start-Sleep -Seconds $backoffSeconds
            $refreshedAfterBackoff = Get-CaptureProgress 'reaction'
            if ($refreshedAfterBackoff) { $current = $refreshedAfterBackoff }
            Wait-ReactionMinCycle $cycleStarted $minCycleMs
            continue
        }

        if ([int]$next.latest_line -gt $trackedLatestLine -and $next.latest_group_key) {
            $reactionKey = [string]$next.latest_group_key
            $trackedLatestLine = [int]$next.latest_line
        }
        $metric = Get-ProgressMetric $next 'reaction'
        $grew = (-not [string]::IsNullOrWhiteSpace($metric)) -and ($metric -ne $previousMetric)
        if ($grew) {
            $consecutiveRateLimits = 0
            if ($fastGrowth) { $fastPollGrowthHits++ }
            if ($usePipelineBurst) {
                $burstGrowthHits++
                $noGrowthBursts = 0
            }
            $growthEvents++
            $lastGrowthAttempt = $attempts
            $strategyGrowth[$strategy] = [int]$strategyGrowth[$strategy] + 1
            $strategyIdle = 0
            Write-Host "Reaction pagination growth via strategy=$strategy key=$reactionKey metric=$metric"
            # Recovery strategies are probes, not sticky steady-state modes.
            # Once any non-long strategy proves that the list can grow again,
            # immediately return to the fast long swipe.  In particular this
            # prevents wait-long's explicit WaitSeconds backoff from being paid
            # on every subsequent page after a transient stall.
            if ($strategyIndex -ne 0) {
                Write-Host "Reaction pagination recovered via strategy=$strategy; resetting to long."
                $strategyIndex = 0
                $strategyResetsToLong++
            }
        }
        else {
            $strategyIdle++
            if ($usePipelineBurst) { $noGrowthBursts++ }
        }
        $current = $next
        $previousMetric = $metric
        $group = Get-ReactionGroup $current $reactionKey
        if ($group -and $group.terminal_page_seen -eq $true) {
            Write-Host "Adaptive reaction pagination stopped: terminal page reached for $reactionKey after $attempts attempt(s)."
            $stopReason = 'terminal'
            break
        }
        $strategyIdleLimit = [Math]::Max(1, $ReactionStrategyIdleLimit)
        if ($group -and $group.open_page_seen -eq $true) {
            $strategyIdleLimit = [Math]::Max($strategyIdleLimit, $ReactionOpenStrategyIdleLimit)
        }
        if ($strategyIdle -ge $strategyIdleLimit) {
            Write-Host "Reaction strategy $strategy produced no growth for $strategyIdle attempt(s); switching strategy."
            $strategyIndex++
            $strategyIdle = 0
        }
        Wait-ReactionMinCycle $cycleStarted $minCycleMs
    }

    $group = Get-ReactionGroup $current $reactionKey
    if (-not $unlimited -and $attempts -ge $absoluteCap -and $stopReason -notin @('terminal', 'ui-trigger-exhausted', 'rate-limited', 'session-restricted', 'reaction-list-lost', 'native-scroll-unavailable')) {
        $stopReason = 'absolute-cap'
    }
    $paginationElapsedMs = [int][Math]::Round(((Get-Date) - $paginationStarted).TotalMilliseconds)
    return [pscustomobject]@{
        reaction_key = $reactionKey
        stop_reason = $stopReason
        attempts = $attempts
        growth_events = $growthEvents
        last_strategy = $lastStrategy
        terminal_seen = [bool]($group -and $group.terminal_page_seen -eq $true)
        open_seen = [bool]($group -and $group.open_page_seen -eq $true)
        strategy_attempts = $strategyAttempts
        strategy_growth = $strategyGrowth
        base_budget = $baseBudget
        final_budget = $currentBudget
        extension_chunk = $extensionChunk
        budget_extensions = $budgetExtensions
        absolute_cap = $absoluteCap
        unlimited = $unlimited
        progress_poll_count = $progressPollCount
        fast_poll_growth_hits = $fastPollGrowthHits
        ui_dump_count = $uiDumpCount
        loader_backpressure_timeout_ms = $loaderBackpressureTimeoutMs
        loader_backpressure_cycles = $loaderBackpressureCycles
        loader_backpressure_polls = $loaderBackpressurePolls
        loader_backpressure_growth_hits = $loaderBackpressureGrowthHits
        poll_interval_ms = $pollIntervalMs
        poll_timeout_ms = $pollTimeoutMs
        live_poll_interval_ms = $livePollIntervalMs
        live_poll_timeout_ms = $livePollTimeoutMs
        steady_no_growth_poll_timeout_ms = $steadyNoGrowthPollTimeoutMs
        steady_short_poll_cycles = $steadyShortPollCycles
        live_poll_cycles = $livePollCycles
        fallback_poll_cycles = $fallbackPollCycles
        strategy_resets_to_long = $strategyResetsToLong
        pipeline_enabled = $pipelineEnabled
        burst_size = $pipelineBurstSize
        burst_swipe_gap_ms = $pipelineSwipeGapMs
        burst_count = $burstCount
        burst_swipes = $burstSwipes
        burst_growth_hits = $burstGrowthHits
        no_growth_bursts = $noGrowthBursts
        effective_swipes_per_growth = if ($growthEvents -gt 0) { [Math]::Round(([double]$attempts / [double]$growthEvents), 3) } else { $null }
        elapsed_ms = $paginationElapsedMs
        seconds_per_growth = if ($growthEvents -gt 0) { [Math]::Round(([double]$paginationElapsedMs / 1000.0 / [double]$growthEvents), 3) } else { $null }
        growths_per_minute = if ($paginationElapsedMs -gt 0) { [Math]::Round(([double]$growthEvents * 60000.0 / [double]$paginationElapsedMs), 3) } else { 0 }
        native_scroll_enabled = $nativeScrollEnabled
        native_scroll_attempts = $nativeScrollAttempts
        native_scroll_success = $nativeScrollSuccess
        native_scroll_fallbacks = $nativeScrollFallbacks
        native_scroll_swipes = $nativeScrollSwipes
        native_scroll_partial_bursts = $nativeScrollPartialBursts
        native_scroll_timeout_ms = $nativeScrollTimeoutMs
        reaction_list_losses = $reactionListLosses
        reaction_list_recoveries = $reactionListRecoveries
        long_swipe_start_y = $ReactionLongSwipeStartY
        long_swipe_end_y = $ReactionLongSwipeEndY
        long_swipe_duration_ms = $ReactionLongSwipeDurationMs
        ui_dump_every_n = $uiDumpEveryN
        min_cycle_ms = $minCycleMs
        initial_pagination_cooldown_seconds = $initialPaginationCooldownSeconds
        initial_pagination_cooldown_applied_seconds = $initialPaginationCooldownApplied
        rate_limit_base_backoff_seconds = $rateLimitBaseBackoffSeconds
        rate_limit_max_backoff_seconds = $rateLimitMaxBackoffSeconds
        rate_limit_max_consecutive = $rateLimitMaxConsecutive
        max_consecutive_rate_limits_seen = $maxConsecutiveRateLimitsSeen
        session_restricted = [bool]($current -and $current.PSObject.Properties['session_restricted'] -and $current.session_restricted -eq $true)
        session_restriction_line = if ($current -and $current.PSObject.Properties['session_restriction_line']) { [int]$current.session_restriction_line } else { 0 }
        session_restriction_graphql_code = if ($current -and $current.PSObject.Properties['session_restriction_graphql_code']) { $current.session_restriction_graphql_code } else { $null }
        session_restriction_api_error_code = if ($current -and $current.PSObject.Properties['session_restriction_api_error_code']) { $current.session_restriction_api_error_code } else { $null }
        no_growth_ui_dump_every_n = $noGrowthUiDumpEveryN
        last_growth_attempt = $lastGrowthAttempt
    }
}

function Is-ReactionTabLabel([string]$Label) {
    return (
        $Label -match '^\s*(?:\u5168\u90e8|All)?\s*[0-9][0-9,.\s]*(?:[,\uFF0C]\s*(?:\u7b2c|item)\s*[0-9]+.*)?\s*$' -or
        $Label -match '^\s*(?:\u6240\u6709\u5fc3\u60c5|\u5168\u90e8)\s*[0-9][0-9,.\s]*(?:\u4e2a|\u500b)?(?:\s*\u5df2\u9009)?\s*$' -or
        $Label -match '^\s*[0-9][0-9,.\s]*(?:\u4e2a|\u500b)\S{1,10}(?:\s*(?:\u5df2\u9009|\u5df2\u53d6\u6d88\u9009\u62e9))?\s*$' -or
        $Label -match '^\s*All\s+reactions?\s*[0-9][0-9,.\s]*(?:\s+selected)?\s*$'
    )
}

function Get-ReactionTabNodes([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    $tabBar = $nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Label -match '^\s*(?:\u5fc3\u60c5\u9009\u9879\u5361\u680f[\u3002.]?|Reaction tabs?(?: bar)?)\s*$'
    } | Select-Object -First 1
    if ($tabBar) {
        return @($nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Clickable -and $_.Class -eq 'android.widget.TabWidget' -and
            $_.Y1 -ge $tabBar.Y1 -and $_.Y2 -le ($tabBar.Y2 + 20) -and
            (Is-ReactionTabLabel $_.Label)
        })
    }
    return @($nodes | Where-Object {
        $_.Clickable -and $_.Y1 -ge 150 -and $_.Y2 -le 360 -and
        ($_.Class -match 'TabWidget|Button') -and (Is-ReactionTabLabel $_.Label)
    })
}

function Get-ReactionTabKey($Node) {
    if (-not $Node) { return '' }
    $label = ([string]$Node.Label -replace '\s+', ' ').Trim()
    return $label
}

function Get-ReactionVisibilityHint([string]$Path) {
    foreach ($node in Get-UiNodes $Path) {
        $label = [string]$node.Label
        if ([string]::IsNullOrWhiteSpace($label)) { continue }
        # Current Facebook builds may explicitly state that only friends' names
        # are visible even though the total reaction count is public.
        if (
            $label -match '\u53ea\u80fd\u770b\u5230.*\u597d\u53cb.*\u59d3\u540d' -or
            $label -match '(?i)only\s+(?:see|view).*friends?.*names?'
        ) {
            return 'friends-only-names'
        }
    }
    return ''
}

function Get-PageState([string]$Path) {
    $nodes = @(Get-UiNodes $Path)
    $blockingNode = $nodes | Where-Object {
        $_.Label -match "(?i)isn(?:'|&apos;)t responding|not responding" -or
        $_.Label -match '\u6ca1\u6709\u54cd\u5e94|\u65e0\u54cd\u5e94|\u6c92\u6709\u56de\u61c9'
    } | Select-Object -First 1
    if ($blockingNode) { return 'app-not-responding' }
    $reactionTabBar = $nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Label -match '^\s*(?:\u5fc3\u60c5\u9009\u9879\u5361\u680f|Reactions? tabs?(?: bar)?)[\u3002.\s]*$'
    } | Select-Object -First 1
    if ($reactionTabBar) {
        $reactionSheetTab = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Clickable -and $_.Class -eq 'android.widget.TabWidget' -and
            $_.Y1 -ge $reactionTabBar.Y1 -and $_.Y2 -le ($reactionTabBar.Y2 + 20) -and
            (Is-ReactionTabLabel $_.Label)
        } | Select-Object -First 1
        if ($reactionSheetTab) { return 'reaction-list' }
    }
    $reactionTabs = @(
        $nodes | Where-Object {
            $_.Clickable -and $_.Y1 -ge 150 -and $_.Y2 -le 360 -and
            ($_.Class -match 'TabWidget|Button') -and (Is-ReactionTabLabel $_.Label)
        }
    )
    if ($reactionTabs.Count -ge 1) { return 'reaction-list' }

    # Facebook's native Reaction page can spend several seconds in a loading
    # shell that already has the real top bar (Back / 心情|Reactions / Search)
    # but has not rendered the category tabs yet.  Treating this as unknown
    # caused legitimate list opens to be abandoned immediately.
    $reactionTitle = $nodes | Where-Object {
        $_.Y1 -le 220 -and $_.Label -match '^\s*(?:\u5fc3\u60c5|Reactions?)\s*$'
    } | Select-Object -First 1
    $reactionBack = $nodes | Where-Object {
        $_.Clickable -and $_.Y1 -le 220 -and $_.Label -match '^\s*(?:Back|\u8fd4\u56de|\u8fd4\u56de\u6309\u94ae|\u8fd4\u56de\u6309\u9215)\s*$'
    } | Select-Object -First 1
    $reactionSearch = $nodes | Where-Object {
        $_.Clickable -and $_.Y1 -le 220 -and $_.Label -match '^\s*(?:Search|\u641c\u7d22|\u641c\u5bfb|\u641c\u5c0b)\s*$'
    } | Select-Object -First 1
    $reactionProgress = $nodes | Where-Object {
        $_.Class -match 'ProgressBar' -and $_.Y1 -ge 300
    } | Select-Object -First 1
    $reactionContentList = $nodes | Where-Object {
        $_.Class -match 'RecyclerView' -and $_.Y1 -ge 280 -and $_.Y2 -gt 500 -and $_.Y2 -gt $_.Y1
    } | Select-Object -First 1
    if ($reactionTitle -and $reactionBack -and $reactionContentList -and -not $reactionProgress) {
        return 'reaction-list'
    }
    if ($reactionTitle -and $reactionBack -and ($reactionSearch -or $reactionProgress)) {
        return 'reaction-list-loading'
    }

    $shareTitlePattern = 'People who shared this|All shares|Reshares|\u5206\u4eab\u6b64\u5e16\u7684\u4eba|\u5206\u4eab\u6b64\u8d34\u7684\u4eba|\u5206\u4eab\u9019\u5247\u8cbc\u6587\u7684\u4eba|\u5206\u4eab\u9019\u7bc7\u8cbc\u6587\u7684\u4eba|\u5206\u4eab\u4e86\u8fd9\u7bc7\u5e16\u5b50\u7684\u4eba|\u5206\u4eab\u7528\u6236|\u5206\u4eab\u7528\u6237'
    if ($nodes | Where-Object { $_.Label -match $shareTitlePattern } | Select-Object -First 1) {
        return 'share-list'
    }
    $hasBack = $nodes | Where-Object { $_.Clickable -and $_.Label -match '^\s*(?:Back|\u8fd4\u56de|\u8fd4\u56de\u6309\u94ae|\u8fd4\u56de\u6309\u9215)\s*$' } | Select-Object -First 1
    $shareSummary = $nodes | Where-Object {
        (-not $_.Clickable) -and $_.Label -match '^\s*[0-9]+(?:[.,][0-9]+)?\s*(?:(?:[KkMmBb])|\u5343|\u4e07|\u842c|\u4ebf|\u5104)?\s*(?:shares?|reshares?|\u6b21\u5206\u4eab|\u6b21\u8f49\u767c|\u6b21\u8f6c\u53d1|\u4eba\u5206\u4eab)\s*$'
    } | Select-Object -First 1
    if ($hasBack -and $shareSummary) { return 'share-list' }

    $postMarker = $nodes | Where-Object {
        $_.Label -match '^\s*(?:Post|\u5e16\u5b50|\u8cbc\u6587|Like|Comment|Comments|Share|\u8d5e|\u8b9a|\u8bc4\u8bba|\u8a55\u8ad6|\u5206\u4eab\u6309\u94ae|\u5206\u4eab\u6309\u9215)\s*$'
    } | Select-Object -First 1
    if ($postMarker) { return 'post' }
    return 'placeholder-or-unknown'
}

function Wait-ReactionListReady([string]$InitialPath) {
    $path = $InitialPath
    $state = Get-PageState $path
    $loadingSeen = ($state -eq 'reaction-list-loading')
    $attempts = 0
    while ($state -eq 'reaction-list-loading' -and $attempts -lt [Math]::Max(1, $ReactionListReadyAttempts)) {
        $attempts++
        Start-Sleep -Seconds ([Math]::Max(1, $ReactionListReadyDelaySeconds))
        $path = Dump-Ui ("reaction-list-ready-{0:00}.xml" -f $attempts)
        $state = Get-PageState $path
        if ($state -eq 'reaction-list-loading') { $loadingSeen = $true }
    }
    return [pscustomobject]@{
        Path = $path
        State = $state
        Attempts = $attempts
        LoadingObserved = $loadingSeen
        Ready = ($state -eq 'reaction-list')
    }
}

$DiagnosticsPath = Join-Path $Out 'exploration-diagnostics.json'
$Diagnostics = [ordered]@{
    serial = $Serial
    output_dir = $Out
    started_at = (Get-Date).ToString('o')
    actions = @()
    pagination_runs = @()
    share_screening = $null
    reaction_screening = $null
    share_native_fallback = $null
    reaction_native_fallback = $null
    target_guard = $null
    target_guard_events = @()
    target_guard_check_count = 0
    target_guard_matched_count = 0
    target_guard_mismatch_count = 0
}
$script:DiagnosticsLastSaveAt = [datetime]::MinValue
$script:DiagnosticsSaveThrottleMs = 2000

function Save-Diagnostics([switch]$Throttle) {
    # Diagnostics are telemetry, not part of the capture control plane.  A
    # transient Windows reader/AV/indexer lock must never abort a long-running
    # Facebook pagination job.  Serialize once, retry bounded writes with a
    # short backoff, and degrade to a warning if the file stays unavailable.
    $now = Get-Date
    if ($Throttle -and $script:DiagnosticsLastSaveAt -ne [datetime]::MinValue) {
        if (($now - $script:DiagnosticsLastSaveAt).TotalMilliseconds -lt $script:DiagnosticsSaveThrottleMs) {
            return
        }
    }
    $json = $Diagnostics | ConvertTo-Json -Depth 10
    $maxAttempts = 8
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            [IO.File]::WriteAllText(
                $DiagnosticsPath,
                $json,
                [Text.UTF8Encoding]::new($false)
            )
            $script:DiagnosticsLastSaveAt = Get-Date
            return
        }
        catch [IO.IOException] {
            if ($attempt -ge $maxAttempts) {
                Write-Warning "Diagnostics write remained locked after $maxAttempts attempt(s); capture will continue: $DiagnosticsPath"
                return
            }
        }
        catch [UnauthorizedAccessException] {
            if ($attempt -ge $maxAttempts) {
                Write-Warning "Diagnostics write remained unavailable after $maxAttempts attempt(s); capture will continue: $DiagnosticsPath"
                return
            }
        }
        Start-Sleep -Milliseconds ([Math]::Min(800, 75 * $attempt))
    }
    return
}

function Test-ExpectedSourceGuard($Progress, [ValidateSet('share', 'reaction')][string]$Family, [string]$Phase) {
    if ([string]::IsNullOrWhiteSpace($ExpectedSourcePostId) -or -not $Progress) { return $true }
    $observed = @($Progress.observed_source_post_ids | Where-Object { $_ } | ForEach-Object { [string]$_ } | Select-Object -Unique)
    if ($observed.Count -eq 0) { return $true }
    $matched = ($observed.Count -eq 1 -and $observed[0] -eq [string]$ExpectedSourcePostId)
    $previousGuard = $Diagnostics['target_guard']
    $Diagnostics['target_guard_check_count'] = [int]$Diagnostics['target_guard_check_count'] + 1
    if ($matched) {
        $Diagnostics['target_guard_matched_count'] = [int]$Diagnostics['target_guard_matched_count'] + 1
    }
    else {
        $Diagnostics['target_guard_mismatch_count'] = [int]$Diagnostics['target_guard_mismatch_count'] + 1
    }
    $guardCheckCount = [int]$Diagnostics['target_guard_check_count']
    $guardEvent = [ordered]@{
        status = if ($matched) { 'matched' } else { 'mismatch' }
        expected_post_id = [string]$ExpectedSourcePostId
        family = $Family
        observed_source_post_ids = $observed
        phase = $Phase
        capture_min_line = [Math]::Max(1, $CaptureMinLine)
        timestamp = (Get-Date).ToString('o')
    }
    $Diagnostics['target_guard'] = $guardEvent
    $phaseChanged = (-not $previousGuard) -or ([string]$previousGuard['phase'] -ne $Phase)
    $recordDetailedEvent = (-not $matched) -or $phaseChanged -or ($guardCheckCount -eq 1) -or (($guardCheckCount % 50) -eq 0)
    if ($recordDetailedEvent) {
        $Diagnostics['target_guard_events'] += $guardEvent
        if (@($Diagnostics['target_guard_events']).Count -gt 256) {
            $Diagnostics['target_guard_events'] = @($Diagnostics['target_guard_events'] | Select-Object -Last 256)
        }
    }
    if ($matched) { Save-Diagnostics -Throttle | Out-Null } else { Save-Diagnostics | Out-Null }
    if (-not $matched) {
        Write-Warning "Target source mismatch during $Family/$Phase expected=$ExpectedSourcePostId observed=[$($observed -join ',')]; stopping this family before further pagination."
    }
    return $matched
}

function Add-Diagnostic(
    [string]$Action,
    [string]$BeforeState,
    $Candidate,
    [string]$AfterState,
    [bool]$Confirmed,
    [string]$FailureReason,
    [bool]$NormalizedViaComments = $false,
    [string]$VisibilityHint = '',
    [int]$EntryScanScrolls = 0,
    [string]$EntryMethod = ''
) {
    $candidateData = $null
    if ($Candidate) {
        $candidateData = [ordered]@{
            label = [string]$Candidate.Label
            resource_id = [string]$Candidate.ResourceId
            class = [string]$Candidate.Class
            bounds = [string]$Candidate.Bounds
        }
    }
    $Diagnostics.actions += [ordered]@{
        action = $Action
        before_state = $BeforeState
        candidate = $candidateData
        after_state = $AfterState
        confirmed = $Confirmed
        failure_reason = $FailureReason
        normalized_via_comments = $NormalizedViaComments
        entry_method = $EntryMethod
        visibility_hint = $VisibilityHint
        entry_scan_scrolls = $EntryScanScrolls
        timestamp = (Get-Date).ToString('o')
    }
    Save-Diagnostics
}

function Add-PaginationDiagnostic([int]$TabIndex, $Candidate, $Result) {
    if (-not $Result) { return }
    $Diagnostics.pagination_runs += [ordered]@{
        tab_index = $TabIndex
        tab_label = if ($Candidate) { [string]$Candidate.Label } else { '' }
        tab_bounds = if ($Candidate) { [string]$Candidate.Bounds } else { '' }
        reaction_key = [string]$Result.reaction_key
        stop_reason = [string]$Result.stop_reason
        attempts = [int]$Result.attempts
        growth_events = [int]$Result.growth_events
        last_strategy = [string]$Result.last_strategy
        terminal_seen = Get-SafeBoolProperty $Result 'terminal_seen' $false
        open_seen = Get-SafeBoolProperty $Result 'open_seen' $false
        strategy_attempts = $Result.strategy_attempts
        strategy_growth = $Result.strategy_growth
        base_budget = [int]$Result.base_budget
        final_budget = [int]$Result.final_budget
        extension_chunk = [int]$Result.extension_chunk
        budget_extensions = [int]$Result.budget_extensions
        absolute_cap = [int]$Result.absolute_cap
        unlimited = [bool]$Result.unlimited
        progress_poll_count = if ($Result.PSObject.Properties['progress_poll_count']) { [int]$Result.progress_poll_count } else { 0 }
        fast_poll_growth_hits = if ($Result.PSObject.Properties['fast_poll_growth_hits']) { [int]$Result.fast_poll_growth_hits } else { 0 }
        ui_dump_count = if ($Result.PSObject.Properties['ui_dump_count']) { [int]$Result.ui_dump_count } else { 0 }
        loader_backpressure_timeout_ms = if ($Result.PSObject.Properties['loader_backpressure_timeout_ms']) { [int]$Result.loader_backpressure_timeout_ms } else { [Math]::Max([Math]::Max(50, $ReactionLiveProgressPollIntervalMs), [Math]::Min(8000, ([Math]::Max([Math]::Max(100, $ReactionProgressPollIntervalMs), $ReactionProgressPollTimeoutMs) * 3))) }
        loader_backpressure_cycles = if ($Result.PSObject.Properties['loader_backpressure_cycles']) { [int]$Result.loader_backpressure_cycles } else { 0 }
        loader_backpressure_polls = if ($Result.PSObject.Properties['loader_backpressure_polls']) { [int]$Result.loader_backpressure_polls } else { 0 }
        loader_backpressure_growth_hits = if ($Result.PSObject.Properties['loader_backpressure_growth_hits']) { [int]$Result.loader_backpressure_growth_hits } else { 0 }
        poll_interval_ms = if ($Result.PSObject.Properties['poll_interval_ms']) { [int]$Result.poll_interval_ms } else { [Math]::Max(100, $ReactionProgressPollIntervalMs) }
        poll_timeout_ms = if ($Result.PSObject.Properties['poll_timeout_ms']) { [int]$Result.poll_timeout_ms } else { [Math]::Max([Math]::Max(100, $ReactionProgressPollIntervalMs), $ReactionProgressPollTimeoutMs) }
        live_poll_interval_ms = if ($Result.PSObject.Properties['live_poll_interval_ms']) { [int]$Result.live_poll_interval_ms } else { [Math]::Max(50, $ReactionLiveProgressPollIntervalMs) }
        live_poll_timeout_ms = if ($Result.PSObject.Properties['live_poll_timeout_ms']) { [int]$Result.live_poll_timeout_ms } else { [Math]::Max([Math]::Max(50, $ReactionLiveProgressPollIntervalMs), $ReactionLiveProgressPollTimeoutMs) }
        steady_no_growth_poll_timeout_ms = if ($Result.PSObject.Properties['steady_no_growth_poll_timeout_ms']) { [int]$Result.steady_no_growth_poll_timeout_ms } else { [Math]::Max([Math]::Max(50, $ReactionLiveProgressPollIntervalMs), $ReactionSteadyNoGrowthPollTimeoutMs) }
        steady_short_poll_cycles = if ($Result.PSObject.Properties['steady_short_poll_cycles']) { [int]$Result.steady_short_poll_cycles } else { 0 }
        live_poll_cycles = if ($Result.PSObject.Properties['live_poll_cycles']) { [int]$Result.live_poll_cycles } else { 0 }
        fallback_poll_cycles = if ($Result.PSObject.Properties['fallback_poll_cycles']) { [int]$Result.fallback_poll_cycles } else { 0 }
        strategy_resets_to_long = if ($Result.PSObject.Properties['strategy_resets_to_long']) { [int]$Result.strategy_resets_to_long } else { 0 }
        pipeline_enabled = if ($Result.PSObject.Properties['pipeline_enabled']) { [bool]$Result.pipeline_enabled } else { ($ReactionPipelineBurstSize -gt 1) }
        burst_size = if ($Result.PSObject.Properties['burst_size']) { [int]$Result.burst_size } else { [Math]::Max(1, $ReactionPipelineBurstSize) }
        burst_swipe_gap_ms = if ($Result.PSObject.Properties['burst_swipe_gap_ms']) { [int]$Result.burst_swipe_gap_ms } else { [Math]::Max(0, $ReactionPipelineSwipeGapMs) }
        burst_count = if ($Result.PSObject.Properties['burst_count']) { [int]$Result.burst_count } else { 0 }
        burst_swipes = if ($Result.PSObject.Properties['burst_swipes']) { [int]$Result.burst_swipes } else { 0 }
        burst_growth_hits = if ($Result.PSObject.Properties['burst_growth_hits']) { [int]$Result.burst_growth_hits } else { 0 }
        no_growth_bursts = if ($Result.PSObject.Properties['no_growth_bursts']) { [int]$Result.no_growth_bursts } else { 0 }
        effective_swipes_per_growth = if ($Result.PSObject.Properties['effective_swipes_per_growth']) { $Result.effective_swipes_per_growth } else { $null }
        elapsed_ms = if ($Result.PSObject.Properties['elapsed_ms']) { [int]$Result.elapsed_ms } else { 0 }
        seconds_per_growth = if ($Result.PSObject.Properties['seconds_per_growth']) { $Result.seconds_per_growth } else { $null }
        growths_per_minute = if ($Result.PSObject.Properties['growths_per_minute']) { $Result.growths_per_minute } else { 0 }
        native_scroll_enabled = if ($Result.PSObject.Properties['native_scroll_enabled']) { [bool]$Result.native_scroll_enabled } else { ($ReactionNativeScrollEnabled -ne 0) }
        native_scroll_attempts = if ($Result.PSObject.Properties['native_scroll_attempts']) { [int]$Result.native_scroll_attempts } else { 0 }
        native_scroll_success = if ($Result.PSObject.Properties['native_scroll_success']) { [int]$Result.native_scroll_success } else { 0 }
        native_scroll_fallbacks = if ($Result.PSObject.Properties['native_scroll_fallbacks']) { [int]$Result.native_scroll_fallbacks } else { 0 }
        native_scroll_swipes = if ($Result.PSObject.Properties['native_scroll_swipes']) { [int]$Result.native_scroll_swipes } else { 0 }
        native_scroll_partial_bursts = if ($Result.PSObject.Properties['native_scroll_partial_bursts']) { [int]$Result.native_scroll_partial_bursts } else { 0 }
        native_scroll_timeout_ms = if ($Result.PSObject.Properties['native_scroll_timeout_ms']) { [int]$Result.native_scroll_timeout_ms } else { [Math]::Max(500,$ReactionNativeScrollTimeoutMs) }
        ui_dump_every_n = if ($Result.PSObject.Properties['ui_dump_every_n']) { [int]$Result.ui_dump_every_n } else { if ($ReactionUiDumpEveryN -le 0) { 1 } else { $ReactionUiDumpEveryN } }
        min_cycle_ms = if ($Result.PSObject.Properties['min_cycle_ms']) { [int]$Result.min_cycle_ms } else { [Math]::Max(0, $ReactionMinCycleMs) }
        initial_pagination_cooldown_seconds = if ($Result.PSObject.Properties['initial_pagination_cooldown_seconds']) { [int]$Result.initial_pagination_cooldown_seconds } else { [Math]::Max(0, $ReactionInitialPaginationCooldownSeconds) }
        initial_pagination_cooldown_applied_seconds = if ($Result.PSObject.Properties['initial_pagination_cooldown_applied_seconds']) { [int]$Result.initial_pagination_cooldown_applied_seconds } else { 0 }
        session_restricted = if ($Result.PSObject.Properties['session_restricted']) { [bool]$Result.session_restricted } else { $false }
        session_restriction_line = if ($Result.PSObject.Properties['session_restriction_line']) { [int]$Result.session_restriction_line } else { 0 }
        session_restriction_graphql_code = if ($Result.PSObject.Properties['session_restriction_graphql_code']) { $Result.session_restriction_graphql_code } else { $null }
        session_restriction_api_error_code = if ($Result.PSObject.Properties['session_restriction_api_error_code']) { $Result.session_restriction_api_error_code } else { $null }
        long_swipe_start_y = if ($Result.PSObject.Properties['long_swipe_start_y']) { [int]$Result.long_swipe_start_y } else { $ReactionLongSwipeStartY }
        long_swipe_end_y = if ($Result.PSObject.Properties['long_swipe_end_y']) { [int]$Result.long_swipe_end_y } else { $ReactionLongSwipeEndY }
        long_swipe_duration_ms = if ($Result.PSObject.Properties['long_swipe_duration_ms']) { [int]$Result.long_swipe_duration_ms } else { $ReactionLongSwipeDurationMs }
        no_growth_ui_dump_every_n = if ($Result.PSObject.Properties['no_growth_ui_dump_every_n']) { [int]$Result.no_growth_ui_dump_every_n } else { if ($ReactionNoGrowthUiDumpEveryN -le 0) { 1 } else { $ReactionNoGrowthUiDumpEveryN } }
        capture_progress_live_hits = [int]$script:CaptureProgressLiveHits
        capture_progress_fallback_hits = [int]$script:CaptureProgressFallbackHits
        last_growth_attempt = [int]$Result.last_growth_attempt
        timestamp = (Get-Date).ToString('o')
    }
    Save-Diagnostics
}

function Set-ShareScreeningDecision(
    [string]$Classification,
    [string]$DecisionReason,
    [bool]$ListConfirmed,
    [string]$EntryMethod = '',
    $Progress = $null
) {
    $Diagnostics['share_screening'] = [ordered]@{
        classification = $Classification
        decision_reason = $DecisionReason
        list_confirmed = $ListConfirmed
        entry_method = $EntryMethod
        query_records = if ($Progress) { [int]$Progress.query_records } else { 0 }
        user_count = if ($Progress) { [int]$Progress.user_count } else { 0 }
        terminal_page_seen = [bool]($Progress -and $Progress.terminal_page_seen -eq $true)
        open_page_seen = [bool]($Progress -and $Progress.open_page_seen -eq $true)
        timestamp = (Get-Date).ToString('o')
    }
    Save-Diagnostics
    Write-Host "Share screening decision: classification=$Classification reason=$DecisionReason users=$($Diagnostics['share_screening']['user_count']) queries=$($Diagnostics['share_screening']['query_records']) terminal=$($Diagnostics['share_screening']['terminal_page_seen'])"
}

function Finalize-ShareScreening([bool]$ListConfirmed, [string]$EntryMethod, [string]$FallbackReason = '') {
    $progress = Get-CaptureProgress 'share'
    if (-not (Test-ExpectedSourceGuard $progress 'share' 'share-finalize')) {
        Set-ShareScreeningDecision 'target-mismatch' 'source-post-mismatch' $ListConfirmed $EntryMethod $progress
        return
    }
    if (-not $ListConfirmed) {
        Set-ShareScreeningDecision 'entry-or-query-failed' $FallbackReason $false $EntryMethod $progress
        return
    }
    if ($progress -and [int]$progress.user_count -gt 0) {
        Set-ShareScreeningDecision 'enumerable' 'visible-share-users' $true $EntryMethod $progress
        return
    }
    if ($progress -and [int]$progress.query_records -gt 0 -and $progress.terminal_page_seen -eq $true) {
        Set-ShareScreeningDecision 'no-visible-share-users' 'terminal-without-visible-share-users' $true $EntryMethod $progress
        return
    }
    if ($progress -and [int]$progress.query_records -gt 0) {
        Set-ShareScreeningDecision 'entry-or-query-failed' 'share-query-without-visible-users' $true $EntryMethod $progress
        return
    }
    Set-ShareScreeningDecision 'entry-or-query-failed' 'share-list-confirmed-query-not-triggered' $true $EntryMethod $progress
}

$adbState = Get-FacebookAdbState -Serial $Serial
if ($adbState -ne 'device') { throw "ADB device is not ready: $Serial" }
if (Get-Command Ensure-FacebookFrameworkServicesReady -ErrorAction SilentlyContinue) {
    if (-not (Ensure-FacebookFrameworkServicesReady -Serial $Serial -TimeoutSeconds 30)) { throw "Android framework services are not ready: $Serial" }
}
if (Get-Command Ensure-FacebookUiDumpHelper -ErrorAction SilentlyContinue) {
    $null = Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $Base
}
if (Get-Command Ensure-FacebookProjectTransport -ErrorAction SilentlyContinue) {
    $transport = Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort
    Write-Host "[transport] explorer network=$($transport.active_default_network) proxy=$($transport.proxy) capture=$($transport.capture_active)"
}

$before = Dump-Ui "before.xml"
if ($OpenShareList) {
    $beforeState = Get-PageState $before
    $shareScan = if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
        Find-StrictTargetShareEntry $before 'share' ([Math]::Min(2, [Math]::Max(0, $EntryScanScrollCount)))
    }
    else {
        Find-EntryWithFeedScan 'share' $before 'share' $EntryScanScrollCount
    }
    $shareEntry = $shareScan.Entry
    $sharePath = $shareScan.Path
    $shareEntryScanScrolls = [int]$shareScan.Scrolls
    $shareNormalized = $false
    $shareHandled = $false
    $shareEntryMethod = if ($shareEntry) {
        if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) { 'strict-target-detail' } else { 'feed-node' }
    } else { '' }
    if (-not $shareHandled -and -not $shareEntry -and $shareScan.CommentEntry -and [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
        $probeOffsets = @(70, 95, 45, 120)
        for ($probeIndex = 0; $probeIndex -lt $probeOffsets.Count -and -not $shareHandled; $probeIndex++) {
            $commentForProbe = Find-CommentsEntryNode $sharePath
            if (-not $commentForProbe) { break }
            $probeOffset = [int]$probeOffsets[$probeIndex]
            $hotspot = Get-FeedShareMetricHotspotNode $sharePath $commentForProbe $probeOffset
            if (-not $hotspot) { break }
            if (-not (Tap-Node $hotspot ("feed share metric visual probe {0}" -f ($probeIndex + 1)))) { continue }
            $hotspotPath = Dump-Ui ("share-feed-visual-probe-{0:00}.xml" -f ($probeIndex + 1))
            $hotspotState = Get-PageState $hotspotPath
            if ($hotspotState -eq 'share-list') {
                $summaryCandidate = Find-ShareCountNode $hotspotPath
                if (-not $summaryCandidate) { $summaryCandidate = $hotspot }
                Add-Diagnostic 'share' $beforeState $summaryCandidate $hotspotState $true '' $false '' $shareEntryScanScrolls 'feed-visual-metric'
                Scroll-ShareAdaptive 'share-scroll' $ShareScrollCount
                Finalize-ShareScreening $true 'feed-visual-metric'
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                Start-Sleep -Seconds $WaitSeconds
                Dump-Ui 'after-share-back.xml' | Out-Null
                $shareHandled = $true
                break
            }

            Write-Host "Feed share visual probe $($probeIndex + 1) did not open share-list (state=$hotspotState)."
            if ($hotspotState -ne 'post') {
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                Start-Sleep -Seconds $WaitSeconds
                $sharePath = Dump-Ui ("share-feed-visual-probe-{0:00}-back.xml" -f ($probeIndex + 1))
            }
            else {
                $sharePath = $hotspotPath
            }
            $shareEntry = Find-ShareCountNode $sharePath
            if ($shareEntry) {
                $shareEntryMethod = 'post-visual-probe-node'
                break
            }
            $shareScan = [pscustomobject]@{
                Path = $sharePath
                Entry = $shareEntry
                CommentEntry = Find-CommentsEntryNode $sharePath
                Scrolls = $shareEntryScanScrolls
                Surface = Get-FeedSurfaceState $sharePath
            }
        }
    }
    if ($shareHandled) {
        # Direct visual feed entry already completed and returned to the post.
    }
    elseif (-not $shareEntry) {
        $commentEntry = $shareScan.CommentEntry
        $usedFeedCommentCount = [bool]($commentEntry -and ([string]$commentEntry.ResourceId).EndsWith('/feed_comment_count'))
        if ($usedFeedCommentCount) {
            $shareEntryMethod = 'feed-comment-count-normalization'
        }
        if ($commentEntry) {
            $normalized = if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
                Open-PostDetailViaComments $sharePath 'share' -RequireDetailEntry
            }
            else {
                Open-PostDetailViaComments $sharePath 'share'
            }
            if ($normalized) {
                $shareNormalized = $true
                if (-not $usedFeedCommentCount) {
                    $shareEntryMethod = if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) { 'strict-target-comments-detail' } else { 'comments-normalized' }
                }
                $sharePath = $normalized.Path
                $Diagnostics['share_native_fallback'] = [ordered]@{
                    attempted = $true
                    succeeded = $false
                    method = if ($usedFeedCommentCount) { 'feed-comment-count-normalization' } else { 'comments-surface-validation' }
                    attempts = 1
                    initial_state = [string]$normalized.State
                    final_state = [string]$normalized.State
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics

                if ($normalized.State -eq 'page-info') {
                    Write-Warning 'Comments normalization opened Page Info; backing out instead of treating this as a Share no-entry result.'
                    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                    Start-Sleep -Seconds $WaitSeconds
                    $sharePath = Dump-Ui 'share-native-page-info-back.xml'
                    $Diagnostics['share_native_fallback']['method'] = if ($usedFeedCommentCount) { 'feed-comment-count+page-info-backout' } else { 'page-info-backout' }
                    $Diagnostics['share_native_fallback']['final_state'] = Get-PageState $sharePath
                    Save-Diagnostics
                }
                elseif ($normalized.State -eq 'comments-surface') {
                    $reveal = Reveal-NormalizedPostSummary $sharePath 'share' 2
                    $sharePath = $reveal.Path
                    $Diagnostics['share_native_fallback']['method'] = if ($usedFeedCommentCount) { 'feed-comment-count+comments-summary-reveal' } else { 'comments-summary-reveal' }
                    $Diagnostics['share_native_fallback']['attempts'] = 1 + [int]$reveal.Attempts
                    $Diagnostics['share_native_fallback']['succeeded'] = [bool]$reveal.Revealed
                    $Diagnostics['share_native_fallback']['final_state'] = Get-NormalizedPostSurfaceState $sharePath
                    Save-Diagnostics
                }
                $shareEntry = Find-ShareCountNode $sharePath
                if ($shareEntry -and $Diagnostics['share_native_fallback']) {
                    $Diagnostics['share_native_fallback']['succeeded'] = $true
                    Save-Diagnostics
                }
            }
        }
    }
    if ($shareHandled) {
        # Nothing else to do for this share action.
    }
    elseif (-not $shareEntry) {
        $failureState = Get-PageState $sharePath
        $failureReason = if ($shareNormalized) {
            'no-share-count-after-comments-normalization'
        }
        elseif (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
            'no-share-count-after-strict-target-detail-scan'
        }
        else {
            'no-visible-share-count-entry'
        }
        Add-Diagnostic 'share' $beforeState $null $failureState $false $failureReason $shareNormalized '' $shareEntryScanScrolls $shareEntryMethod
        Set-ShareScreeningDecision 'no-share-entry' $failureReason $false $shareEntryMethod $null
        Write-Host 'No visible share-count entry; skipping rather than opening the Share composer.'
        if ($shareNormalized) {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
            Start-Sleep -Seconds $WaitSeconds
            Dump-Ui "after-share-normalization-back.xml" | Out-Null
        }
    }
    elseif (Tap-Node $shareEntry 'share-count entry') {
        $shareList = Dump-Ui "share-list.xml"
        $afterState = Get-PageState $shareList
        if ($afterState -eq 'share-list') {
            Add-Diagnostic 'share' $beforeState $shareEntry $afterState $true '' $shareNormalized '' $shareEntryScanScrolls $shareEntryMethod
            Scroll-ShareAdaptive 'share-scroll' $ShareScrollCount
            Finalize-ShareScreening $true $shareEntryMethod
        }
        else {
            Add-Diagnostic 'share' $beforeState $shareEntry $afterState $false 'share-list-not-confirmed' $shareNormalized '' $shareEntryScanScrolls $shareEntryMethod
            Finalize-ShareScreening $false $shareEntryMethod 'share-list-not-confirmed'
            Write-Host "Share list not confirmed (state=$afterState); returning immediately."
        }
        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
        Start-Sleep -Seconds $WaitSeconds
        Dump-Ui "after-share-back.xml" | Out-Null
        if ($shareNormalized) {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
            Start-Sleep -Seconds $WaitSeconds
            Dump-Ui "after-share-normalization-back.xml" | Out-Null
        }
    }
}
if ($OpenReactionList) {
    $beforeState = Get-PageState $before
    $reactionPreopened = ($beforeState -eq 'reaction-list' -or $beforeState -eq 'reaction-list-loading')
    $reactionScan = $null
    $reactionEntry = $null
    $reactionPath = $before
    $reactionEntryScanScrolls = 0
    if (-not $reactionPreopened) {
        if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
            $reactionScan = Find-StrictTargetReactionEntry $before 'reaction' ([Math]::Min(2, [Math]::Max(0, $EntryScanScrollCount)))
        }
        else {
            $reactionScan = Find-EntryWithFeedScan 'reaction' $before 'reaction' $EntryScanScrollCount
        }
        $reactionEntry = $reactionScan.Entry
        $reactionPath = $reactionScan.Path
        $reactionEntryScanScrolls = [int]$reactionScan.Scrolls
    }
    $reactionNormalized = $false
    if (-not $reactionPreopened -and -not $reactionEntry) {
        $commentEntry = $reactionScan.CommentEntry
        if ($commentEntry) {
            $normalized = if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
                Open-PostDetailViaComments $reactionPath 'reaction' -RequireDetailEntry -Entry $commentEntry
            }
            else {
                Open-PostDetailViaComments $reactionPath 'reaction'
            }
            if ($normalized) {
                $reactionNormalized = $true
                $reactionPath = $normalized.Path
                $Diagnostics['reaction_native_fallback'] = [ordered]@{
                    attempted = $true
                    succeeded = $false
                    method = 'comments-surface-validation'
                    attempts = 1
                    initial_state = [string]$normalized.State
                    final_state = [string]$normalized.State
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics
                if ($normalized.State -eq 'page-info') {
                    Write-Warning 'Reaction normalization opened Page Info; backing out and refusing to use it as a reaction-count surface.'
                    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                    Start-Sleep -Seconds $WaitSeconds
                    $reactionPath = Dump-Ui 'reaction-native-page-info-back.xml'
                    $Diagnostics['reaction_native_fallback']['method'] = 'page-info-backout'
                    $Diagnostics['reaction_native_fallback']['final_state'] = Get-PageState $reactionPath
                    Save-Diagnostics
                }
                elseif ($normalized.State -eq 'comments-surface') {
                    # Match the proven Golden Light route: comments/detail can
                    # initially focus the composer while the numeric Reaction
                    # metric header remains clipped above the viewport. Reveal
                    # that header with bounded non-action swipes before deciding
                    # that the metric is unavailable.
                    $reveal = Reveal-NormalizedPostSummary $reactionPath 'reaction' 3
                    $reactionPath = $reveal.Path
                    $Diagnostics['reaction_native_fallback']['method'] = 'comments-summary-reveal'
                    $Diagnostics['reaction_native_fallback']['attempts'] = 1 + [int]$reveal.Attempts
                    $Diagnostics['reaction_native_fallback']['succeeded'] = [bool]$reveal.Revealed
                    $Diagnostics['reaction_native_fallback']['final_state'] = Get-NormalizedPostSurfaceState $reactionPath
                    Save-Diagnostics
                }
                $reactionEntry = Find-ReactionCountNode $reactionPath
                if (-not $reactionEntry) { $reactionEntry = Find-ReactionSummaryIconNode $reactionPath }
                if ($reactionEntry -and $Diagnostics['reaction_native_fallback']) {
                    $Diagnostics['reaction_native_fallback']['succeeded'] = $true
                    Save-Diagnostics
                }
            }
        }
    }
    if (-not $reactionPreopened -and -not $reactionEntry) {
        $failureState = Get-PageState $reactionPath
        $failureReason = if ($reactionNormalized) {
            'no-reaction-count-after-comments-normalization'
        }
        elseif (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
            'no-reaction-count-after-strict-target-detail-scan'
        }
        else {
            'no-visible-reaction-count-entry'
        }
        Add-Diagnostic 'reaction' $beforeState $null $failureState $false $failureReason $reactionNormalized '' $reactionEntryScanScrolls
        Write-Host 'No visible reaction-count entry; skipping.'
        if ($reactionNormalized) {
            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
            Start-Sleep -Seconds $WaitSeconds
            Dump-Ui "after-reaction-normalization-back.xml" | Out-Null
        }
    }
    elseif ($reactionPreopened -or (Activate-ReactionEntry $reactionEntry)) {
        $reactionListInitialName = if ($reactionPreopened) { 'reaction-list-preopened.xml' } else { 'reaction-list.xml' }
        $reactionList = Dump-Ui $reactionListInitialName
        $reactionIntermediateState = Get-PageState $reactionList
        if (-not $reactionPreopened -and $reactionIntermediateState -eq 'post') {
            $nestedReactionEntry = Find-ReactionCountNode $reactionList
            if ($nestedReactionEntry) {
                Write-Host 'Reaction entry opened an intermediate post panel; activating its explicit reaction count.'
                if (Activate-ReactionEntry $nestedReactionEntry) {
                    $reactionEntry = $nestedReactionEntry
                    $reactionList = Dump-Ui 'reaction-list-from-intermediate-post.xml'
                }
            }
        }
        $ready = Wait-ReactionListReady $reactionList
        $reactionList = $ready.Path
        $afterState = $ready.State
        $Diagnostics['reaction_native_fallback'] = [ordered]@{
            attempted = [bool]($reactionPreopened -or $ready.LoadingObserved)
            succeeded = [bool]$ready.Ready
            method = if ($reactionPreopened) { 'preopened-readonly-reactors-deeplink' } elseif ($ready.LoadingObserved) { 'native-reaction-loading-wait' } else { 'not-needed' }
            attempts = [int]$ready.Attempts
            initial_state = Get-PageState (Join-Path $Out $reactionListInitialName)
            final_state = [string]$ready.State
            timestamp = (Get-Date).ToString('o')
        }
        Save-Diagnostics
        $reactionVisibility = Get-ReactionVisibilityHint $reactionList
        if ($afterState -ne 'reaction-list') {
            Add-Diagnostic 'reaction' $beforeState $reactionEntry $afterState $false 'reaction-list-not-confirmed' $reactionNormalized $reactionVisibility $reactionEntryScanScrolls
            Write-Host "Reaction list not confirmed (state=$afterState); returning immediately."
                    $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
            Start-Sleep -Seconds $WaitSeconds
            Dump-Ui "after-reaction-back.xml" | Out-Null
            if ($reactionNormalized) {
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                Start-Sleep -Seconds $WaitSeconds
                Dump-Ui "after-reaction-normalization-back.xml" | Out-Null
            }
        }
        else {
            Add-Diagnostic 'reaction' $beforeState $reactionEntry $afterState $true '' $reactionNormalized $reactionVisibility $reactionEntryScanScrolls

            $deepCaptureAllowed = $true
            $decisionReason = ''
            $initialReactionProgress = Get-CaptureProgress 'reaction'
            if ([string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId) -and $AllowSourceDiscovery) {
                $discoveryAttempts = 0
                $discoveredSources = @()
                $discoveryTabTapped = $false
                while ($discoveryAttempts -lt [Math]::Max(1, $ReactionListReadyAttempts)) {
                    if ($initialReactionProgress) {
                        $discoveredSources = @($initialReactionProgress.observed_source_post_ids | Where-Object { $_ } | ForEach-Object { [string]$_ } | Select-Object -Unique)
                    }
                    if ($initialReactionProgress -and [int]$initialReactionProgress.query_records -gt 0 -and $discoveredSources.Count -gt 0) { break }
                    $discoveryAttempts++
                    Start-Sleep -Seconds ([Math]::Max(1, $ReactionListReadyDelaySeconds))
                    Dump-Ui ("reaction-source-discovery-{0:00}.xml" -f $discoveryAttempts) | Out-Null
                    $initialReactionProgress = Get-CaptureProgress 'reaction'
                }
                if (-not $initialReactionProgress -or [int]$initialReactionProgress.query_records -eq 0) {
                    $discoveryTab = Get-ReactionTabNodes $reactionList | Select-Object -First 1
                    if ($discoveryTab) {
                        $discoveryTabTapped = $true
                        Tap-Node $discoveryTab 'reaction source-discovery filter' | Out-Null
                        $reactionList = Dump-Ui 'reaction-source-discovery-filter.xml'
                        $initialReactionProgress = Get-CaptureProgress 'reaction'
                    }
                }
                $discoveredSources = @()
                if ($initialReactionProgress) {
                    $discoveredSources = @($initialReactionProgress.observed_source_post_ids | Where-Object { $_ } | ForEach-Object { [string]$_ } | Select-Object -Unique)
                }
                $discoveryMatched = [bool](
                    $initialReactionProgress -and
                    [int]$initialReactionProgress.query_records -gt 0 -and
                    $discoveredSources.Count -eq 1
                )
                if ($discoveryMatched) { $ExpectedSourcePostId = [string]$discoveredSources[0] }
                $Diagnostics['source_discovery'] = [ordered]@{
                    status = if ($discoveryMatched) { 'matched' } elseif ($discoveredSources.Count -gt 1) { 'ambiguous' } else { 'unproven' }
                    source_post_id = if ($discoveryMatched) { [string]$ExpectedSourcePostId } else { '' }
                    observed_source_post_ids = $discoveredSources
                    query_records = if ($initialReactionProgress) { [int]$initialReactionProgress.query_records } else { 0 }
                    attempts = $discoveryAttempts
                    tab_tapped = $discoveryTabTapped
                    capture_min_line = [Math]::Max(1, $CaptureMinLine)
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics
                if (-not $discoveryMatched) {
                    $deepCaptureAllowed = $false
                    $decisionReason = if ($discoveredSources.Count -gt 1) { 'source-discovery-ambiguous' } else { 'source-discovery-unproven' }
                    Write-Warning "Fresh Reel reactor query did not yield one unique source post; no tab or pagination action will run. observed=[$($discoveredSources -join ',')]"
                }
                else {
                    Write-Host "Fresh Reel reactor query bound this run to source_post_id=$ExpectedSourcePostId"
                }
            }
            if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)) {
                $proofAttempts = 0
                $observedProof = @()
                while ($proofAttempts -lt [Math]::Max(1, $ReactionListReadyAttempts)) {
                    if ($initialReactionProgress) {
                        $observedProof = @($initialReactionProgress.observed_source_post_ids | Where-Object { $_ } | ForEach-Object { [string]$_ } | Select-Object -Unique)
                    }
                    if ($initialReactionProgress -and [int]$initialReactionProgress.query_records -gt 0 -and $observedProof.Count -gt 0) { break }
                    $proofAttempts++
                    Start-Sleep -Seconds ([Math]::Max(1, $ReactionListReadyDelaySeconds))
                    Dump-Ui ("reaction-source-proof-{0:00}.xml" -f $proofAttempts) | Out-Null
                    $initialReactionProgress = Get-CaptureProgress 'reaction'
                }
                $observedProof = @()
                if ($initialReactionProgress) {
                    $observedProof = @($initialReactionProgress.observed_source_post_ids | Where-Object { $_ } | ForEach-Object { [string]$_ } | Select-Object -Unique)
                }
                $proofMatched = [bool](
                    $initialReactionProgress -and
                    [int]$initialReactionProgress.query_records -gt 0 -and
                    $observedProof.Count -eq 1 -and
                    $observedProof[0] -eq [string]$ExpectedSourcePostId
                )
                $Diagnostics['reaction_preopened_source_proof'] = [ordered]@{
                    status = if ($proofMatched) { 'matched' } elseif ($observedProof.Count -gt 0) { 'mismatch' } else { 'unproven' }
                    expected_post_id = [string]$ExpectedSourcePostId
                    method = if ($reactionPreopened) { 'preopened-deeplink' } else { 'direct-ui-reaction-count' }
                    observed_source_post_ids = $observedProof
                    query_records = if ($initialReactionProgress) { [int]$initialReactionProgress.query_records } else { 0 }
                    attempts = $proofAttempts
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics
                if (-not $proofMatched) {
                    $deepCaptureAllowed = $false
                    $decisionReason = if ($observedProof.Count -gt 0) { 'source-post-mismatch' } else { 'verified-deeplink-source-unproven' }
                    Write-Warning "Strict target Reaction list lacks exact fresh source proof; no tab or pagination action will run. expected=$ExpectedSourcePostId observed=[$($observedProof -join ',')]"
                }
            }
            if (-not (Test-ExpectedSourceGuard $initialReactionProgress 'reaction' 'reaction-list-open')) {
                $deepCaptureAllowed = $false
                $decisionReason = 'source-post-mismatch'
            }
            if (($AutoScreenThenDeep -or $ReactionProbeOnly) -and $deepCaptureAllowed) {
                $probeAttempts = 0
                $screenTabTapped = $false
                $screenProgress = if ($initialReactionProgress) { $initialReactionProgress } else { Get-CaptureProgress 'reaction' }
                $screenClassification = 'pending'
                if (-not (Test-ExpectedSourceGuard $screenProgress 'reaction' 'reaction-screening')) {
                    $screenClassification = 'target-mismatch'
                    $decisionReason = 'source-post-mismatch'
                }
                elseif ($reactionVisibility -eq 'friends-only-names') {
                    $screenClassification = 'visibility-restricted'
                    $decisionReason = 'friends-only-names'
                }
                elseif ($screenProgress -and [int]$screenProgress.user_count -gt 0) {
                    $screenClassification = 'enumerable'
                    $decisionReason = 'visible-reactor-users'
                }
                elseif ($screenProgress -and [int]$screenProgress.query_records -gt 0 -and $screenProgress.terminal_page_seen -eq $true) {
                    $screenClassification = 'not-enumerable'
                    $decisionReason = 'terminal-without-visible-users'
                }
                else {
                    $screenPath = $reactionList
                    $screenTabs = @(Get-ReactionTabNodes $screenPath)
                    $screenTab = $screenTabs | Where-Object {
                        $_.Label -match '(?:\u5df2\u53d6\u6d88\u9009\u62e9|(?i:not selected|unselected))'
                    } | Select-Object -First 1
                    if (-not $screenTab -and -not $ReactionProbeOnly) {
                        $screenTab = $screenTabs | Select-Object -First 1
                    }
                    if ($screenTab) {
                        $screenTabTapped = $true
                        Tap-Node $screenTab 'reaction screening filter' | Out-Null
                        $screenPath = Dump-Ui 'reaction-screen-category.xml'
                        $screenProgress = Get-CaptureProgress 'reaction'
                        if (-not (Test-ExpectedSourceGuard $screenProgress 'reaction' 'reaction-screening-tab')) {
                            $screenClassification = 'target-mismatch'
                            $decisionReason = 'source-post-mismatch'
                        }
                    }

                    $screenProbeScrollLimit = if ($ReactionProbeOnly) { 0 } else { [Math]::Max(0, $ReactionScreenScrollCount) }
                    for ($probe = 0; $screenClassification -ne 'target-mismatch' -and $probe -lt $screenProbeScrollLimit; $probe++) {
                        if ($screenProgress -and [int]$screenProgress.user_count -gt 0) { break }
                        if ($screenProgress -and [int]$screenProgress.query_records -gt 0 -and $screenProgress.terminal_page_seen -eq $true) { break }
                        $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'swipe', '540', '1650', '540', '500', '600')
                        Start-Sleep -Seconds $WaitSeconds
                        $probeAttempts++
                        Dump-Ui ("reaction-screen-probe-{0:00}.xml" -f $probeAttempts) | Out-Null
                        $screenProgress = Get-CaptureProgress 'reaction'
                        if (-not (Test-ExpectedSourceGuard $screenProgress 'reaction' 'reaction-screening-probe')) {
                            $screenClassification = 'target-mismatch'
                            $decisionReason = 'source-post-mismatch'
                            break
                        }
                    }

                    if ($screenClassification -eq 'target-mismatch') {
                        # Preserve the source guard decision; wrong-post users must never become enumerable.
                    }
                    elseif ($screenProgress -and [int]$screenProgress.user_count -gt 0) {
                        $screenClassification = 'enumerable'
                        $decisionReason = 'visible-reactor-users'
                    }
                    elseif ($screenProgress -and [int]$screenProgress.query_records -gt 0 -and $screenProgress.terminal_page_seen -eq $true) {
                        $screenClassification = 'not-enumerable'
                        $decisionReason = 'terminal-without-visible-users'
                    }
                    elseif ($screenProgress -and [int]$screenProgress.query_records -gt 0) {
                        $screenClassification = 'not-enumerable'
                        $decisionReason = 'query-without-visible-users'
                    }
                    else {
                        $screenClassification = 'not-enumerable'
                        $decisionReason = 'reaction-query-not-triggered'
                    }
                }

                $deepCaptureAllowed = ($deepCaptureAllowed -and $screenClassification -eq 'enumerable')
                $Diagnostics['reaction_screening'] = [ordered]@{
                    classification = $screenClassification
                    decision_reason = $decisionReason
                    probe_attempts = $probeAttempts
                    screen_tab_tapped = $screenTabTapped
                    query_records = if ($screenProgress) { [int]$screenProgress.query_records } else { 0 }
                    user_count = if ($screenProgress) { [int]$screenProgress.user_count } else { 0 }
                    terminal_page_seen = [bool]($screenProgress -and $screenProgress.terminal_page_seen -eq $true)
                    open_page_seen = [bool]($screenProgress -and $screenProgress.open_page_seen -eq $true)
                    probe_only = [bool]$ReactionProbeOnly
                    probe_status = if ($screenProgress -and $screenProgress.PSObject.Properties['session_restricted'] -and $screenProgress.session_restricted -eq $true) { 'blocked-session-restriction' } elseif ($screenProgress -and $screenProgress.PSObject.Properties['latest_rate_limited'] -and $screenProgress.latest_rate_limited -eq $true) { 'blocked-rate-limit' } elseif ($screenClassification -eq 'enumerable') { 'initial-ready' } else { 'not-ready' }
                    session_restricted = [bool]($screenProgress -and $screenProgress.PSObject.Properties['session_restricted'] -and $screenProgress.session_restricted -eq $true)
                    latest_rate_limited = [bool]($screenProgress -and $screenProgress.PSObject.Properties['latest_rate_limited'] -and $screenProgress.latest_rate_limited -eq $true)
                    latest_graphql_error_code = if ($screenProgress -and $screenProgress.PSObject.Properties['latest_graphql_error_code']) { $screenProgress.latest_graphql_error_code } else { $null }
                    session_restriction_friendly_name = if ($screenProgress -and $screenProgress.PSObject.Properties['session_restriction_friendly_name']) { [string]$screenProgress.session_restriction_friendly_name } else { $null }
                    deep_capture_requested = [bool]($deepCaptureAllowed -and -not $ReactionProbeOnly)
                    deep_capture_completed = $false
                    deep_pagination_runs = 0
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics
                Write-Host "Reaction screening decision: classification=$screenClassification reason=$decisionReason users=$($Diagnostics['reaction_screening']['user_count']) queries=$($Diagnostics['reaction_screening']['query_records']) deep=$($Diagnostics['reaction_screening']['deep_capture_requested']) probe=$($Diagnostics['reaction_screening']['probe_status'])"
            }
            elseif (($AutoScreenThenDeep -or $ReactionProbeOnly) -and -not $deepCaptureAllowed) {
                $Diagnostics['reaction_screening'] = [ordered]@{
                    classification = if ($decisionReason -eq 'source-post-mismatch') { 'target-mismatch' } else { 'not-enumerable' }
                    decision_reason = $decisionReason
                    probe_attempts = 0
                    screen_tab_tapped = $false
                    query_records = if ($initialReactionProgress) { [int]$initialReactionProgress.query_records } else { 0 }
                    user_count = if ($initialReactionProgress) { [int]$initialReactionProgress.user_count } else { 0 }
                    terminal_page_seen = [bool]($initialReactionProgress -and $initialReactionProgress.terminal_page_seen -eq $true)
                    open_page_seen = [bool]($initialReactionProgress -and $initialReactionProgress.open_page_seen -eq $true)
                    probe_only = [bool]$ReactionProbeOnly
                    probe_status = if ($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['session_restricted'] -and $initialReactionProgress.session_restricted -eq $true) { 'blocked-session-restriction' } elseif ($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['latest_rate_limited'] -and $initialReactionProgress.latest_rate_limited -eq $true) { 'blocked-rate-limit' } else { 'not-ready' }
                    session_restricted = [bool]($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['session_restricted'] -and $initialReactionProgress.session_restricted -eq $true)
                    latest_rate_limited = [bool]($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['latest_rate_limited'] -and $initialReactionProgress.latest_rate_limited -eq $true)
                    latest_graphql_error_code = if ($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['latest_graphql_error_code']) { $initialReactionProgress.latest_graphql_error_code } else { $null }
                    session_restriction_friendly_name = if ($initialReactionProgress -and $initialReactionProgress.PSObject.Properties['session_restriction_friendly_name']) { [string]$initialReactionProgress.session_restriction_friendly_name } else { $null }
                    deep_capture_requested = $false
                    deep_capture_completed = $false
                    deep_pagination_runs = 0
                    timestamp = (Get-Date).ToString('o')
                }
                Save-Diagnostics
            }

            # The top row contains reaction filters, not action buttons. Visit each
            # visible filter at most once, then perform bounded list scrolling.
            if ($reactionVisibility -eq 'friends-only-names') {
                Write-Host 'Reaction names are explicitly friends-only; skipping long category pagination.'
            }
            elseif ($ReactionProbeOnly) {
                Write-Host "[probe] Reaction health probe complete: status=$($Diagnostics['reaction_screening']['probe_status']); no pagination or category traversal was attempted."
            }
            elseif (-not $deepCaptureAllowed) {
                Write-Host "Reaction screening did not observe enumerable user nodes; skipping deep pagination ($decisionReason)."
            }
            else {
                # The initial reactors list is the ALL stream. Its edges already
                # include feedback_reaction_info, so deep-page ALL before touching
                # any per-reaction filter. This avoids an extra Like/other category
                # request while the same complete user stream is still open.
                $allFirstHandled = $false
                $allProgress = Get-CaptureProgress 'reaction'
                $allGroup = Get-ReactionGroup $allProgress '__ALL__'
                if ($allGroup) {
                    $allFirstHandled = $true
                    if ($allGroup.terminal_page_seen -eq $true) {
                        Write-Host 'Reaction ALL stream is already terminal; skipping redundant per-reaction category tabs.'
                    }
                    else {
                        Write-Host 'Reaction ALL-first pagination: continuing the existing ALL stream before any category tab.'
                        $allPaginationResult = Scroll-ReactionAdaptive 'reaction-all-scroll' $ReactionScrollCount $allProgress '__ALL__'
                        Add-PaginationDiagnostic 0 $null $allPaginationResult
                        if ([string]$allPaginationResult.stop_reason -in @('rate-limited', 'session-restricted')) {
                            Write-Warning "Reaction ALL-first pagination stopped by $([string]$allPaginationResult.stop_reason); category tabs will not be opened for this job."
                        }
                        elseif (Get-SafeBoolProperty $allPaginationResult 'terminal_seen' $false) {
                            Write-Host 'Reaction ALL stream reached terminal; skipping redundant per-reaction category tabs.'
                        }
                        else {
                            Write-Host "Reaction ALL-first pagination stopped with reason=$($allPaginationResult.stop_reason); preserving the ALL stream result and skipping category tabs."
                        }
                    }
                }

                $visitedTabs = @{}
                for ($tab = 0; -not $allFirstHandled -and $tab -lt [Math]::Max(0, $ReactionTabLimit); $tab++) {
                    $current = Dump-Ui ("reaction-tab-scan-{0:00}.xml" -f ($tab + 1))
                    $candidate = Get-ReactionTabNodes $current |
                        Where-Object { -not $visitedTabs.ContainsKey((Get-ReactionTabKey $_)) } |
                        Select-Object -First 1
                    if (-not $candidate) { break }
                    $tabKey = Get-ReactionTabKey $candidate
                    $visitedTabs[$tabKey] = $true
                    $progressBeforeTap = Get-CaptureProgress 'reaction'
                    Tap-Node $candidate ("reaction filter {0}" -f ($tab + 1)) | Out-Null
                    Dump-Ui ("reaction-category-{0:00}.xml" -f ($tab + 1)) | Out-Null
                    $progressAfterTap = Get-CaptureProgress 'reaction'
                    if (-not (Test-ExpectedSourceGuard $progressAfterTap 'reaction' 'reaction-tab-open')) {
                        $paginationResult = [pscustomobject]@{
                            reaction_key = ''
                            stop_reason = 'target-mismatch'
                            attempts = 0
                            growth_events = 0
                            last_strategy = ''
                            terminal_seen = $false
                            open_seen = $false
                            strategy_attempts = [ordered]@{}
                            strategy_growth = [ordered]@{}
                            base_budget = [Math]::Max(0, $ReactionScrollCount)
                            final_budget = 0
                            extension_chunk = [Math]::Max(1, $ReactionExtensionChunk)
                            budget_extensions = 0
                            absolute_cap = if ($ReactionAbsoluteCap -le 0) { 0 } else { [Math]::Max([Math]::Max(0, $ReactionScrollCount), $ReactionAbsoluteCap) }
                            unlimited = ($ReactionAbsoluteCap -le 0)
                            last_growth_attempt = 0
                        }
                        Add-PaginationDiagnostic ($tab + 1) $candidate $paginationResult
                        break
                    }
                    $paginationResult = Scroll-ReactionAdaptive ("reaction-category-{0:00}-scroll" -f ($tab + 1)) $ReactionScrollCount $progressBeforeTap
                    Add-PaginationDiagnostic ($tab + 1) $candidate $paginationResult
                    if ([string]$paginationResult.reaction_key -eq '__ALL__' -and (Get-SafeBoolProperty $paginationResult 'terminal_seen' $false)) {
                        Write-Host 'Reaction All tab reached terminal; skipping redundant per-reaction category tabs.'
                        break
                    }
                    if ([string]$paginationResult.stop_reason -eq 'target-mismatch') { break }
                }
                if ($AutoScreenThenDeep -and $Diagnostics['reaction_screening']) {
                    # Completion requires proof from the ALL reactor connection.
                    # Merely avoiding a target mismatch is not enough: an
                    # absolute cap, open tail, throttle or session restriction
                    # must remain explicitly incomplete.
                    $paginationRuns = @($Diagnostics.pagination_runs)
                    $allPaginationRuns = @($paginationRuns | Where-Object { [string]$_.reaction_key -eq '__ALL__' })
                    # Keep this as an array even when exactly one pagination run exists.
                    # PowerShell otherwise unwraps the single OrderedDictionary emitted by
                    # the if-expression, making [-1] index the dictionary instead of the run.
                    $completionRuns = @(if ($allPaginationRuns.Count -gt 0) { $allPaginationRuns } else { $paginationRuns })
                    $completionRun = if ($completionRuns.Count -gt 0) { $completionRuns[-1] } else { $null }
                    $completionTerminalSeen = Get-SafeBoolProperty $completionRun 'terminal_seen' $false
                    $completionOpenSeen = Get-SafeBoolProperty $completionRun 'open_seen' $false
                    $deepCaptureCompleted = [bool](
                        $completionRun -and
                        $completionTerminalSeen -and
                        [string]$completionRun.stop_reason -eq 'terminal'
                    )
                    $deepCompletionStatus = if ($deepCaptureCompleted) {
                        'terminal-complete'
                    }
                    elseif (-not $completionRun) {
                        'no-pagination-run'
                    }
                    elseif ([string]$completionRun.stop_reason -eq 'session-restricted') {
                        'session-restricted'
                    }
                    elseif ([string]$completionRun.stop_reason -eq 'rate-limited') {
                        'rate-limited'
                    }
                    elseif ([string]$completionRun.stop_reason -eq 'target-mismatch') {
                        'target-mismatch'
                    }
                    elseif ($completionOpenSeen) {
                        'partial-open-tail'
                    }
                    else {
                        'incomplete'
                    }
                    $Diagnostics['reaction_screening']['deep_capture_completed'] = $deepCaptureCompleted
                    $Diagnostics['reaction_screening']['deep_pagination_runs'] = $paginationRuns.Count
                    $Diagnostics['reaction_screening']['deep_completion_status'] = $deepCompletionStatus
                    $Diagnostics['reaction_screening']['deep_completion_reason'] = if ($completionRun) { [string]$completionRun.stop_reason } else { 'no-pagination-run' }
                    $Diagnostics['reaction_screening']['deep_terminal_seen'] = $completionTerminalSeen
                    Save-Diagnostics
                }
            }

            $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
            Start-Sleep -Seconds $WaitSeconds
            Dump-Ui "after-reaction-back.xml" | Out-Null
            if ($reactionNormalized) {
                $null = Invoke-FacebookAdbQuiet -Serial $Serial -Arguments @('shell', 'input', 'keyevent', '4')
                Start-Sleep -Seconds $WaitSeconds
                Dump-Ui "after-reaction-normalization-back.xml" | Out-Null
            }
        }
    }
}
if ($OpenComments) {
    $current = Dump-Ui "before-comments.xml"
    $commentEntry = Find-CommentsDetailEntryNode $current
    if (-not $commentEntry) { $commentEntry = Find-CommentsEntryNode $current }
    if (Tap-Node $commentEntry 'comments entry') {
        Dump-Ui "comments.xml" | Out-Null
    }
}
Write-Output "Readonly exploration saved: $Out"
