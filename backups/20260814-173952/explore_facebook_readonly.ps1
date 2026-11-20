[CmdletBinding()]
param(
    [string]$Serial = "emulator-5554",
    [switch]$OpenShareList,
    [switch]$OpenComments,
    [int]$WaitSeconds = 4
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Out = Join-Path $Base (Join-Path "readonly-exploration" $Stamp)
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Dump-Ui([string]$Name) {
    $path = Join-Path $Out $Name
    $remote = "/sdcard/codex-facebook-ui.xml"
    & adb -s $Serial shell uiautomator dump $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UI dump failed: $Name" }
    & adb -s $Serial pull $remote $path | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "UI dump pull failed: $Name" }
    return $path
}

function Find-Bounds([string]$Path, [string]$Pattern) {
    $xml = Get-Content -Raw -LiteralPath $Path
    $matches = [regex]::Matches($xml, '<node\b[^>]*(?:text|content-desc)="([^"]*)"[^>]*bounds="\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\]"')
    foreach ($match in $matches) {
        if ($match.Groups[1].Value -match $Pattern) {
            return @([int]$match.Groups[2].Value, [int]$match.Groups[3].Value, [int]$match.Groups[4].Value, [int]$match.Groups[5].Value, $match.Groups[1].Value)
        }
    }
    return $null
}

function Tap-First([string]$XmlPath, [string]$Pattern, [string]$Label) {
    $bounds = Find-Bounds $XmlPath $Pattern
    if (-not $bounds) {
        Write-Host "$Label not found; no tap performed"
        return $false
    }
    $x = [int](($bounds[0] + $bounds[2]) / 2)
    $y = [int](($bounds[1] + $bounds[3]) / 2)
    Write-Host "${Label}: $($bounds[4]) -> tap $x,$y"
    & adb -s $Serial shell input tap $x $y
    Start-Sleep -Seconds $WaitSeconds
    return $true
}

& adb -s $Serial get-state | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ADB device is not ready: $Serial" }

$before = Dump-Ui "before.xml"
if ($OpenShareList) {
    if (Tap-First $before '\d+次分享|\d+ shares' 'share-count entry') {
        Dump-Ui "share-list.xml" | Out-Null
        & adb -s $Serial shell input keyevent 4
        Start-Sleep -Seconds 2
        Dump-Ui "after-share-back.xml" | Out-Null
    }
}
if ($OpenComments) {
    $current = Dump-Ui "before-comments.xml"
    if (Tap-First $current '^评论$|^Comments$' 'comments entry') {
        Dump-Ui "comments.xml" | Out-Null
    }
}
Write-Output "Readonly exploration saved: $Out"
