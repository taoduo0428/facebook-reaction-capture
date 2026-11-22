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
from dataclasses import dataclass
from typing import Any, Callable

from facebook_backend.observability.logging import get_logger, log_event
from facebook_backend.observability.metrics import METRICS
from facebook_backend.persistence.database import Database
from facebook_backend.persistence.repositories import (
    JobRepository,
    LeaseRepository,
    EventRepository,
)
from facebook_backend.platform import process_manager
from facebook_backend.persistence.models import utc_now
from facebook_backend.scheduler.leases import safe_to_reuse
from facebook_backend.scheduler.queue import write_session

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
    job_version: int | None = None


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

    def _identity(self, job: dict[str, Any]) -> str:
        pid = process_manager.coerce_pid(job.get("process_pid"))
        if pid is None:
            # A claim may have committed immediately before a crash in execute().
            # An empty PID column says nothing about whether launch succeeded.
            return job.get("process_state") if job.get("process_state") in {"stopped", "not-started"} else "unknown"
        creation = process_manager.coerce_creation_time(job.get("process_creation_time"))
        try:
            return self.identity_probe(pid, creation)
        except Exception:
            return "unknown"

    def _actions(self, job_id, job, leases):
        version = job["version"] if job else None
        status = job["status"] if job else None
        identity = self._identity(job) if job else "unknown"
        stopped = "stopped" if identity == "absent" else identity
        safe = bool(job and safe_to_reuse(stopped, job["transport_cleanup_state"]))
        if (not job or len(leases) > 1 or status not in (*ACTIVE_STATUSES, *TERMINAL_STATUSES, "queued")
                or job.get("recovery_state") == "failed-result-persistence"):
            safe = False
        if job and leases and job.get("assigned_worker_id") and leases[0].get("worker_id"):
            safe = safe and job["assigned_worker_id"] == leases[0]["worker_id"]
        if not safe:
            reason = ("persisted capture process is still running" if identity == "match"
                      else "stop, transport cleanup, or ownership is unverified")
            return [RecoveryAction(job_id, ACTION_BLOCK, reason, lease["lease_token"] if lease else None,
                                   version) for lease in leases or [None]]
        actions = []
        if status in ACTIVE_STATUSES:
            action = ACTION_CANCEL if status == "cancelling" else ACTION_REQUEUE
            actions.append(RecoveryAction(job_id, action, "stop and cleanup verified", job_version=version))
        for lease in leases:
            actions.append(RecoveryAction(job_id, ACTION_RELEASE_LEASE, "stop and cleanup verified",
                                          lease["lease_token"], version))
        return actions

    @staticmethod
    def _report(actions, *, applied=False):
        def ids(kind):
            return tuple(dict.fromkeys(a.job_id for a in actions if a.action == kind))
        return RecoveryReport(actions=tuple(actions), requeued=ids(ACTION_REQUEUE),
                              cancelled=ids(ACTION_CANCEL), blocked=ids(ACTION_BLOCK),
                              released_leases=tuple(a.lease_token for a in actions
                                                    if a.action == ACTION_RELEASE_LEASE), applied=applied)

    def plan(self) -> RecoveryReport:
        with self.database.session() as session:
            repo = JobRepository(session)
            jobs = {job["id"]: job for job in repo.list_by_status(ACTIVE_STATUSES, limit=10_000)}
            leases_by_job = {}
            for lease in LeaseRepository(session).list_active():
                job_id = lease["job_id"]
                if job_id not in jobs:
                    jobs[job_id] = repo.get(job_id) if job_id else None
                leases_by_job.setdefault(job_id, []).append(lease)
            return self._report([action for key, job in jobs.items()
                                 for action in self._actions(key, job, leases_by_job.get(key, []))])

    def apply(self, report: RecoveryReport) -> RecoveryReport:
        # Plans are observations, not durable permission. Revalidate ownership,
        # version, identity and cleanup together under SQLite's write reservation.
        groups = {}
        for action in report.actions:
            groups.setdefault(action.job_id, []).append(action)
        actual = []
        for job_id, planned in groups.items():
            with write_session(self.database) as session:
                jobs, leases = JobRepository(session), LeaseRepository(session)
                job = jobs.get(job_id) if job_id else None
                holders = [lease for lease in leases.list_active() if lease["job_id"] == job_id]
                checked = self._actions(job_id, job, holders)
                if checked != planned:
                    actual.append(RecoveryAction(job_id, ACTION_LEAVE, "recovery plan changed; replan"))
                    continue
                for action in checked:
                    if action.action == ACTION_BLOCK:
                        if job and job["status"] in ACTIVE_STATUSES:
                            state = ("restart-process-still-running" if action.reason.endswith("still running")
                                     else "restart-unverified")
                            if job["recovery_state"] not in {state, "failed-result-persistence"}:
                                job = jobs.update(job_id, expected_version=job["version"], recovery_state=state)
                    elif action.action in {ACTION_REQUEUE, ACTION_CANCEL}:
                        # Keep the existing pin, including on restart-requeue.
                        queued = action.action == ACTION_REQUEUE
                        fields = (dict(status="queued", started_at=None, finished_at=None,
                                       assigned_worker_id=None, device_serial=None,
                                       recovery_state="requeued-after-restart") if queued else
                                  dict(status="cancelled", finished_at=utc_now(), recovery_state="cancelled"))
                        process_state = ("not-started" if job["process_pid"] is None and
                                         job["process_state"] == "not-started" else "stopped")
                        job = jobs.update(job_id, expected_version=job["version"], process_state=process_state,
                                          **fields)
                        EventRepository(session).append(event_type="restart-" + action.action, job_id=job_id)
                    elif action.action == ACTION_RELEASE_LEASE:
                        leases.release(action.lease_token, reason="restart-recovery")
            actual.extend(checked)
            for action in checked:
                METRICS.recoveries.inc(action=action.action)
                log_event(LOGGER, "recovery.action", job_id=job_id, action=action.action,
                          reason=action.reason, lease_token=action.lease_token)
        return self._report(actual, applied=True)

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
