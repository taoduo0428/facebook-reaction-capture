"""Job schemas.

Two distinct jobs:

* ``JobSubmission`` documents the accepted request body in OpenAPI.  Actual
  validation still happens in ``facebook_capture_api.normalize_request`` during
  Phase 1, because the legacy normaliser accepts loose types (``post_id`` may be
  an int, ``collect`` may be a string) and returns specific Chinese/English
  error messages that clients already depend on.
* ``JobPublic`` mirrors the response so tests can assert the manager keeps
  emitting the documented fields.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from facebook_backend.domain.statuses import JOB_STATUSES


class JobSubmission(BaseModel):
    """Request body accepted by ``POST /v1/jobs``."""

    model_config = ConfigDict(extra="allow")

    post_url: str = Field(default="", description="Facebook post/reel/photo URL")
    post_id: str | int | None = Field(default=None, description="Explicit post id")
    collect: str | list[str] | None = Field(
        default=None, description="Subset of: reactions, comments, shares"
    )
    save_artifacts: str | list[str] | None = Field(
        default=None, description="Subset of: pcap, mitm, safe_capture, progress"
    )
    reaction_scroll_limit: int | None = Field(
        default=None, ge=1, le=300, description="Bounded sample size (1-300)"
    )


class JobPublic(BaseModel):
    """Response body of ``GET /v1/jobs/{job_id}``."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested_at: str | None = None
    request: dict[str, Any] | None = None
    completion_scope: str | None = None
    error: Any = None
    run_dir: str | None = None
    capture_run_dir: str | None = None
    attempt_dirs: list[str] = Field(default_factory=list)
    batch_id: str | None = None
    batch_index: int | None = None
    worker_id: str | None = None
    serial: str | None = None
    attempts: int = 0
    failover_count: int = 0
    process_pid: int | None = None
    process_creation_time: float | None = None
    process_state: str | None = None
    transport_cleanup_state: str | None = None
    partial_evidence_available: bool = False

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in JOB_STATUSES:
            raise ValueError(f"unknown job status: {value!r}")
        return value
