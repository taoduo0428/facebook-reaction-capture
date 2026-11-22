"""Scheduler layer.

Submodules (import them explicitly — this package deliberately does **not**
re-export them, to keep the import graph acyclic):

* ``device_registry`` — worker/device configuration (JSON stays the source of truth).
* ``models`` — Device, ExecutionSlot, WorkerHealth, WorkerAssignment.
* ``leases`` — exclusive device ownership, fencing, strict takeover rule.
* ``queue`` — persistent compare-and-swap queue with rate-limit pinning.
* ``dispatcher`` — pure decision logic (one job per device, cooldown, isolation).
* ``manager`` — composes the above with an executor.
* ``failures`` — failure classification and retry policy.

The in-process ``JobManager`` still owns the live dispatch path until the flip
described in docs/backend-modernization/PHASE-04-SCHEDULER.md.
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
