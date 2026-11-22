"""Device identity schema.

Phase 1 keeps the legacy ``WorkerConfig`` as the source of truth; this model
describes the device-facing slice of it so the scheduler phase can move to a
device registry without changing the API layer.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DeviceIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_id: str
    worker_id: str | None = None
    serial: str
    avd_name: str | None = None
    capture_port: int | None = Field(default=None, ge=1, le=65535)
    native_port: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool = True
    health_status: str | None = None
    current_job_id: str | None = None
    cooldown_until: float | None = None
