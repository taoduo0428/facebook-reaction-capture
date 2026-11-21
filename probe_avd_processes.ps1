$ErrorActionPreference='Stop'
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.CommandLine -and $_.CommandLine -match '(?i)fb-x86_64-api35|AVDManual\\fb-x86_64-api35\.avd'
} | ForEach-Object {
  Write-Output ('name=' + $_.Name)
  Write-Output ('pid=' + $_.ProcessId)
  Write-Output ('ppid=' + $_.ParentProcessId)
  Write-Output ('cmd=' + $_.CommandLine)
  Write-Output '---'
}
