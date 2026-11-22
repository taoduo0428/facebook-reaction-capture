"""Process identity and termination primitives.

Extracted verbatim from ``facebook_capture_api`` so the rules that keep a stale
capture process from being confused with a new one live in one place:

* **PID + creation time** is the identity.  A PID alone is not: Windows reuses
  PIDs, and the creation timestamp is what proves "this is still the process I
  started".
* ``creation_time`` only works on Windows today (``GetProcessTimes``).  On other
  platforms it returns ``None``, which the caller must treat as *unknown*, never
  as *absent*.  This is a known limitation recorded in the baseline audit.
* ``terminate_tree`` signals the whole process group/session first and falls
  back to the process handle; it never signals an unrelated group.
* ``presence`` returns ``None`` when the OS cannot tell us, so callers can keep
  a job blocked instead of guessing.

``facebook_capture_api`` keeps thin wrappers with the historical private names
(``_process_creation_time`` …) so existing call sites and tests keep patching the
same symbols.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from typing import Any, Callable


def creation_time(pid: int) -> float | None:
    """Process creation time in Unix seconds, or ``None`` when unavailable."""
    if pid <= 0 or os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return value / 10_000_000 - 11_644_473_600
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


#: Alias used inside :func:`identity_state`, whose ``creation_time`` parameter
#: would otherwise shadow the module-level function of the same name.
creation_time_of = creation_time


def presence(pid: int | None) -> bool | None:
    """Return whether a PID exists, or ``None`` when the OS cannot tell us."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        error = ctypes.get_last_error()
        if error in {5}:  # ERROR_ACCESS_DENIED still proves that a process exists.
            return True
        if error in {87, 1168}:  # invalid parameter / not found
            return False
        return None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def terminate_tree(process: subprocess.Popen[Any]) -> bool:
    """Terminate the process and its tree/session; ``True`` only when it is gone."""
    try:
        if process.poll() is not None:
            return True
        pid = int(process.pid)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    except (AttributeError, TypeError, ValueError):
        return False
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    else:
        # Capture runners start a new session, so signal the whole process
        # group on POSIX.  Fall back to the handle when a test/foreign caller
        # did not create a session leader; never signal an unrelated group.
        try:
            process_group = os.getpgid(pid)
        except (AttributeError, OSError, ProcessLookupError):
            process_group = None
        if process_group == pid:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                try:
                    if process.poll() is not None:
                        return True
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
                try:
                    process.terminate()
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
            except (AttributeError, OSError):
                try:
                    process.terminate()
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
        else:
            try:
                process.terminate()
            except ProcessLookupError:
                return True
            except OSError:
                return False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            if process.poll() is not None:
                return True
        except ProcessLookupError:
            return True
        except OSError:
            return False
        time.sleep(0.05)
    try:
        return process.poll() is not None
    except ProcessLookupError:
        return True
    except OSError:
        return False


def identity_state(
    pid: int | None,
    creation_time: float | None,
    *,
    creation_time_probe: Callable[[int], float | None] | None = None,
    presence_probe: Callable[[int | None], bool | None] | None = None,
) -> str:
    """``match`` / ``mismatch`` / ``absent`` / ``unknown`` for a persisted identity.

    ``unknown`` is a first-class answer: it means the OS could not prove either
    way, so callers must keep the job blocked instead of assuming it stopped.
    The probes are injectable so the legacy module can keep its patchable
    ``_process_creation_time`` / ``_process_presence`` symbols in the loop.
    """
    if not pid or pid <= 0:
        return "unknown"
    probe_creation = creation_time_probe or creation_time_of
    actual = probe_creation(int(pid))
    if actual is not None:
        if creation_time is None:
            return "unknown"
        return "match" if abs(actual - float(creation_time)) <= 2.0 else "mismatch"
    probe_presence = presence_probe or presence
    return "absent" if probe_presence(pid) is False else "unknown"


def coerce_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def coerce_creation_time(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


__all__ = [
    "coerce_creation_time",
    "coerce_pid",
    "creation_time",
    "creation_time_of",
    "identity_state",
    "presence",
    "terminate_tree",
]
