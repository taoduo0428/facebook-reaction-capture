"""Verified result schema.

The result payload is deliberately open (``extra="allow"``): it is produced by
the existing completeness/visibility verification code and gains fields as that
code grows.  The fields listed here are the ones the UI and clients rely on, so
a regression in *those* fails a test.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_dir: str | None = None
    result_outcome: str | None = None
    completion_scope: str | None = None
    target_verification: dict[str, Any] | None = None
    completeness: dict[str, Any] | None = None
    visibility: dict[str, Any] | None = None
    reaction_users: Any = None
    share_users: Any = None
    transport_observation: dict[str, Any] | None = None
    job_log: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    label: str | None = None
    available: bool | None = None
    size: int | None = None
    sha256: str | None = None
