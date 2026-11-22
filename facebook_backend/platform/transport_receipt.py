"""Read a native runner's transport proof from its exclusively created attempt directory."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


RECEIPT_NAME = 'transport-cleanup-receipt.json'
_LIMIT = 32 * 1024


def _read_attempt_file(path: Path, started_at: float, now: float) -> str:
    # Never treat a link to another attempt (or a FIFO/device) as local evidence.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _LIMIT:
        raise ValueError('not an attempt evidence file')
    if not started_at <= info.st_mtime <= now + 1:
        raise ValueError('file outside current attempt')
    with path.open('rb') as handle:
        if not os.path.samestat(info, os.fstat(handle.fileno())):
            raise ValueError('file identity changed')
        data = handle.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError('oversized receipt')
    return data.decode('utf-8-sig')


def _windows_readback(receipt: dict, capture_port: int) -> bool:
    required = receipt['required_reverse_ports']
    observed = receipt['reverse_readback_ports']
    if not all(isinstance(ports, list) and all(type(p) is int and 0 < p <= 65535 for p in ports)
               for ports in (required, observed)):
        return False
    expected = {capture_port}
    normal_proxy = receipt['normal_proxy']
    if normal_proxy.startswith('127.0.0.1:'):
        expected.add(int(normal_proxy.split(':', 1)[1]))
    return (expected == set(required) == set(observed)
            and receipt['network_ready'] is True and receipt['ui_online'] is True
            and receipt['watchdog_owner'] == 'production' and receipt['watchdog_pid'] is None)


def _verified(directory: Path, *, job_id: str, attempt_id: str, serial: str,
              capture_port: int, platform: str, runner_pid: int, started_at: float,
              require_host: bool) -> bool:
    """Called after verified runner exit/result. Every missing or uncertain observation fails closed."""
    try:
        directory = Path(directory).absolute()
        if directory.resolve(strict=True) != directory or platform not in ('darwin', 'win32'):
            return False
        now = time.time()
        receipt = json.loads(_read_attempt_file(directory / RECEIPT_NAME, started_at, now))
        expected = {'version': 1, 'job_id': job_id, 'attempt_id': attempt_id, 'serial': serial,
                    'capture_port': capture_port, 'platform': platform, 'runner_pid': runner_pid,
                    'state': 'pending-watchdog' if platform == 'win32' else 'restored',
                    'adb_state': 'device', 'errors': []}
        if not isinstance(receipt, dict) or not job_id or not attempt_id or runner_pid <= 0:
            return False
        if any(type(receipt[key]) is not type(value) or receipt[key] != value for key, value in expected.items()):
            return False
        if Path(receipt['attempt_dir']) != directory:
            return False
        timestamp = datetime.fromisoformat(receipt['observed_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None or not started_at <= timestamp.timestamp() <= now + 1:
            return False
        normal = receipt['normal_proxy']
        if (not isinstance(normal, str) or not normal or normal != receipt['proxy_readback']
                or normal == f'127.0.0.1:{capture_port}'):
            return False
        if platform == 'win32':
            if not _windows_readback(receipt, capture_port):
                return False
            if not require_host:
                return True
            host = json.loads(_read_attempt_file(directory / HOST_RECEIPT, started_at, now))
            return (_watcher_running(host, serial, capture_port)
                    and all(host[key] == receipt[key] for key in ('job_id', 'attempt_id', 'runner_pid', 'attempt_dir')))
        owner = _read_attempt_file(directory / '.android-capture-owner', started_at, now).strip()
        status = json.loads(_read_attempt_file(directory / 'android-capture-status.json', started_at, now))
        return (bool(owner) and owner == receipt['run_id'] == status['run_id']
                and type(status['exit_code']) is int and status['exit_code'] == 0
                and status['cleanup_errors'] == [] and receipt['reverse_readback'] == 'absent')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RuntimeError):
        return False


HOST_RECEIPT = 'transport-watchdog-receipt.json'


def _watcher_running(host: dict, serial: str, capture_port: int) -> bool:
    return (isinstance(host, dict) and host.get('state') == 'watchdog-running'
            and host.get('serial') == serial and type(host.get('capture_port')) is int
            and host['capture_port'] == capture_port and type(host.get('watchdog_pid')) is int
            and type(host.get('helper_pid')) is int and host['helper_pid'] > 0 and host['watchdog_pid'] > 0
            and host['helper_pid'] != host['watchdog_pid'])


def verify_transport_receipt(directory: Path, **identity) -> bool:
    return _verified(directory, require_host=True, **identity)


def resume_windows_watchdog(directory: Path, *, job_id: str, attempt_id: str, serial: str,
                            capture_port: int, runner_pid: int, started_at: float) -> bool:
    """Only called by the scheduler after the owned capture Job is verifiably empty."""
    identity = dict(job_id=job_id, attempt_id=attempt_id, serial=serial, capture_port=capture_port,
                    platform='win32', runner_pid=runner_pid, started_at=started_at)
    directory = Path(directory)
    if not _verified(directory, require_host=False, **identity):
        return False
    try:
        helper = Path(__file__).resolve().parents[2] / 'facebook_resume_transport_watch.ps1'
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        command = [str(powershell), '-NoProfile', '-NonInteractive', '-File', str(helper),
                   '-Serial', serial, '-CapturePort', str(capture_port)]
        # Reserve the handoff once. A timeout or partial observation keeps the lease held.
        with (directory / HOST_RECEIPT).open('x', encoding='utf-8') as handle:
            result = subprocess.run(command, cwd=helper.parent, capture_output=True, timeout=30,
                                    stdin=subprocess.DEVNULL, check=True)
            host = json.loads(result.stdout)
            if not _watcher_running(host, serial, capture_port):
                return False
            host.update(job_id=job_id, attempt_id=attempt_id, runner_pid=runner_pid,
                        attempt_dir=str(directory.absolute()))
            json.dump(host, handle)
        return verify_transport_receipt(directory, **identity)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False
