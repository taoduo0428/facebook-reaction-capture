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
    $contentViewports = @($nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Scrollable -and
        $_.Class -match '(?i)(?:RecyclerView|ScrollView)' -and
        $_.X2 -gt $_.X1 -and $_.Y2 -gt $_.Y1
    })
    if (-not $contentViewports) { return $null }

    foreach ($candidate in @($nodes | Where-Object {
        $_.Package -eq 'com.facebook.katana' -and
        $_.Clickable -and $_.Naf -and
        $_.Class -eq 'android.view.ViewGroup' -and
        $_.X1 -ge 900 -and $_.X2 -le 1080 -and
        ($_.Y2 - $_.Y1) -ge 70 -and ($_.Y2 - $_.Y1) -le 180
    })) {
        $viewport = $contentViewports | Where-Object {
            $_.X1 -le $candidate.X1 -and $_.Y1 -le $candidate.Y1 -and
            $_.X2 -ge $candidate.X2 -and $_.Y2 -ge $candidate.Y2
        } | Select-Object -First 1
        if (-not $viewport) { continue }

        # A bottom composer can be inside the same viewport while covering the
        # action row; only accept candidates that end before its top edge.
        $composer = $nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Class -match '(?i)(?:AutoCompleteTextView|EditText)' -and
            $_.Y1 -ge $viewport.Y1 -and $_.Y1 -lt $viewport.Y2
        } | Sort-Object Y1 | Select-Object -First 1
        if ($composer -and $candidate.Y2 -ge $composer.Y1) { continue }
        if ($candidate.Label -match '(?i)(?:comment|comments|评论|評論).*button') { continue }

        $rowTolerance = 16
        $sameRow = @($nodes | Where-Object {
            $_.Package -eq 'com.facebook.katana' -and
            $_.Clickable -and
            [Math]::Abs($_.Y1 - $candidate.Y1) -le $rowTolerance -and
            [Math]::Abs($_.Y2 - $candidate.Y2) -le $rowTolerance
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
            $commentAction -and $_.X1 -ge $commentAction.X2 -and $_.X1 -le 650 -and
            $_.X2 -le $candidate.X1 -and
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
$p='C:\Users\lenovo\Desktop\facebook\transactions\photo-reaction-route-fix-20260925-152350\USER_CONFIRMED_FEED_ENTRY.xml'
$n=Find-ReactionSummaryIconNode $p; if($n){$n | Select-Object Label,Bounds,X1,Y1,X2,Y2 | ConvertTo-Json -Compress}else{'NO_MATCH'; exit 1}
