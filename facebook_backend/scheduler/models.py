"""Device-aware scheduler domain objects.

These are pure values: no I/O, no locks, no database.  The scheduler composes
them; the repositories persist them.  Keeping them frozen makes an assignment
auditable — once a job is bound to a device, the binding cannot be mutated in
place by another thread.

Vocabulary:

* **Device** — one Android target (serial + AVD + its two ports) plus the last
  observed health and cooldown.
* **ExecutionSlot** — "this device is currently running this job".  One slot per
  device; the slot owns the lease token.
* **WorkerHealth** — the last ADB observation.
* **WorkerAssignment** — the decision "job X goes to device Y".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from facebook_backend.scheduler.device_registry import WorkerConfig

#: ADB states that mean "the device is there and usable".
HEALTHY_STATES = frozenset({"device"})
#: ADB states where the target exists but is not ready.
TRANSIENT_STATES = frozenset({"offline", "unauthorized", "booting", "unknown"})


@dataclass(frozen=True)
class WorkerHealth:
    device_id: str
    state: str = "unknown"
    boot_completed: bool = False
    proxy: str = ""
    checked_at: float = 0.0

    @property
    def usable(self) -> bool:
        return self.state in HEALTHY_STATES and self.boot_completed


@dataclass(frozen=True)
class Device:
    device_id: str
    worker_id: str
    serial: str
    avd_name: str
    capture_port: int
    native_port: int
    enabled: bool = True
    frida_ssl_unpin: bool = False
    health_status: str = "unknown"
    boot_completed: bool = False
    current_job_id: str | None = None
    cooldown_until: float = 0.0

    @classmethod
    def from_worker_config(
        cls,
        worker: WorkerConfig,
        *,
        health: WorkerHealth | None = None,
        current_job_id: str | None = None,
        cooldown_until: float = 0.0,
    ) -> "Device":
        return cls(
            device_id=worker.worker_id,
            worker_id=worker.worker_id,
            serial=worker.serial,
            avd_name=worker.avd_name,
            capture_port=worker.capture_port,
            native_port=worker.native_port,
            enabled=worker.enabled,
            frida_ssl_unpin=worker.frida_ssl_unpin,
            health_status=health.state if health else "unknown",
            boot_completed=health.boot_completed if health else False,
            current_job_id=current_job_id,
            cooldown_until=cooldown_until,
        )

    @property
    def healthy(self) -> bool:
        return self.health_status in HEALTHY_STATES and self.boot_completed

    def is_available(self, now: float) -> bool:
        """Cheap availability gate; lease state is checked separately."""
        return (
            self.enabled
            and self.current_job_id is None
            and float(self.cooldown_until or 0.0) <= now
        )

    def conflicts_with(self, other: "Device") -> list[str]:
        """Resource collisions that make two devices unsafe to run together."""
        problems: list[str] = []
        if self.serial == other.serial:
            problems.append("serial")
        if self.capture_port == other.capture_port:
            problems.append("capture_port")
        if self.native_port == other.native_port:
            problems.append("native_port")
        if self.capture_port == other.native_port or self.native_port == other.capture_port:
            problems.append("cross_port")
        return problems


@dataclass(frozen=True)
class ExecutionSlot:
    device_id: str
    worker_id: str
    job_id: str
    lease_token: str
    fencing_version: int
    attempt_id: str | None = None
    started_at: str | None = None


@dataclass(frozen=True)
class WorkerAssignment:
    job_id: str
    device: Device
    request: dict[str, Any] = field(default_factory=dict)
    run_dir: str | None = None
    attempt: int = 0
    lease_token: str | None = None
    fencing_version: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class SchedulerSnapshot:
    """Read-only view used by health endpoints and tests."""

    max_concurrent_jobs: int
    devices: tuple[Device, ...] = ()
    slots: tuple[ExecutionSlot, ...] = ()
    queue_depth: int = 0
    blocked_reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "queue_depth": self.queue_depth,
            "devices": [
                {
                    "device_id": device.device_id,
                    "worker_id": device.worker_id,
                    "serial": device.serial,
                    "enabled": device.enabled,
                    "health_status": device.health_status,
                    "boot_completed": device.boot_completed,
                    "current_job_id": device.current_job_id,
                    "cooldown_until": device.cooldown_until,
                }
                for device in self.devices
            ],
            "slots": [
                {
                    "device_id": slot.device_id,
                    "worker_id": slot.worker_id,
                    "job_id": slot.job_id,
                    "lease_token": slot.lease_token,
                    "fencing_version": slot.fencing_version,
                }
                for slot in self.slots
            ],
            "blocked_reasons": dict(self.blocked_reasons),
        }


__all__ = [
    "HEALTHY_STATES",
    "TRANSIENT_STATES",
    "Device",
    "ExecutionSlot",
    "SchedulerSnapshot",
    "WorkerAssignment",
    "WorkerHealth",
]
