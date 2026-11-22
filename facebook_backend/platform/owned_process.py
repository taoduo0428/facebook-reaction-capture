"""One Windows invocation and its anonymous, kernel-accounted Job Object.

The root joins the job *during* CreateProcessW (Windows 10+). There is no
unprotected launch fallback. No breakaway or kill-on-close flags are enabled.
A mixed-lifetime runner may own its root but only observe its descendants.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
from functools import lru_cache
import math
import os
import subprocess
import time


class _StartupInfo(C.Structure):
    _fields_ = [
        ('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
        ('x', W.DWORD), ('y', W.DWORD), ('x_size', W.DWORD), ('y_size', W.DWORD),
        ('x_chars', W.DWORD), ('y_chars', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
        ('show_window', W.WORD), ('reserved_size', W.WORD), ('reserved_ptr', C.c_void_p),
        ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE),
    ]


class _StartupInfoEx(C.Structure):
    _fields_ = [('info', _StartupInfo), ('attributes', C.c_void_p)]


class _ProcessInfo(C.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]


class _JobAccounting(C.Structure):
    _fields_ = [
        ('user_time', C.c_int64), ('kernel_time', C.c_int64),
        ('period_user_time', C.c_int64), ('period_kernel_time', C.c_int64),
        ('page_faults', W.DWORD), ('total', W.DWORD), ('active', W.DWORD), ('terminated', W.DWORD),
    ]


@lru_cache(maxsize=1)
def _kernel():
    dll = C.WinDLL('kernel32', use_last_error=True)
    ptr = C.c_void_p
    size = C.c_size_t
    specs = {
        'CreateJobObjectW': (W.HANDLE, [ptr, W.LPCWSTR]),
        'InitializeProcThreadAttributeList': (W.BOOL, [ptr, W.DWORD, W.DWORD, C.POINTER(size)]),
        'DeleteProcThreadAttributeList': (None, [ptr]),
        'UpdateProcThreadAttribute': (W.BOOL, [ptr, W.DWORD, size, ptr, size, ptr, ptr]),
        'CreateProcessW': (W.BOOL, [W.LPCWSTR, W.LPWSTR, ptr, ptr, W.BOOL, W.DWORD, ptr, W.LPCWSTR,
                                   C.POINTER(_StartupInfoEx), C.POINTER(_ProcessInfo)]),
        'QueryInformationJobObject': (W.BOOL, [W.HANDLE, C.c_int, ptr, W.DWORD, C.POINTER(W.DWORD)]),
        'TerminateJobObject': (W.BOOL, [W.HANDLE, W.UINT]),
    }
    for name, (result, args) in specs.items():
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = result, args
    return dll


def _check(ok):
    if not ok:
        raise C.WinError(C.get_last_error())
    return ok


def _stdio_handle(stream, mode, stack):
    import _winapi as win
    import msvcrt

    if stream == subprocess.DEVNULL:
        stream = stack.enter_context(open(os.devnull, mode))
    # Limit inheritance to explicit file handles. PIPE / pseudo handles are
    # intentionally outside the capture launch interface.
    fd = stream if isinstance(stream, int) else stream.fileno()
    source = msvcrt.get_osfhandle(fd)
    current = win.GetCurrentProcess()
    handle = win.DuplicateHandle(current, source, current, 0, True, win.DUPLICATE_SAME_ACCESS)
    stack.callback(win.CloseHandle, handle)
    return handle


def _env_block(env):
    if env is None:
        return None
    entries = []
    for key, value in sorted(env.items(), key=lambda item: item[0].upper()):
        if not key or '=' in key[1:] or '\0' in key or '\0' in value:
            raise ValueError('invalid Windows environment entry')
        entries.append(key + '=' + value)
    return C.create_unicode_buffer('\0'.join(entries) + '\0')


def _attribute_list(kernel, stack):
    size = C.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
    if C.get_last_error() != 122 or not size.value:  # ERROR_INSUFFICIENT_BUFFER
        raise C.WinError(C.get_last_error())
    attrs = C.create_string_buffer(size.value)
    _check(kernel.InitializeProcThreadAttributeList(attrs, 2, 0, C.byref(size)))
    stack.callback(kernel.DeleteProcThreadAttributeList, attrs)
    return attrs


class OwnedProcess:
    """Popen-like wait/poll for the root. active_processes() covers the tree."""

    @classmethod
    def start(cls, args, *, cwd=None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.STDOUT, start_new_session=False, creationflags=0, env=None, exclusive=False):
        if os.name != 'nt':
            raise OSError('Windows Job Objects require Windows')
        import _winapi as win

        if isinstance(args, (str, bytes)) or not args:
            raise ValueError('expected a non-empty argument list')
        argv = [os.fsdecode(arg) for arg in args]
        if any('\0' in arg for arg in argv) or (cwd is not None and '\0' in os.fsdecode(cwd)):
            raise ValueError('embedded null character in launch arguments')
        if creationflags & ~subprocess.CREATE_NO_WINDOW:
            raise ValueError('unsupported creation flags')
        # start_new_session is a POSIX-only Popen option and has no Win32 effect.
        command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = _env_block(env)
        kernel = _kernel()
        with ExitStack() as owners, ExitStack() as stack:
            job = subprocess.Handle(_check(kernel.CreateJobObjectW(None, None)))
            owners.callback(job.Close)
            info = _StartupInfoEx()
            info.info.cb = C.sizeof(info)
            info.info.flags = 0x100  # STARTF_USESTDHANDLES
            info.info.stdin = _stdio_handle(stdin, 'rb', stack)
            info.info.stdout = _stdio_handle(stdout, 'wb', stack)
            info.info.stderr = (info.info.stdout if stderr == subprocess.STDOUT
                                else _stdio_handle(stderr, 'wb', stack))
            handles = list(dict.fromkeys([info.info.stdin, info.info.stdout, info.info.stderr]))
            handle_array = (W.HANDLE * len(handles))(*handles)
            job_array = (W.HANDLE * 1)(int(job))
            attrs = _attribute_list(kernel, stack)
            # PROC_THREAD_ATTRIBUTE_HANDLE_LIST and PROC_THREAD_ATTRIBUTE_JOB_LIST.
            # These buffers must remain live until DeleteProcThreadAttributeList.
            for key, values in ((0x20002, handle_array), (0x2000D, job_array)):
                _check(kernel.UpdateProcThreadAttribute(attrs, 0, key, values, C.sizeof(values), None, None))
            info.attributes = C.cast(attrs, C.c_void_p)
            child = _ProcessInfo()
            flags = creationflags | 0x80000 | 0x400  # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT
            _check(kernel.CreateProcessW(None, command, None, None, True, flags, environment,
                                         os.fsdecode(cwd) if cwd is not None else None, C.byref(info), C.byref(child)))
            obj = cls()
            obj._process = subprocess.Handle(child.process)
            owners.callback(obj._process.Close)
            win.CloseHandle(child.thread)
            obj._job = job
            obj._exclusive = exclusive is True
            obj.args, obj.pid, obj.returncode = argv, child.pid, None
            owners.pop_all()  # Both handles now belong to the returned object.
            return obj

    def wait(self, timeout=None):
        import _winapi as win

        handle = self._process  # Keep the underlying handle alive across concurrent close().
        if handle is None:
            raise ValueError('process handle is closed')
        milliseconds = win.INFINITE if timeout is None else min(max(0, math.ceil(timeout * 1000)), 0xfffffffe)
        state = win.WaitForSingleObject(handle, milliseconds)
        if state == win.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args[0], timeout)
        if state != win.WAIT_OBJECT_0:
            raise OSError(f'unexpected process wait result {state}')
        self.returncode = win.GetExitCodeProcess(handle)
        return self.returncode

    def poll(self):
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None

    def active_processes(self) -> int | None:
        job = self._job
        if job is None:
            return None
        accounting = _JobAccounting()
        written = W.DWORD()
        try:
            # JobObjectBasicAccountingInformation counts all generations, even
            # when the originally launched parent has already exited.
            ok = _kernel().QueryInformationJobObject(job, 1, C.byref(accounting), C.sizeof(accounting), C.byref(written))
        except OSError:
            return None
        if not ok or written.value != C.sizeof(accounting) or accounting.total < 1 or accounting.active > accounting.total:
            return None
        return int(accounting.active)

    def terminate_root(self, timeout=2.0) -> bool:
        """Stop only the original process object. This result is not tree proof."""
        import _winapi as win

        handle = self._process
        if handle is None:
            return False
        try:
            if self.poll() is None:
                win.TerminateProcess(handle, 1)
            self.wait(timeout)
            return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def terminate_owned(self, timeout=2.0) -> bool:
        """Only callers that own *every* descendant can request job termination."""
        job = self._job
        if job is None or not self._exclusive:
            return False
        try:
            if not _kernel().TerminateJobObject(job, 1):
                return False
        except OSError:
            return False
        deadline = time.monotonic() + timeout
        while True:
            count = self.active_processes()
            if count == 0:
                return True
            remaining = deadline - time.monotonic()
            if count is None or remaining <= 0:
                return False
            time.sleep(min(.02, remaining))

    def close(self):
        """Drop observation authority. Closing either handle never kills a process."""
        self._job = self._process = None
