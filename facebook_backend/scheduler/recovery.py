"""Startup recovery for the persistent queue and device leases.

A crash leaves three kinds of debris behind:

1. jobs still marked ``running`` (or ``cancelling``) whose process may or may
   not exist,
2. device leases that were never released,
3. results whose persistence failed.

The rules mirror the ones the legacy manifest loader already enforced, because
they are the ones that were proven correct:

* **requeue only when the old process is verifiably gone** — ``absent`` or an
  explicit ``stopped`` state.  A ``match`` (still running) or ``unknown``
  identity keeps the job blocked; it is never silently re-run.
* **a cancelled job is only terminal when the stop is proven** — a
  ``cancelling`` job with a live/unverified process stays ``cancelling``.
* **a lease is released only with the same proof** — otherwise the device stays
  blocked, which is what stops a second capture from starting on it.

``plan()`` is pure (read-only) so the recovery decision can be inspected in a
report or a test; ``apply()`` performs it in one transaction per job.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from facebook_backend.observability.logging import get_logger, log_event
from facebook_backend.observability.metrics import METRICS
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.repositories import (
    JobRepository,
    LeaseRepository,
    RecordNotFoundError,
)
from facebook_backend.platform import process_manager
from facebook_backend.scheduler.queue import JobQueue

LOGGER = get_logger("recovery")

ACTIVE_STATUSES = ("running", "cancelling")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

ACTION_REQUEUE = "requeue"
ACTION_CANCEL = "cancel"
ACTION_BLOCK = "block"
ACTION_RELEASE_LEASE = "release-lease"
ACTION_LEAVE = "leave"


@dataclass(frozen=True)
class RecoveryAction:
    job_id: str
    action: str
    reason: str
    lease_token: str | None = None


@dataclass(frozen=True)
class RecoveryReport:
    actions: tuple[RecoveryAction, ...] = ()
    requeued: tuple[str, ...] = ()
    cancelled: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    released_leases: tuple[str, ...] = ()
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "requeued": list(self.requeued),
            "cancelled": list(self.cancelled),
            "blocked": list(self.blocked),
            "released_leases": list(self.released_leases),
            "actions": [
                {
                    "job_id": action.job_id,
                    "action": action.action,
                    "reason": action.reason,
                    "lease_token": action.lease_token,
                }
                for action in self.actions
            ],
        }


class RecoveryPlanner:
    def __init__(
        self,
        database: Database,
        *,
        identity_probe: Callable[[int | None, float | None], str] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.database = database
        self.identity_probe = identity_probe or process_manager.identity_state
        self.clock = clock or time.time

    # --- planning ------------------------------------------------------
    def plan(self) -> RecoveryReport:
        actions: list[RecoveryAction] = []
        requeued: list[str] = []
        cancelled: list[str] = []
        blocked: list[str] = []
        released: list[str] = []

        with self.database.session() as session:
            jobs_repo = JobRepository(session)
            leases_repo = LeaseRepository(session)
            active_jobs = jobs_repo.list_by_status(ACTIVE_STATUSES, limit=10_000)
            active_leases = leases_repo.list_active()

        by_id = {job["id"]: job for job in active_jobs}
        for job in active_jobs:
            identity = self._identity(job)
            job_id = job["id"]
            if identity == "match":
                blocked.append(job_id)
                actions.append(
                    RecoveryAction(job_id, ACTION_BLOCK, "persisted capture process is still running")
                )
                continue
            if identity == "unknown":
                blocked.append(job_id)
                actions.append(
                    RecoveryAction(
                        job_id, ACTION_BLOCK, "persisted capture process identity could not be verified"
                    )
                )
                continue
            if job["status"] == "cancelling":
                cancelled.append(job_id)
                actions.append(
                    RecoveryAction(job_id, ACTION_CANCEL, "stop verified after restart; cancellation is terminal")
                )
            else:
                requeued.append(job_id)
                actions.append(
                    RecoveryAction(job_id, ACTION_REQUEUE, "stop verified after restart; job is queued again")
                )

        for lease in active_leases:
            job_id = lease.get("job_id")
            job = by_id.get(job_id) if job_id else None
            terminal = False
            if job_id and job is None:
                with self.database.session() as session:
                    stored = JobRepository(session).get(job_id)
                terminal = stored is None or stored["status"] in TERMINAL_STATUSES
            identity = self._identity(job) if job is not None else "absent"
            if terminal and identity in {"absent", "none"}:
                released.append(lease["lease_token"])
                actions.append(
                    RecoveryAction(
                        str(job_id),
                        ACTION_RELEASE_LEASE,
                        "lease belongs to a finished job whose process is gone",
                        lease_token=lease["lease_token"],
                    )
                )
            elif job is not None and identity in {"absent", "none"}:
                released.append(lease["lease_token"])
                actions.append(
                    RecoveryAction(
                        str(job_id),
                        ACTION_RELEASE_LEASE,
                        "lease holder is stopped; device can be reused",
                        lease_token=lease["lease_token"],
                    )
                )
            else:
                actions.append(
                    RecoveryAction(
                        str(job_id),
                        ACTION_BLOCK,
                        "lease kept: the old process is running or unverified",
                        lease_token=lease["lease_token"],
                    )
                )

        return RecoveryReport(
            actions=tuple(actions),
            requeued=tuple(requeued),
            cancelled=tuple(cancelled),
            blocked=tuple(dict.fromkeys(blocked)),
            released_leases=tuple(released),
        )

    def _identity(self, job: dict[str, Any] | None) -> str:
        if job is None:
            return "none"
        pid = process_manager.coerce_pid(job.get("process_pid"))
        creation = process_manager.coerce_creation_time(job.get("process_creation_time"))
        if pid is None:
            return "stopped" if job.get("process_state") == "stopped" else "absent"
        try:
            return self.identity_probe(pid, creation)
        except (OSError, TypeError, ValueError):
            return "unknown"

    # --- applying ------------------------------------------------------
    def apply(self, report: RecoveryReport) -> RecoveryReport:
        queue = JobQueue(self.database, clock=self.clock)
        for action in report.actions:
            METRICS.recoveries.inc(action=action.action)
            log_event(
                LOGGER,
                "recovery.action",
                job_id=action.job_id,
                action=action.action,
                reason=action.reason,
                lease_token=action.lease_token,
            )
            if action.action == ACTION_REQUEUE:
                queue.release(
                    action.job_id,
                    reason="restart-recovery",
                    recovery_state="requeued-after-restart",
                    pinned_device_id=None,
                )
            elif action.action == ACTION_CANCEL:
                queue.complete(
                    action.job_id,
                    status="cancelled",
                    process_state="stopped",
                    error=None,
                )
            elif action.action == ACTION_BLOCK:
                with self.database.session() as session:
                    repo = JobRepository(session)
                    job = repo.get(action.job_id)
                    if job is not None and job["status"] in ACTIVE_STATUSES:
                        state = (
                            "restart-process-still-running"
                            if action.reason.endswith("still running")
                            else "restart-unverified"
                        )
                        repo.update(
                            action.job_id,
                            expected_version=job["version"],
                            recovery_state=state,
                        )
            elif action.action == ACTION_RELEASE_LEASE and action.lease_token:
                with self.database.session() as session:
                    try:
                        LeaseRepository(session).release(
                            action.lease_token, reason="restart-recovery"
                        )
                    except RecordNotFoundError:
                        pass
        return RecoveryReport(
            actions=report.actions,
            requeued=report.requeued,
            cancelled=report.cancelled,
            blocked=report.blocked,
            released_leases=report.released_leases,
            applied=True,
        )

    def run(self) -> RecoveryReport:
        return self.apply(self.plan())


__all__ = [
    "ACTION_BLOCK",
    "ACTION_CANCEL",
    "ACTION_LEAVE",
    "ACTION_RELEASE_LEASE",
    "ACTION_REQUEUE",
    "ACTIVE_STATUSES",
    "RecoveryAction",
    "RecoveryPlanner",
    "RecoveryReport",
]
