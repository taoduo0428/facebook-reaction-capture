"""Identity checks and bounded cleanup of the capture runner's process group.

Persisted birth times are Unix seconds. JSON and SQLite REAL round-trip the
same binary float, so a time tolerance would only authorize other generations.
"""
from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator


_STOP_TIMEOUT = 2.0
_PROBE_ERRORS = (OSError, RuntimeError, TypeError, ValueError, OverflowError)


def coerce_pid(value: Any) -> int | None:
    # In particular, never truncate 12.5, treat True as PID 1, or wrap a DWORD.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        pid = int(value)
    except ValueError:
        return None
    limit = 0xffffffff if os.name == "nt" else 0x7fffffff
    return pid if 0 < pid <= limit else None


def coerce_creation_time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        timestamp = float(value)
    except (OverflowError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) and timestamp > 0 else None


@contextmanager
def _windows_handle(pid: int) -> Iterator[int]:
    import _winapi

    # QUERY_LIMITED_INFORMATION | SYNCHRONIZE. Keeping the handle open pins
    # the process object and its PID throughout identity checks and taskkill.
    try:
        handle = _winapi.OpenProcess(0x1000 | 0x100000, False, pid)
    except OSError as exc:
        if exc.winerror in (87, 1168):  # invalid PID / not found
            raise ProcessLookupError(pid) from exc
        raise
    try:
        yield handle
    finally:
        _winapi.CloseHandle(handle)


def _windows_running(handle: int) -> bool:
    import _winapi

    state = _winapi.WaitForSingleObject(handle, 0)
    if state == _winapi.WAIT_TIMEOUT:
        return True
    if state == _winapi.WAIT_OBJECT_0:
        return False
    raise OSError(f"unexpected process wait result {state}")


def _windows_birth(handle: int) -> float:
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    get_times = ctypes.WinDLL("kernel32", use_last_error=True).GetProcessTimes
    get_times.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    get_times.restype = wintypes.BOOL
    if not get_times(handle, *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    ticks = times[0].dwHighDateTime << 32 | times[0].dwLowDateTime
    # Preserve the *order* as well as the units of the legacy conversion.
    return ticks / 10_000_000 - 11_644_473_600


def creation_time(pid: int) -> float | None:
    """Kernel birth time in Unix seconds, including the subsecond component."""
    pid = coerce_pid(pid)
    if pid is None:
        return None
    try:
        if os.name == "nt":
            with _windows_handle(pid) as handle:
                return _windows_birth(handle) if _windows_running(handle) else None
        # On Darwin psutil reads sysctl's kinfo_proc.p_starttime timeval.
        # Construct a fresh Process for each read (create_time itself is cached).
        import psutil

        try:
            return coerce_creation_time(psutil.Process(pid).create_time())
        except psutil.Error:
            return None
    except (ImportError, AttributeError, NotImplementedError, *_PROBE_ERRORS):
        return None


creation_time_of = creation_time  # Historical import / injectable probe.


def _group_present(pgid: int) -> bool | None:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # Includes EPERM, which cannot verify cleanup.
        return None
    return True


def presence(pid: int | None) -> bool | None:
    """False requires an absent PID and, on POSIX, no residual runner group."""
    pid = coerce_pid(pid)
    if pid is None:
        return None
    try:
        if os.name == "nt":
            with _windows_handle(pid) as handle:
                return _windows_running(handle)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        if os.name != "nt" and _group_present(pid) is not False:
            return None
        return False
    except _PROBE_ERRORS:
        return None


def identity_state(
    pid: int | None,
    creation_time: float | None,
    *,
    creation_time_probe: Callable[[int], float | None] | None = None,
    presence_probe: Callable[[int | None], bool | None] | None = None,
) -> str:
    """Return match, mismatch, absent or unknown using the facade's probes."""
    pid = coerce_pid(pid)
    if pid is None:
        return "unknown"
    try:
        actual = (creation_time_probe or creation_time_of)(pid)
        if actual is None:
            exists = (presence_probe or presence)(pid)
            return "absent" if exists is False else "unknown"
        birth = coerce_creation_time(actual)
        expected = coerce_creation_time(creation_time)
        if birth is None or expected is None:
            return "unknown"
        return "match" if birth == expected else "mismatch"
    except _PROBE_ERRORS:
        return "unknown"


def _terminate_windows(pid: int, handle: int, expected: float) -> bool:
    import _winapi

    if not _windows_running(handle) or _windows_birth(handle) != expected:
        return False
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return False
    command = os.path.join(system_root, "System32", "taskkill.exe")
    result = subprocess.run(
        [command, "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10, check=False,
    )
    return (result.returncode == 0 and
            _winapi.WaitForSingleObject(handle, int(_STOP_TIMEOUT * 1000)) == _winapi.WAIT_OBJECT_0)


def _terminate_posix_group(
    pid: int, still_matches: Callable[[], bool], stopped: Callable[[], bool],
) -> bool:
    # Production starts a new session. Foreign / inherited groups do not
    # establish ownership. A single-PID fallback could leave children running.
    try:
        if os.getpgid(pid) != pid or os.getsid(pid) != pid:
            return False
        # POSIX killpg takes a number, not a stable group handle. Re-read the
        # generation immediately before the one signal; never escalate by PGID
        # after the leader has exited or during polling.
        if not still_matches():
            return False
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # ESRCH on one operation doesn't prove that both have exited.
    deadline = time.monotonic() + _STOP_TIMEOUT
    while True:
        if stopped() and _group_present(pid) is False:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def terminate_known_pid(
    pid: int | None, creation_time: float | None, *,
    identity_probe: Callable[[int | None, float | None], str] | None = None,
) -> bool:
    """Stop only the verified generation of a persisted capture runner."""
    pid = coerce_pid(pid)
    if pid in (None, 1, os.getpid()):
        return False
    probe = identity_probe or identity_state
    expected = coerce_creation_time(creation_time)
    try:
        state = probe(pid, expected)
        if state == "absent":
            return True
        if state != "match" or expected is None:
            return False
        if os.name == "nt":
            with _windows_handle(pid) as handle:
                return _terminate_windows(pid, handle, expected)
        return _terminate_posix_group(
            pid, lambda: probe(pid, expected) == "match",
            lambda: probe(pid, expected) == "absent",
        )
    except (*_PROBE_ERRORS, subprocess.SubprocessError):
        return False


def terminate_tree(process: subprocess.Popen[Any]) -> bool:
    """Stop a runner launched by this instance, using its live Popen handle."""
    try:
        pid = coerce_pid(process.pid)
        if pid in (None, 1, os.getpid()):
            return False
        if process.poll() is not None:
            return os.name == "nt" or _group_present(pid) is False
        if os.name == "nt":
            # Borrow the existing Popen handle. Popen owns and closes it.
            handle = int(process._handle)
            return _terminate_windows(pid, handle, _windows_birth(handle))
        expected = creation_time_of(pid)
        if expected is None:
            return False
        return _terminate_posix_group(
            pid, lambda: process.poll() is None and identity_state(pid, expected) == "match",
            lambda: process.poll() is not None,
        )
    except (AttributeError, *_PROBE_ERRORS, subprocess.SubprocessError):
        return False


__all__ = [
    "coerce_creation_time", "coerce_pid", "creation_time", "creation_time_of",
    "identity_state", "presence", "terminate_known_pid", "terminate_tree",
]
