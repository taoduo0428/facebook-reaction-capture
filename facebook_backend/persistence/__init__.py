"""Persistence: crash-safe file writes plus the SQLAlchemy database layer.

The submodules are imported explicitly rather than re-exported here on purpose:
``facebook_capture_api`` imports ``facebook_backend.persistence.atomic_write`` on
every start, and that must not drag SQLAlchemy (models/engine) into the legacy
process.  Use ``from facebook_backend.persistence.database import Database``.
"""
from __future__ import annotations

from facebook_backend.persistence import atomic_write
from facebook_backend.persistence.atomic_write import write_csv, write_json

__all__ = ["atomic_write", "write_csv", "write_json"]
