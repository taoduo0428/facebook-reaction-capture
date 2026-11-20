$ErrorActionPreference = 'Stop'
$avd = 'D:\Android\AVDManual\fb-x86_64-api35.avd'
$backup = 'D:\Android\AVDManual\avd-lock-backup-visible'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Get-ChildItem -LiteralPath $avd -Filter '*.lock' -ErrorAction SilentlyContinue | ForEach-Object { Move-Item -LiteralPath $_.FullName -Destination $backup -Force }
Write-Output 'locks_cleared=1'
