$ErrorActionPreference='Stop'
Get-CimInstance Win32_Process -Filter "Name='emulator.exe'" | ForEach-Object {
  Write-Output ('pid=' + $_.ProcessId)
  Write-Output ('cmd=' + $_.CommandLine)
}
