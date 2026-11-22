"""Artifact catalogue, path boundary and metadata."""
from __future__ import annotations

from facebook_backend.artifacts import catalog, manager
from facebook_backend.artifacts.manager import (
    artifact_metadata,
    checked_artifact_path,
    required_artifacts_ready,
)

__all__ = [
    "artifact_metadata",
    "catalog",
    "checked_artifact_path",
    "manager",
    "required_artifacts_ready",
]
