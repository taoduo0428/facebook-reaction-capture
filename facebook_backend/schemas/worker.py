"""Worker and device schemas.

The worker list is part of ``GET /health``.  These models exist so a regression
in the shape of ``worker_health``/``adb_snapshot`` fails a test instead of
silently breaking the UI.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AndroidDeviceState(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: str
    boot_completed: bool | None = None
    proxy: str = ""
    proxy_error: str | None = None


class WorkerHealth(BaseModel):
    model_config = ConfigDict(extra="allow")

    worker_id: str
    enabled: bool | None = None
    serial: str | None = None
    avd_name: str | None = None
    capture_port: int | None = None
    native_port: int | None = None
    current_job_id: str | None = None
    cooldown_until: float | None = None
    android: AndroidDeviceState | dict[str, Any] | None = None


class HealthPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    service: str
    mode: str | None = None
    worker: str | None = None
    scheduler: dict[str, Any] | None = None
    queued_jobs: int | None = None
    cancelling_jobs: int | None = None
    active_worker_id: str | None = None
    serial: str | None = None
    android: dict[str, Any] | None = None
    capture_lease: dict[str, Any] | None = None
    workers: list[WorkerHealth] = []
