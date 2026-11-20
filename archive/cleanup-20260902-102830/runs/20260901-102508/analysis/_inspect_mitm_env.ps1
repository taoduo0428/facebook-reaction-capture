$ErrorActionPreference = 'Stop'
$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*mitmweb*' }
$procs | ForEach-Object {
  Write-Output ("NAME=" + $_.Name)
  Write-Output ("EXE=" + $_.ExecutablePath)
  Write-Output ("CMD=" + $_.CommandLine)
  if ($_.ExecutablePath) {
    $dir = Split-Path $_.ExecutablePath -Parent
    Write-Output ("DIR=" + $dir)
    Get-ChildItem -LiteralPath $dir -Filter 'mitm*.exe' -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("SIBLING=" + $_.FullName) }
    Get-ChildItem -LiteralPath $dir -Filter 'python*.exe' -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("PY=" + $_.FullName) }
  }
}