[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9_.:-]+$')][string]$Serial,
    [Parameter(Mandatory=$true)][ValidateRange(1,65535)][int]$CapturePort
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'facebook_transport.ps1')
$watch = Start-FacebookTransportWatch -Serial $Serial -CapturePort $CapturePort -BasePath $PSScriptRoot -RequireVacant
if (-not $watch) { throw 'The scheduler watchdog did not start.' }
# This helper runs outside the completed capture Job. Read back the exact process
# identity before returning a host observation; never terminate or replace it.
$process = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int]$watch) -ErrorAction Stop
$path = [regex]::Escape((Join-Path $PSScriptRoot 'facebook_network_watch.ps1'))
if (-not $process -or $process.Name -ine 'powershell.exe' -or
    $process.ProcessId -ne $watch -or $process.ParentProcessId -ne $PID -or
    $process.CommandLine -notmatch ('(?:^|\s)-File\s+"?' + $path + '"?(?:\s|$)') -or
    $process.CommandLine -notmatch ('(?:^|\s)-Serial\s+"?' + [regex]::Escape($Serial) + '"?(?:\s|$)') -or
    $process.CommandLine -notmatch ('(?:^|\s)-CapturePort\s+' + $CapturePort + '(?:\s|$)')) {
    throw 'The new watchdog process identity was not confirmed.'
}
@{watchdog_pid=[int]$process.ProcessId; helper_pid=$PID; serial=$Serial; capture_port=$CapturePort;
  state='watchdog-running'} | ConvertTo-Json -Compress
