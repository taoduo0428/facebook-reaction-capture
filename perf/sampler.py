"""Dependency-free CPU/RSS sampler for a process id.

``psutil`` is not a project dependency and should not become one just for a
benchmark, so the two numbers the performance report needs are read directly:

* **Windows** — ``GetProcessTimes`` for CPU time, ``GetProcessMemoryInfo`` for
  the working set.
* **POSIX** — ``/proc/<pid>/stat`` (utime+stime) and ``/proc/<pid>/status``
  (VmRSS).

Both return ``None`` when the platform cannot answer instead of inventing a
number.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    cpu_seconds: float | None
    rss_bytes: int | None


def _windows_sample(pid: int) -> ProcessSample:
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    # GetProcessMemoryInfo needs QUERY_INFORMATION | VM_READ to report the real
    # working set; without VM_READ it succeeds but under-reports (measured).
    access = 0x0400 | 0x0010 | 0x1000
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return ProcessSample(pid, None, None)
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        cpu: float | None = None
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            kernel_s = ((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime) / 10_000_000
            user_s = ((user.dwHighDateTime << 32) | user.dwLowDateTime) / 10_000_000
            cpu = kernel_s + user_s
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        rss: int | None = None
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            rss = int(counters.WorkingSetSize)
        return ProcessSample(pid, cpu, rss)
    finally:
        kernel32.CloseHandle(handle)


def _posix_sample(pid: int) -> ProcessSample:
    cpu: float | None = None
    rss: int | None = None
    try:
        fields = open(f"/proc/{pid}/stat", encoding="utf-8").read().split()
        ticks = float(fields[13]) + float(fields[14])
        cpu = ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError, AttributeError):
        cpu = None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError, IndexError):
        rss = None
    return ProcessSample(pid, cpu, rss)


def sample(pid: int | None = None) -> ProcessSample:
    pid = pid or os.getpid()
    if sys.platform == "win32":
        try:
            return _windows_sample(pid)
        except (OSError, AttributeError, ValueError):
            return ProcessSample(pid, None, None)
    return _posix_sample(pid)


def format_mb(value: int | None) -> str:
    return "n/a" if value is None else f"{value / (1024 * 1024):.1f} MiB"


def resolve_listener_pid(port: int) -> int | None:
    """Find the process actually listening on ``port``.

    A Windows venv ``python.exe`` is a launcher that re-executes the base
    interpreter as a child, so the pid from ``Popen`` is *not* the server and its
    CPU/RSS are meaningless (measured: 4 MiB, 0 CPU while serving ~1k rps).
    Resolving the listener by port is the honest way to sample the right process.
    """
    import shutil
    import subprocess

    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)",
        ]
    else:
        lsof = shutil.which("lsof")
        if not lsof:
            return None
        command = [lsof, "-ti", f"tcp:{int(port)}", "-sTCP:LISTEN"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    for token in result.stdout.split():
        try:
            return int(token)
        except ValueError:
            continue
    return None


__all__ = ["ProcessSample", "format_mb", "resolve_listener_pid", "sample"]
