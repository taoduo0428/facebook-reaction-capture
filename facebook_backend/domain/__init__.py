"""Domain layer: statuses, errors and (later) transition rules."""
from __future__ import annotations

from facebook_backend.domain.exceptions import (
    ApiError,
    BackendError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    SchedulerStartError,
    ServiceUnavailableError,
    WorkerUnavailableError,
)
from facebook_backend.domain.statuses import (
    BATCH_STATUSES,
    COMPLETION_SCOPES,
    JOB_ACTIVE_STATUSES,
    JOB_STATUSES,
    PARTIAL_RESULT_OUTCOMES,
)

__all__ = [
    "ApiError",
    "BackendError",
    "BadRequestError",
    "ConflictError",
    "NotFoundError",
    "SchedulerStartError",
    "ServiceUnavailableError",
    "WorkerUnavailableError",
    "BATCH_STATUSES",
    "COMPLETION_SCOPES",
    "JOB_ACTIVE_STATUSES",
    "JOB_STATUSES",
    "PARTIAL_RESULT_OUTCOMES",
]
