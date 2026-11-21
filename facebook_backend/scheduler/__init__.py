"""Scheduler layer.

Phase 2 ships the device registry (worker/device configuration).  The queue,
dispatcher, lease and failover modules land in Phase 4 together with the
device-aware scheduler; until then the in-process ``JobManager`` remains the
only scheduler and the registry is its configuration source.
"""
from __future__ import annotations

from facebook_backend.scheduler import device_registry
from facebook_backend.scheduler.device_registry import (
    WorkerConfig,
    default_worker,
    load_worker_config,
    validate_worker_config,
    write_worker_config,
)

__all__ = [
    "WorkerConfig",
    "default_worker",
    "device_registry",
    "load_worker_config",
    "validate_worker_config",
    "write_worker_config",
]
