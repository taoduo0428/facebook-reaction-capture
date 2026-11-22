[CmdletBinding()]
param(
    [ValidateSet('worker-01', 'worker-02')]
    [string]$WorkerId = 'worker-01'
)

$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$AvdName = if ($WorkerId -eq 'worker-02') { 'fb-x86_64-api35-worker-02' } else { 'fb-x86_64-api35' }
$Serial = if ($WorkerId -eq 'worker-02') { 'emulator-5556' } else { 'emulator-5554' }
$EmulatorPort = if ($WorkerId -eq 'worker-02') { 5556 } else { 5554 }
. (Join-Path $Base 'facebook_transport.ps1')

$avdPattern = '(?i)(?:^|\s)-avd\s+' + [regex]::Escape($AvdName) + '(?:\s|$)'
$portPattern = '(?i)(?:^|\s)-port\s+' + [string]$EmulatorPort + '(?:\s|$)'
$matchingEmulators = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq 'qemu-system-x86_64.exe' -and
    $_.CommandLine -and
    $_.CommandLine -match $avdPattern -and
    $_.CommandLine -match $portPattern
})
$interactiveEmulators = @($matchingEmulators | Where-Object { [int]$_.SessionId -ne 0 })
if ($interactiveEmulators.Count -gt 1) {
    throw "Multiple interactive emulator processes match $WorkerId ($AvdName/$Serial); refusing ambiguous reuse."
}
if ($interactiveEmulators.Count -eq 1) {
    Write-Output "worker_id=$WorkerId already_running_pid=$($interactiveEmulators[0].ProcessId) session=$($interactiveEmulators[0].SessionId)"
    return
}

