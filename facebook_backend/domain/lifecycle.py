"""Pure job lifecycle rules shared by transaction writers and journal replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .statuses import JOB_STATUSES


class LifecycleError(ValueError):
    """An event would contradict the recorded lifecycle or its evidence."""


ACTIVE = {"running", "cancelling"}
TERMINAL = {"completed", "cancelled", "failed"}


def _stay(states):
    return {(state, state) for state in states}


# Each event describes an operation, including observations which do not move
# the public status. There is deliberately no arbitrary set-status operation.
_EDGES = {
    "job.created": {(None, "queued")},
    "job.queued": {("queued", "queued")},
    "job.claimed": {("queued", "running")},
    "job.started": {("running", "running")},
    "job.progress": _stay(ACTIVE),
    "job.worker_failed": _stay(ACTIVE),
    "job.failover": {("queued", "queued"), ("running", "queued")},
    "job.retry_scheduled": {("running", "queued")},
    "job.cancellation_requested": {("running", "cancelling"), ("queued", "cancelled")},
    "job.process_stopped": _stay(ACTIVE),
    "job.cleanup_completed": _stay(ACTIVE),
    "job.artifact_verified": _stay({"running", "completed"}),
    "job.completed": {("running", "completed")},
    "job.failed": {("running", "failed"), ("queued", "failed")},
    "job.cancelled": {("queued", "cancelled"), ("running", "cancelled"), ("cancelling", "cancelled")},
    "job.recovered": _stay(JOB_STATUSES) | {("running", "queued"), ("cancelling", "cancelled")},
    "job.shutdown": _stay(JOB_STATUSES),
    "job.result_write_failed": _stay(ACTIVE) | {("running", "failed")},
}
EVENT_TYPES = frozenset(_EDGES)

# Fixed fields AND bounded scalar values: no generic message/reason/dict field
# through which a URL, exception, request body or lease token can be journaled.
_ENUMS = {
    "failure": {"rate-limit", "device", "network", "executor", "timeout", "cancelled", "unknown", "storage"},
    "process_state": {"stopped", "not-started", "running", "cancelling", "stop-unverified", "unknown"},
    "transport_cleanup_state": {"restored", "not-required", "pending", "failed", "runner-owned", "unknown"},
    "completion_scope": {"terminal", "bounded-sample"},
}
_COUNTS = {"attempt", "fencing_version", "cooldown_seconds", "artifact_count", "progress_count"}


def event_details(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise LifecycleError("event payload must be a mapping")
    result = dict(payload)
    for name, value in result.items():
        if name in _ENUMS:
            valid = type(value) is str and value in _ENUMS[name]
        elif name in _COUNTS:
            valid = type(value) is int and 0 <= value < 2**63
        elif name == "same_device":
            valid = type(value) is bool
        else:
            valid = False
        if not valid:
            raise LifecycleError("unexpected lifecycle payload field or value")
    return result


@dataclass(frozen=True)
class JobLifecycle:
    status: str | None = None
    attempt_id: str | None = None
    # A failed result write is a durable hold. Shutdown/observations do not
    # authorize a late success, recovery requeue or another attempt.
    result_write_failed: bool = False
    attempt_failed: bool = False
    shutdown: bool = False
    claimed_attempts: frozenset[str] = frozenset()

    def advance(self, event_type: str, *, before: str | None, after: str,
                attempt_id: str | None = None, payload: Mapping[str, Any] | None = None) -> JobLifecycle:
        details = event_details(payload)
        if before != self.status or (before, after) not in _EDGES.get(event_type, ()):
            raise LifecycleError("invalid lifecycle transition or stale previous status")
        if self.result_write_failed and (before != after or event_type == "job.started"):
            raise LifecycleError("result persistence hold requires offline reconciliation")
        if self.shutdown and event_type in {"job.claimed", "job.started", "job.retry_scheduled", "job.failover"}:
            raise LifecycleError("dispatch is closed until recovery")
        if self.attempt_failed and event_type in {"job.started", "job.progress", "job.completed"}:
            raise LifecycleError("failed attempt cannot report new execution or success")
        if event_type == "job.claimed":
            if not attempt_id or attempt_id == self.attempt_id or attempt_id in self.claimed_attempts:
                raise LifecycleError("claim requires a new attempt id")
        elif self.attempt_id and attempt_id and attempt_id != self.attempt_id:
            raise LifecycleError("event belongs to another attempt")
        elif before in ACTIVE and not attempt_id and self.attempt_id and event_type not in {
            "job.shutdown", "job.cancellation_requested", "job.recovered",
        }:
            raise LifecycleError("active event is missing its attempt id")
        if before in ACTIVE and after in TERMINAL | {"queued"} and event_type != "job.result_write_failed":
            if (details.get("process_state"), details.get("transport_cleanup_state")) not in {
                ("stopped", "restored"), ("not-started", "not-required"),
            }:
                raise LifecycleError("attempt has no verified stop and transport cleanup")
        if event_type == "job.process_stopped" and details.get("process_state") not in {"stopped", "not-started"}:
            raise LifecycleError("process stop event needs positive evidence")
        if event_type == "job.cleanup_completed" and details.get("transport_cleanup_state") not in {"restored", "not-required"}:
            raise LifecycleError("cleanup event needs positive evidence")
        claimed_attempts = self.claimed_attempts
        if event_type == "job.claimed":
            claimed_attempts = claimed_attempts.union({attempt_id})
        shutdown = self.shutdown or (event_type == "job.shutdown" and before not in TERMINAL)
        if event_type == "job.recovered" and after == "queued":
            shutdown = False
        return JobLifecycle(
            status=after,
            attempt_id=attempt_id or self.attempt_id,
            result_write_failed=self.result_write_failed or event_type == "job.result_write_failed",
            attempt_failed=(self.attempt_failed or event_type == "job.worker_failed") and event_type != "job.claimed",
            shutdown=shutdown,
            claimed_attempts=claimed_attempts,
        )
