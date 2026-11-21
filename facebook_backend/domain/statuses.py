"""Canonical lifecycle vocabularies.

These constants are *descriptive*: the legacy ``JobManager`` remains the only
writer of job/batch state in Phase 1, and this module exists so routers,
schemas and documentation share one vocabulary instead of repeating string
literals.  ``tests/test_backend_fastapi.py`` asserts that the vocabulary still
matches the legacy module so drift is caught immediately.

Important semantic note carried over from the existing implementation:
``partial`` is **not** a job status.  A job that finished early with a bounded
sample stays ``completed`` and reports ``completion_scope="bounded-sample"``
(plus a per-result outcome), so the UI keeps showing real completion semantics
instead of inventing a new state.
"""
from __future__ import annotations

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_CANCELLING = "cancelling"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

JOB_STATUSES = frozenset(
    {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_CANCELLING,
        JOB_STATUS_CANCELLED,
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
    }
)

#: Statuses where a capture subprocess may still exist.  Nothing may be
#: requeued, cancelled-as-done or treated as "stopped" while a job is in one of
#: these states without an explicit process-identity check.
JOB_ACTIVE_STATUSES = frozenset({JOB_STATUS_RUNNING, JOB_STATUS_CANCELLING})

BATCH_STATUS_QUEUED = "queued"
BATCH_STATUS_RUNNING = "running"
BATCH_STATUS_COMPLETED = "completed"
BATCH_STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"

BATCH_STATUSES = frozenset(
    {
        BATCH_STATUS_QUEUED,
        BATCH_STATUS_RUNNING,
        BATCH_STATUS_COMPLETED,
        BATCH_STATUS_COMPLETED_WITH_ERRORS,
    }
)

COMPLETION_SCOPE_TERMINAL = "terminal"
COMPLETION_SCOPE_BOUNDED_SAMPLE = "bounded-sample"

COMPLETION_SCOPES = frozenset({COMPLETION_SCOPE_TERMINAL, COMPLETION_SCOPE_BOUNDED_SAMPLE})

#: Result outcome labels that mean "capture finished but not at a terminal
#: end-of-list".  They must never be reported as complete enumeration.
PARTIAL_RESULT_OUTCOMES = frozenset({"partial-open-tail", "bounded-sample"})
