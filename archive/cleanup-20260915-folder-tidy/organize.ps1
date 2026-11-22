[CmdletBinding()]
param([ValidateSet('Archive', 'Verify', 'Restore')][string]$Mode = 'Verify')

$ErrorActionPreference = 'Stop'
$archiveRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$projectRoot = [IO.Path]::GetFullPath((Split-Path (Split-Path $archiveRoot -Parent) -Parent))
$receiptPath = Join-Path $archiveRoot 'receipt.json'

function Resolve-Child([string]$Base, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)') { throw "Invalid relative path: $Relative" }
    $resolved = [IO.Path]::GetFullPath((Join-Path $Base $Relative))
    if (-not $resolved.StartsWith($Base.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Path escaped its base: $Relative" }
    $cursor = $resolved
    while ($cursor -ne $Base) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point refused: $cursor" }
        }
        $cursor = Split-Path $cursor -Parent
    }
    return $resolved
}

function Get-Sha256([string]$File) {
    $stream = [IO.File]::Open($File, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hash.Dispose() }
}

function Save-Receipt($Receipt) {
    [IO.File]::WriteAllText($receiptPath, ($Receipt | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}

if ($Mode -eq 'Archive') {
    if (Test-Path -LiteralPath $receiptPath) { throw 'Receipt already exists; use Verify or Restore.' }
    $plan = Get-Content -LiteralPath (Join-Path $archiveRoot 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $release = @(Get-Content -LiteralPath (Join-Path $projectRoot 'release-files.txt') -Encoding UTF8 | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } | ForEach-Object { $_.Trim() })
    $protectedNames = @($release + @('facebook.local.json', 'facebook_workers.json') | Select-Object -Unique)
    $trackedNames = @(& git -C $projectRoot ls-files --cached)
    if ($LASTEXITCODE -ne 0) { throw 'Git tracked-file inventory failed.' }
    $protected = @(foreach ($name in $protectedNames) {
        $path = Resolve-Child $projectRoot $name
        [pscustomobject]@{ path = $name; sha256 = Get-Sha256 $path }
    })
    $seenSource = @{}; $seenDestination = @{}
    $files = @(foreach ($entry in $plan.files) {
        if ($seenSource.ContainsKey($entry.source) -or $seenDestination.ContainsKey($entry.destination)) { throw 'Duplicate plan entry.' }
        $seenSource[$entry.source] = $true; $seenDestination[$entry.destination] = $true
        if ($protectedNames -contains $entry.source) { throw "Protected file: $($entry.source)" }
        $source = Resolve-Child $projectRoot $entry.source
        $destination = Resolve-Child $archiveRoot $entry.destination
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Source is not a file: $source" }
        if (Test-Path -LiteralPath $destination) { throw "Destination exists: $destination" }
        if ($trackedNames -contains $entry.source) { throw "Tracked source refused: $($entry.source)" }
        $item = Get-Item -LiteralPath $source
        [pscustomobject]@{ source = $entry.source; destination = $entry.destination; bytes = $item.Length; sha256 = Get-Sha256 $source }
    })
    $receipt = [pscustomobject]@{ state = 'prepared'; created_at = [DateTime]::UtcNow.ToString('o'); project = $projectRoot; files = $files; protected = $protected }
    Save-Receipt $receipt
    foreach ($entry in $files) {
        $source = Resolve-Child $projectRoot $entry.source
        $destination = Resolve-Child $archiveRoot $entry.destination
        if ((Get-Sha256 $source) -ne $entry.sha256) { throw "Source changed: $source" }
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Move-Item -LiteralPath $source -Destination $destination -ErrorAction Stop
        if ((Get-Sha256 $destination) -ne $entry.sha256) { throw "Archive hash mismatch: $destination" }
    }
    $receipt.state = 'archived'; Save-Receipt $receipt
} else {
    $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($receipt.project -ne $projectRoot) { throw 'Receipt belongs to another project.' }
}

if ($Mode -eq 'Restore') {
    $pending = @(foreach ($entry in $receipt.files) {
        $source = Resolve-Child $projectRoot $entry.source
        $destination = Resolve-Child $archiveRoot $entry.destination
        if (Test-Path -LiteralPath $destination) {
            if (Test-Path -LiteralPath $source) { throw "Original path already exists; no overwrite allowed: $source" }
            if ((Get-Sha256 $destination) -ne $entry.sha256) { throw "Archive hash mismatch: $destination" }
            $entry
        } elseif (-not (Test-Path -LiteralPath $source) -or (Get-Sha256 $source) -ne $entry.sha256) { throw "Missing or changed file: $source" }
    })
    foreach ($entry in $pending) {
        Move-Item -LiteralPath (Resolve-Child $archiveRoot $entry.destination) -Destination (Resolve-Child $projectRoot $entry.source) -ErrorAction Stop
    }
    $receipt.state = 'restored'; Save-Receipt $receipt
}

foreach ($entry in $receipt.files) {
    if ($receipt.state -eq 'restored') { $path = Resolve-Child $projectRoot $entry.source }
    else { $path = Resolve-Child $archiveRoot $entry.destination }
    if ((Get-Sha256 $path) -ne $entry.sha256) { throw "Verification mismatch: $path" }
}
if ($Mode -ne 'Restore') {
    foreach ($entry in $receipt.protected) {
        if ((Get-Sha256 (Resolve-Child $projectRoot $entry.path)) -ne $entry.sha256) { throw "Protected file changed: $($entry.path)" }
    }
}
[pscustomobject]@{ state = $receipt.state; files = @($receipt.files).Count; bytes = ($receipt.files | Measure-Object bytes -Sum).Sum; protected_files_verified = @($receipt.protected).Count; receipt = $receiptPath } | ConvertTo-Json -Compress
