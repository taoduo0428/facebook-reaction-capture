"""Executor adapter for the existing capture path.

This is the bridge that keeps the validated execution code in charge: the
scheduler decides *where* and *when*, ``facebook_capture_api.run_capture_job``
still decides *how* (PowerShell on Windows, Python on macOS, ADB lifecycle,
process identity, transport cleanup, result verification).

The adapter never fabricates a "stopped" process state:

* success — the runner returned after observing exit code 0, so the process is
  verifiably gone;
* any failure — ``process_state=None`` (unknown).  The recovery path must verify
  the identity before the device can be reused.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from facebook_backend.executors.base import ExecutionOutcome, ExecutorError
from facebook_backend.scheduler.device_registry import WorkerConfig
from facebook_backend.scheduler.failures import classify_failure
from facebook_backend.scheduler.models import Device, WorkerAssignment


def _worker_config_for(device: Device) -> WorkerConfig:
    return WorkerConfig(
        worker_id=device.worker_id,
        serial=device.serial,
        avd_name=device.avd_name,
        capture_port=device.capture_port,
        native_port=device.native_port,
        enabled=device.enabled,
        frida_ssl_unpin=device.frida_ssl_unpin,
    )


class LegacyCaptureExecutor:
    """Runs one assignment through ``run_capture_job``.

    ``run_job`` is injectable so the adapter itself can be tested without a
    device; production uses the real function.
    """

    def __init__(
        self,
        run_job: Callable[..., dict[str, Any]] | None = None,
        *,
        timeout_seconds: int | None = None,
        base_dir: Path | None = None,
    ):
        self._run_job = run_job
        self.timeout_seconds = timeout_seconds
        self.base_dir = base_dir

    def _resolve(self) -> Callable[..., dict[str, Any]]:
        if self._run_job is not None:
            return self._run_job
        import facebook_capture_api as api

        return api.run_capture_job

    def run_attempt(self, request, job_id, run_dir, worker, *, process_callback, capture_process_callback,
                    process_factory=None, transport_attempt_id=None):
        """Preserve the native signature and both handoffs for the selected attempt."""
        launch = {}
        if process_factory is not None:
            launch['process_factory'] = process_factory
        if transport_attempt_id is not None:
            launch['transport_attempt_id'] = transport_attempt_id
        return self._resolve()(request, job_id, worker.serial, timeout_seconds=self.timeout_seconds,
                               process_callback=process_callback, capture_process_callback=capture_process_callback,
                               run_dir=run_dir, worker=worker, **launch)

    def execute(self, assignment: WorkerAssignment, *, cancel_event: Any = None) -> ExecutionOutcome:
        run_job = self._resolve()
        run_dir = None
        if assignment.run_dir:
            run_dir = Path(assignment.run_dir)
            if self.base_dir is not None and not run_dir.is_absolute():
                run_dir = self.base_dir / run_dir
        try:
            result = run_job(
                dict(assignment.request),
                assignment.job_id,
                serial=assignment.device.serial,
                timeout_seconds=self.timeout_seconds,
                run_dir=run_dir,
                worker=_worker_config_for(assignment.device),
            )
        except ExecutorError:
            raise
        except BaseException as exc:  # noqa: BLE001 - classified, never swallowed
            return ExecutionOutcome(
                job_id=assignment.job_id,
                status="failed",
                failure=classify_failure(exc),
                error=f"{type(exc).__name__}: {exc}",
                process_state=None,
            )
        if not isinstance(result, dict):
            return ExecutionOutcome(
                job_id=assignment.job_id,
                status="failed",
                failure=classify_failure("executor returned no result"),
                error="capture runner returned no result document",
                process_state="stopped",
            )
        return ExecutionOutcome(
            job_id=assignment.job_id,
            status="completed",
            result=result,
            process_state="stopped",
        )


__all__ = ["LegacyCaptureExecutor"]