$sessionZeroEmulators = @($matchingEmulators | Where-Object { [int]$_.SessionId -eq 0 })
if ($sessionZeroEmulators.Count -gt 1) {
    throw "Multiple Session 0 emulator processes match $WorkerId ($AvdName/$Serial); refusing ambiguous cleanup."
}
if ($sessionZeroEmulators.Count -eq 1) {
    $adbState = [string](Get-FacebookAdbState -Serial $Serial)
    if ($adbState -eq 'device') {
        throw "Session 0 emulator for $WorkerId is ADB-ready; refusing automatic cleanup."
    }
    if (Get-FacebookCaptureMarker -Serial $Serial) {
        throw "Session 0 emulator for $WorkerId has an active capture marker; refusing automatic cleanup."
    }

    $stalePid = [int]$sessionZeroEmulators[0].ProcessId
    $confirmed = Get-CimInstance Win32_Process -Filter "ProcessId=$stalePid" -ErrorAction SilentlyContinue
    if (-not $confirmed -or $confirmed.Name -ne 'qemu-system-x86_64.exe' -or [int]$confirmed.SessionId -ne 0 -or
        -not $confirmed.CommandLine -or $confirmed.CommandLine -notmatch $avdPattern -or $confirmed.CommandLine -notmatch $portPattern) {
        throw "Session 0 emulator identity changed before cleanup for $WorkerId; refusing to stop PID $stalePid."
    }

    $parentPid = [int]$confirmed.ParentProcessId
    Write-Output "worker_id=$WorkerId stale_session0_pid=$stalePid adb_state=$adbState cleanup=begin"
    Stop-Process -Id $stalePid -Force -ErrorAction Stop
    $stopDeadline = (Get-Date).AddSeconds(10)
    while ((Get-Process -Id $stalePid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $stopDeadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Get-Process -Id $stalePid -ErrorAction SilentlyContinue) {
        throw "Session 0 emulator PID $stalePid did not exit after cleanup."
    }

    if ($parentPid -gt 0) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentPid" -ErrorAction SilentlyContinue
        if ($parent -and $parent.Name -eq 'emulator.exe' -and [int]$parent.SessionId -eq 0 -and $parent.CommandLine -and
            $parent.CommandLine -match $avdPattern -and $parent.CommandLine -match $portPattern) {
            Stop-Process -Id $parentPid -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Output "worker_id=$WorkerId stale_session0_pid=$stalePid cleanup=complete"
}
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class UserSessionLauncher {
    public const uint PROCESS_QUERY_INFORMATION = 0x0400;
    public const uint TOKEN_ASSIGN_PRIMARY = 0x0001;
    public const uint TOKEN_DUPLICATE = 0x0002;
    public const uint TOKEN_QUERY = 0x0008;
    public const uint TOKEN_ALL_ACCESS = 0xF01FF;
    public const int SecurityImpersonation = 2;
    public const int TokenPrimary = 1;
    public const uint LOGON_WITH_PROFILE = 0x00000001;
    public const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX;
        public int dwY;
        public int dwXSize;
        public int dwYSize;
        public int dwXCountChars;
        public int dwYCountChars;
        public int dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess;
        public IntPtr hThread;
        public int dwProcessId;
        public int dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

    [DllImport("advapi32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool OpenProcessToken(IntPtr ProcessHandle, uint DesiredAccess, out IntPtr TokenHandle);

    [DllImport("advapi32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DuplicateTokenEx(IntPtr hExistingToken, uint dwDesiredAccess, IntPtr lpTokenAttributes, int ImpersonationLevel, int TokenType, out IntPtr phNewToken);

    [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CreateProcessWithTokenW(IntPtr hToken, uint dwLogonFlags, string lpApplicationName, string lpCommandLine, uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory, ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool ProcessIdToSessionId(uint dwProcessId, out uint pSessionId);

    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool CloseHandle(IntPtr hObject);
}
'@

$explorer = Get-Process explorer -ErrorAction Stop | Where-Object { $_.SessionId -ne 0 } | Select-Object -First 1
if (-not $explorer) { throw 'No interactive Explorer process found.' }

$commandFileName = if ($WorkerId -eq 'worker-02') { 'visible_emulator_worker02.cmd' } else { 'visible_emulator_only.cmd' }
$commandFile = Join-Path $Base $commandFileName
if (-not (Test-Path -LiteralPath $commandFile)) { throw "Emulator command file not found: $commandFile" }

$currentSessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
if ($currentSessionId -ne 0) {
    $shell = New-Object -ComObject Shell.Application
    $shell.ShellExecute($commandFile, '', $Base, 'open', 1)
    Write-Output "worker_id=$WorkerId launched_via=interactive-shell caller_session=$currentSessionId"
    return
}

$processHandle = [UserSessionLauncher]::OpenProcess([UserSessionLauncher]::PROCESS_QUERY_INFORMATION, $false, $explorer.Id)
if ($processHandle -eq [IntPtr]::Zero) { throw "OpenProcess failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())" }
$token = [IntPtr]::Zero
$primary = [IntPtr]::Zero
$pi = New-Object UserSessionLauncher+PROCESS_INFORMATION
try {
    if (-not [UserSessionLauncher]::OpenProcessToken($processHandle, ([UserSessionLauncher]::TOKEN_ASSIGN_PRIMARY -bor [UserSessionLauncher]::TOKEN_DUPLICATE -bor [UserSessionLauncher]::TOKEN_QUERY), [ref]$token)) {
        throw "OpenProcessToken failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    if (-not [UserSessionLauncher]::DuplicateTokenEx($token, [UserSessionLauncher]::TOKEN_ALL_ACCESS, [IntPtr]::Zero, [UserSessionLauncher]::SecurityImpersonation, [UserSessionLauncher]::TokenPrimary, [ref]$primary)) {
        throw "DuplicateTokenEx failed: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    $si = New-Object UserSessionLauncher+STARTUPINFO
    $si.cb = [Runtime.InteropServices.Marshal]::SizeOf([type]'UserSessionLauncher+STARTUPINFO')
    $si.lpDesktop = 'winsta0\default'
    $app = 'C:\Windows\System32\cmd.exe'
    $cmd = '"' + $app + '" /d /s /c ""' + $commandFile + '""'
    $ok = [UserSessionLauncher]::CreateProcessWithTokenW(
        $primary,
        [UserSessionLauncher]::LOGON_WITH_PROFILE,
        $app,
        $cmd,
        0,
        [IntPtr]::Zero,
        $Base,
        [ref]$si,
        [ref]$pi
    )
    if (-not $ok) {
        throw "CreateProcessWithTokenW failed from Session 0: $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }

    [uint32]$launchedSessionId = 0
    if (-not [UserSessionLauncher]::ProcessIdToSessionId([uint32]$pi.dwProcessId, [ref]$launchedSessionId)) {
        Stop-Process -Id $pi.dwProcessId -Force -ErrorAction SilentlyContinue
        throw "Could not verify the launched process session for PID $($pi.dwProcessId): $([Runtime.InteropServices.Marshal]::GetLastWin32Error())"
    }
    if ([int]$launchedSessionId -ne [int]$explorer.SessionId) {
        Stop-Process -Id $pi.dwProcessId -Force -ErrorAction SilentlyContinue
        throw "Interactive launcher created PID $($pi.dwProcessId) in Session $launchedSessionId instead of Explorer Session $($explorer.SessionId)."
    }
    Write-Output "worker_id=$WorkerId launched_pid=$($pi.dwProcessId) launched_session=$launchedSessionId explorer_session=$($explorer.SessionId) desktop=$($si.lpDesktop)"
}
finally {
    if ($pi.hThread -ne [IntPtr]::Zero) { [UserSessionLauncher]::CloseHandle($pi.hThread) | Out-Null }
    if ($pi.hProcess -ne [IntPtr]::Zero) { [UserSessionLauncher]::CloseHandle($pi.hProcess) | Out-Null }
    if ($primary -ne [IntPtr]::Zero) { [UserSessionLauncher]::CloseHandle($primary) | Out-Null }
    if ($token -ne [IntPtr]::Zero) { [UserSessionLauncher]::CloseHandle($token) | Out-Null }
    if ($processHandle -ne [IntPtr]::Zero) { [UserSessionLauncher]::CloseHandle($processHandle) | Out-Null }
}
