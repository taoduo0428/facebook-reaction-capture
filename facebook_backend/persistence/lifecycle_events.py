"""Transactional lifecycle journal for the SQLite job coordinator.

Call append_lifecycle_event with the Session that owns the job/attempt/lease
write, after its fenced status update. Let exceptions roll that transaction
back. The caller owns commit. Use an internal command or assignment key which
survives redelivery. The HTTP submission key belongs in the submission store.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from facebook_backend.domain.lifecycle import TERMINAL, JobLifecycle, LifecycleError, event_details
from facebook_backend.domain.statuses import JOB_STATUSES
from .models import Job, JobEvent, utc_now


class EventKeyConflict(LifecycleError):
    """A committed business key already identifies different event data."""


@dataclass(frozen=True)
class Replay:
    state: JobLifecycle
    stored_status: str | None
    event_count: int
    legacy_count: int
    ignored_legacy_count: int
    partial_history: bool
    invalid_event_id: int | None
    last_event_id: int | None
    last_event_type: str | None
    invalid_reason: str | None
    last_failure: str | None

    @property
    def matches_projection(self) -> bool:
        return (self.invalid_event_id is None and self.state.status is not None
                and self.stored_status == self.state.status)


_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.:_\[\]-]*\Z")
_TRACE = re.compile(r"(?!0{32}\Z)[0-9a-f]{32}\Z")
_LEGACY_OUTCOME = {
    "dispatched": "running", "retry-scheduled": "queued", "restart-requeue": "queued",
    "cancelled": "cancelled", "restart-cancel": "cancelled", "completed": "completed",
    "failed": "failed", "execution-unverified": "running", "cancel-unverified": "cancelling",
}


def _id(value: str, *, size: int = 64) -> None:
    if type(value) is not str or len(value) > size or not _ID.fullmatch(value):
        raise LifecycleError("invalid lifecycle identifier")


def _legacy_step(state: JobLifecycle, event: JobEvent, data: dict) -> JobLifecycle | None:
    """Extract historical status only. Never promote old diagnostics to proof."""
    if event.event_type in {"legacy-import", "job.created"}:
        status = data.get("status")
    elif event.event_type == "job.status_changed":
        if state.status is not None and data.get("from") != state.status:
            raise LifecycleError("legacy transition has a different predecessor")
        status = data.get("to")
    else:
        status = _LEGACY_OUTCOME.get(event.event_type)
        if status is None:
            return None
    if type(status) is not str or status not in JOB_STATUSES:
        raise LifecycleError("invalid legacy status")
    if ((state.status in TERMINAL and status != state.status)
            or (state.status == "cancelling" and status not in {"cancelled", "cancelling"})):
        raise LifecycleError("legacy event conflicts with cancellation or terminal status")
    attempt = data.get("attempt") if event.event_type == "dispatched" else None
    return JobLifecycle(status, str(attempt) if type(attempt) is int else state.attempt_id)


def replay_job_events(session: Session, job_id: str) -> Replay:
    """Fold every row in insertion order, including rows older than migration 0004.

    Old streams may lack a create/claim/cancel record. They remain readable,
    with partial_history set. On an invalid row stop at the last consistent state
    and return that row's id for diagnosis. Never return raw legacy payloads.
    """
    # Python's SQLite legacy transaction mode does not begin a read transaction
    # on SELECT. Keep the journal and projection at the same WAL snapshot.
    connection = session.connection()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    state = JobLifecycle()
    count = legacy = ignored = 0
    partial = False
    canonical_started = False
    previous_id = previous_type = invalid_id = invalid_reason = last_failure = None
    statement = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.event_id)
    rows = session.scalars(statement.execution_options(yield_per=256))
    for row in rows:
        count += 1
        try:
            data = json.loads(row.payload) if row.payload is not None else {}
            if not isinstance(data, dict):
                raise LifecycleError("invalid event payload")
            if row.event_key is None:
                legacy += 1
                # A legacy status writer after cutover could bypass attempt and
                # cleanup checks. Keep it visible for diagnosis, never bless it.
                next_state = _legacy_step(state, row, data)
                if next_state is None:
                    ignored += 1
                    partial = True
                elif canonical_started:
                    raise LifecycleError("legacy status written after lifecycle cutover")
                else:
                    partial = True
                    state = next_state
            else:
                before, after = data.pop("from"), data.pop("to")
                if state.status is None and before is not None:
                    # A previously unjournaled job can start its journal at the
                    # first fenced write. Report the missing prefix explicitly.
                    if canonical_started or before not in JOB_STATUSES:
                        raise LifecycleError("invalid initial event")
                    state = JobLifecycle(before)
                    partial = True
                state = state.advance(row.event_type, before=before, after=after,
                                      attempt_id=row.attempt_id, payload=data)
                canonical_started = True
                if "failure" in data:
                    last_failure = data["failure"]
            previous_id, previous_type = row.event_id, row.event_type
        except LifecycleError as exc:
            invalid_id, invalid_reason = row.event_id, str(exc)
            break
        except (KeyError, TypeError, ValueError):
            invalid_id, invalid_reason = row.event_id, "malformed lifecycle payload"
            break
    status = session.scalar(select(Job.status).where(Job.id == job_id))
    return Replay(state, status, count, legacy, ignored, partial or count == 0,
                  invalid_id, previous_id, previous_type, invalid_reason, last_failure)


def append_lifecycle_event(session: Session, *, job_id: str, event_key: str,
                           event_type: str, before: str | None, after: str,
                           batch_id: str | None = None, attempt_id: str | None = None,
                           worker_id: str | None = None, device_serial: str | None = None,
                           trace_id: str | None = None, payload: Mapping[str, Any] | None = None) -> JobEvent:
    """Append once per (job_id, event_key) within the caller's transaction.

    Idempotency covers type, transition, normalized payload and context. An
    exact retry returns the original row, even after later events. Its database
    timestamp is assigned only once. A conflicting retry raises EventKeyConflict.
    """
    _id(job_id)
    _id(event_key, size=128)
    for value in (batch_id, attempt_id, worker_id, device_serial):
        if value is not None:
            _id(value)
    if trace_id is not None and (type(trace_id) is not str or not _TRACE.fullmatch(trace_id)):
        raise LifecycleError("invalid W3C trace id")
    if (before is not None and (type(before) is not str or before not in JOB_STATUSES)
            or type(after) is not str or after not in JOB_STATUSES):
        raise LifecycleError("invalid lifecycle status")
    details = event_details(payload)
    encoded = json.dumps({"from": before, "to": after, **details}, separators=(",", ":"), sort_keys=True)
    fields = dict(event_type=event_type, batch_id=batch_id, attempt_id=attempt_id, worker_id=worker_id,
                  device_serial=device_serial, trace_id=trace_id, payload=encoded)

    # A normal CAS already holds the write reservation. Standalone observations
    # need the same serialized check + append, including concurrent redeliveries.
    connection = session.connection()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    prior = session.scalar(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.event_key == event_key))
    if prior is not None:
        if any(getattr(prior, field) != value for field, value in fields.items()):
            raise EventKeyConflict("event key already describes different data")
        if not replay_job_events(session, job_id).matches_projection:
            raise LifecycleError("duplicate delivery would leave the job and journal inconsistent")
        return prior

    history = replay_job_events(session, job_id)
    if history.invalid_event_id is not None:
        raise LifecycleError("job has an inconsistent event stream")
    if history.stored_status != after:
        raise LifecycleError("event disagrees with the job status in this transaction")
    state = history.state if history.state.status is not None else JobLifecycle(before)
    state.advance(event_type, before=before, after=after, attempt_id=attempt_id, payload=details)
    row = JobEvent(job_id=job_id, event_key=event_key, timestamp=utc_now(), **fields)
    session.add(row)
    session.flush()
    return row
