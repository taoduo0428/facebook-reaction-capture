"""Mock executor for scheduler tests.

No device, no subprocess, no ADB: the outcome is scripted per job id.  That is
what the task asks for — scheduler behaviour must be provable without touching
real devices ("使用 Mock Executor 测试调度器").
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from facebook_backend.executors.base import ExecutionOutcome
from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.models import WorkerAssignment


@dataclass
class MockExecutor:
    #: job_id -> outcome (or a callable receiving the assignment)
    script: Mapping[str, ExecutionOutcome | Callable[[WorkerAssignment], ExecutionOutcome]] = field(
        default_factory=dict
    )
    #: optional gate: execute() blocks until this event is set (concurrency tests)
    gate: threading.Event | None = None
    #: optional per-call delay in seconds
    delay: float = 0.0
    calls: list[tuple[str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def execute(self, assignment: WorkerAssignment, *, cancel_event: Any = None) -> ExecutionOutcome:
        with self._lock:
            self.calls.append((assignment.device.device_id, assignment.job_id))
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.delay:
            threading.Event().wait(self.delay)
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return cancelled(assignment.job_id)
        outcome = self.script.get(assignment.job_id)
        if outcome is None:
            return ExecutionOutcome(
                job_id=assignment.job_id,
                status="completed",
                result={"job_id": assignment.job_id, "device": assignment.device.device_id},
                process_state="stopped",
                transport_cleanup_state="restored",
            )
        if callable(outcome):
            return outcome(assignment)
        return outcome

    def calls_for(self, job_id: str) -> list[str]:
        with self._lock:
            return [device for device, job in self.calls if job == job_id]

    def devices_used(self) -> set[str]:
        with self._lock:
            return {device for device, _job in self.calls}


def completed(job_id: str, *, device: str | None = None) -> ExecutionOutcome:
    return ExecutionOutcome(
        job_id=job_id,
        status="completed",
        result={"job_id": job_id, "device": device},
        process_state="stopped",
        transport_cleanup_state="restored",
    )


def failed(job_id: str, failure: FailureClass, message: str, *, process_state: str | None = None, transport_cleanup_state: str | None = None) -> ExecutionOutcome:
    return ExecutionOutcome(
        job_id=job_id,
        status="failed",
        failure=failure,
        error=message,
        process_state=process_state,
        transport_cleanup_state=transport_cleanup_state,
    )


def cancelled(job_id: str) -> ExecutionOutcome:
    return ExecutionOutcome(job_id=job_id, status="cancelled", process_state="stopped", transport_cleanup_state="restored")


class CallRecorder:
    """Thread-safe call counter used by the isolation tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: list[tuple[str, str]] = []

    def add(self, device_id: str, job_id: str) -> None:
        with self._lock:
            self.entries.append((device_id, job_id))

    def by_device(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        with self._lock:
            for device_id, job_id in self.entries:
                grouped[device_id].append(job_id)
        return dict(grouped)


__all__ = ["CallRecorder", "MockExecutor", "cancelled", "completed", "failed"]
