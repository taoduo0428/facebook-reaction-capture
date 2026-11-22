"""Lifecycle for a single native runner invocation. Callbacks are attempt-scoped."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.platform import process_manager
from facebook_backend.scheduler.failures import classify_failure


class NativeAttempt:
    def __init__(self, scheduler, claim, worker):
        self.scheduler = scheduler
        self.claim = claim
        self.worker = worker
        self.handles = []
        self.owned_process = None
        self.started_at = None
        self.result_verified = False
        self.tree_stopped = set()
        self.transport_started = False
        self.runner_handle = None
        self.sealed = False
        self.cancelled = False
        self.metadata_failed = False
        self.lock = threading.RLock()

    def _record(self, **fields):
        # Both callbacks are inside the runner's spawn handoff. A failed write
        # must not prevent it registering the handle or entering wait/finally.
        try:
            self.scheduler.record_callback(self.claim, **fields)
        except Exception:
            self.metadata_failed = True

    def runner_started(self):
        with self.lock:
            if self.sealed:
                return
            self.transport_started = True
            self._record(transport_cleanup_state='pending')

    def register(self, process):
        with self.lock:
            if self.sealed or process is None:
                return
            self.handles.append(process)
            if self.transport_started:
                self.runner_handle = process
            if self.cancelled:
                self._stop(process)
            self._record(process_pid=process.pid, process_creation_time=process_manager.creation_time(process.pid),
                         process_state='running')

    def spawn(self, command, **kwargs):
        from facebook_backend.platform.owned_process import OwnedProcess
        process = OwnedProcess.start(command, exclusive=False, **kwargs)
        self.owned_process = process
        return process

    def _stop(self, process):
        if process.poll() is not None:
            return
        if sys.platform == 'win32':
            # Bootstrap can own shared emulator and ADB infrastructure.
            if self.owned_process is not None and process is self.owned_process:
                process.terminate_root()
        elif process_manager.terminate_tree(process):
            self.tree_stopped.add(process)

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if not self.sealed:
                for process in self.handles:
                    self._stop(process)

    def proof(self, api):
        with self.lock:
            self.sealed = True
            if self.metadata_failed or self.runner_handle is None or not self.handles:
                return None, None
            if any(p.poll() is None for p in self.handles):
                return None, 'pending'
            if sys.platform == 'win32':
                if (self.owned_process is None or self.runner_handle is not self.owned_process
                        or self.owned_process.active_processes() != 0):
                    return None, 'pending'
            elif any(p not in self.tree_stopped and process_manager.presence(p.pid) is not False for p in self.handles):
                return None, 'pending'
        if self.result_verified and not self.cancelled and self.runner_handle.returncode == 0:
            from facebook_backend.platform.transport_receipt import verify_transport_receipt
            a = self.claim.assignment
            if verify_transport_receipt(Path(a.run_dir), job_id=a.job_id, attempt_id=self.claim.attempt_id,
                    serial=self.worker.serial, capture_port=self.worker.capture_port, platform=sys.platform,
                    runner_pid=self.runner_handle.pid, started_at=self.started_at):
                return 'stopped', 'restored'
        return 'stopped', 'pending'

    def execute(self, adapter, cancel_event):
        import facebook_capture_api as api
        a = self.claim.assignment
        directory = Path(a.run_dir)
        result = None
        error = None
        if cancel_event.is_set():
            return ExecutionOutcome(a.job_id, 'cancelled', process_state='not-started',
                                    transport_cleanup_state='not-required', attempt_id=self.claim.attempt_id)
        try:
            # Refuse a stale directory before the native runner can reuse prior
            # artifacts or cleanup receipts after an import or interrupted copy.
            directory.mkdir(parents=True, exist_ok=False)
            self.started_at = time.time()
            adapter.run_attempt(a.request, a.job_id, directory, self.worker, process_callback=self.register,
                                capture_process_callback=self.runner_started, transport_attempt_id=self.claim.attempt_id,
                                process_factory=self.spawn if sys.platform == 'win32' else None)
            # Keep the established combined-attempt result validation at the API
            # boundary, including evidence/visibility and deduplication rules.
            if not cancel_event.is_set():
                root = Path(self.claim.job['run_dir'])
                attempts = [Path(path) for path in self.claim.job['attempt_dirs']]
                api.materialize_capture_attempts(root, attempts)
                result = api.build_api_result(root, a.request, evidence_dirs=attempts)
                result['job_log'] = str(result.get('job_log') or directory / 'capture.log')
                self.result_verified = True
        except Exception as exc:
            error = exc
        try:
            process_state, cleanup_state = self.proof(api)
        finally:
            if self.owned_process is not None:
                self.owned_process.close()
        status = 'cancelled' if cancel_event.is_set() else ('failed' if error is not None else 'completed')
        return ExecutionOutcome(a.job_id, status, failure=classify_failure(error) if error else None,
                                error=type(error).__name__ if error is not None else None,
                                result=result, process_state=process_state, transport_cleanup_state=cleanup_state,
                                attempt_id=self.claim.attempt_id)
