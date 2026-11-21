"""Domain layer: statuses, errors and (later) transition rules."""
from __future__ import annotations

from facebook_backend.domain.exceptions import (
    BackendError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from facebook_backend.domain.statuses import (
    BATCH_STATUSES,
    COMPLETION_SCOPES,
    JOB_ACTIVE_STATUSES,
    JOB_STATUSES,
    PARTIAL_RESULT_OUTCOMES,
)

__all__ = [
    "BackendError",
    "BadRequestError",
    "ConflictError",
    "NotFoundError",
    "ServiceUnavailableError",
    "BATCH_STATUSES",
    "COMPLETION_SCOPES",
    "JOB_ACTIVE_STATUSES",
    "JOB_STATUSES",
    "PARTIAL_RESULT_OUTCOMES",
]
