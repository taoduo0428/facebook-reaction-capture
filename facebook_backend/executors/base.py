"""Executor contract.

An executor runs one assignment and reports what actually happened.  Two rules
keep the scheduler honest:

* It only returns ``completed`` when the capture really produced a verified
  result — never because "the call returned".
* It reports ``process_state`` **only when it verified it**.  ``None`` means
  "unknown", and the scheduler must then keep the job blocked instead of
  pretending the process stopped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from facebook_backend.scheduler.failures import FailureClass
from facebook_backend.scheduler.models import WorkerAssignment

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class ExecutorError(RuntimeError):
    """The executor could not even start the work."""


@dataclass(frozen=True)
class ExecutionOutcome:
    job_id: str
    status: str
    failure: FailureClass | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    #: ``None`` means "not verified"; never invent "stopped".
    process_state: str | None = None
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid execution status: {self.status!r}")
        if self.status == "completed" and self.result is None:
            raise ValueError("a completed outcome must carry the verified result")


@runtime_checkable
class ExecutorPort(Protocol):
    def execute(self, assignment: WorkerAssignment, *, cancel_event: Any = None) -> ExecutionOutcome:
        ...


__all__ = [
    "ExecutionOutcome",
    "ExecutorError",
    "ExecutorPort",
    "TERMINAL_STATUSES",
]
