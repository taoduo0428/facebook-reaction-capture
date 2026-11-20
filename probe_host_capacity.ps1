$ErrorActionPreference='Stop'
$cs=Get-CimInstance Win32_ComputerSystem
$os=Get-CimInstance Win32_OperatingSystem
Write-Output ('cores=' + $cs.NumberOfLogicalProcessors)
Write-Output ('ram_gb=' + [math]::Round($cs.TotalPhysicalMemory/1GB,1))
Write-Output ('free_ram_gb=' + [math]::Round($os.FreePhysicalMemory/1MB,1))
