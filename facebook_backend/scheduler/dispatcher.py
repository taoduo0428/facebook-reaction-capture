"""Dispatch planning: which device may take which job right now.

The dispatcher is pure decision logic — it starts nothing.  That makes the
isolation rules testable without a device:

1. a device runs at most one job (busy check + one slot per device),
2. a device is skipped while cooling down (rate limit / failure),
3. a device with an active lease is skipped unless takeover was verified,
4. two selected devices must not share a serial or a port,
5. ``max_concurrent_jobs`` caps the number of assignments per tick (default 1).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from facebook_backend.scheduler.models import Device


@dataclass(frozen=True)
class DispatchPlan:
    assignments: tuple[Device, ...] = ()
    blocked: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": [device.device_id for device in self.assignments],
            "blocked": dict(self.blocked),
        }


class Dispatcher:
    def __init__(
        self,
        *,
        max_concurrent_jobs: int = 1,
        require_healthy_device: bool = False,
        clock: Callable[[], float] | None = None,
    ):
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be at least 1")
        self.max_concurrent_jobs = max_concurrent_jobs
        self.require_healthy_device = require_healthy_device
        self.clock = clock or time.time

    def plan(
        self,
        devices: Sequence[Device],
        *,
        active_leases: Mapping[str, dict[str, Any]] | None = None,
        queue_depth: int,
        now: float | None = None,
    ) -> DispatchPlan:
        moment = now if now is not None else self.clock()
        leases = active_leases or {}
        blocked: dict[str, str] = {}
        selected: list[Device] = []

        if queue_depth <= 0:
            return DispatchPlan(assignments=(), blocked=blocked)

        for device in devices:
            if len(selected) >= self.max_concurrent_jobs:
                blocked[device.device_id] = "concurrency-limit"
                continue
            if not device.enabled:
                blocked[device.device_id] = "disabled"
                continue
            if device.current_job_id is not None:
                blocked[device.device_id] = "busy"
                continue
            if float(device.cooldown_until or 0.0) > moment:
                blocked[device.device_id] = "cooldown"
                continue
            if device.device_id in leases:
                # The lease layer decides takeover; the dispatcher never assumes.
                blocked[device.device_id] = "lease-held"
                continue
            if self.require_healthy_device and not device.healthy:
                blocked[device.device_id] = "unhealthy"
                continue
            conflict = self._conflict_with_selected(device, selected)
            if conflict:
                blocked[device.device_id] = conflict
                continue
            selected.append(device)

        return DispatchPlan(assignments=tuple(selected), blocked=blocked)

    @staticmethod
    def _conflict_with_selected(device: Device, selected: Sequence[Device]) -> str | None:
        for other in selected:
            problems = device.conflicts_with(other)
            if problems:
                return f"resource-conflict:{','.join(problems)}"
        return None


__all__ = ["DispatchPlan", "Dispatcher"]
