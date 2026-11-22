"""Pydantic v2 schemas for the HTTP contract."""
from __future__ import annotations

from facebook_backend.schemas.batch import BatchProgress, BatchSubmission
from facebook_backend.schemas.device import DeviceIdentity
from facebook_backend.schemas.job import JobPublic, JobSubmission
from facebook_backend.schemas.result import ArtifactMetadata, JobResult
from facebook_backend.schemas.worker import AndroidDeviceState, HealthPayload, WorkerHealth

__all__ = [
    "ArtifactMetadata",
    "AndroidDeviceState",
    "BatchProgress",
    "BatchSubmission",
    "DeviceIdentity",
    "HealthPayload",
    "JobPublic",
    "JobResult",
    "JobSubmission",
    "WorkerHealth",
]
