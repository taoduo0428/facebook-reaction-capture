"""Batch schemas (documentation + response assertions)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from facebook_backend.domain.statuses import BATCH_STATUSES


class BatchSubmission(BaseModel):
    """Request body accepted by ``POST /v1/batches``."""

    model_config = ConfigDict(extra="allow")

    post_urls: list[str] = Field(min_length=1, max_length=100)
    collect: str | list[str] | None = None
    save_artifacts: str | list[str] | None = None
    reaction_scroll_limit: int | None = Field(default=None, ge=1, le=300)


class BatchProgress(BaseModel):
    model_config = ConfigDict(extra="allow")

    batch_id: str
    status: str
    created_at: str | None = None
    total_posts: int | None = None
    completed_posts: int | None = None
    failed_posts: int | None = None
    cancelled_posts: int | None = None
    completion_scope: str | None = None
    posts: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in BATCH_STATUSES:
            raise ValueError(f"unknown batch status: {value!r}")
        return value
