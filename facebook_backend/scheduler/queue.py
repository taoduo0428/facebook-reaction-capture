"""Persistent job queue backed by the ``jobs`` table.

Claiming is a **compare-and-swap**: the row is read with its ``version``, then
updated with ``WHERE id = ? AND version = ? AND status = 'queued'``.  If another
scheduler (or another thread) won the race, ``ConcurrentUpdateError`` is raised,
the candidate is re-read and the claim retried.  A job therefore cannot be
handed to two devices, even with several processes.

Rate-limit pinning lives in ``jobs.pinned_device_id``: a job pinned to device A
is invisible to device B, so a throttled job can never migrate to another
account/device to keep going.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, text

from facebook_backend.persistence.database import Database
from facebook_backend.persistence.models import DeviceLease, Job, utc_now
from facebook_backend.persistence.repositories import (
    ConcurrentUpdateError,
    EventRepository,
    JobRepository,
    LeaseRepository,
)


@contextmanager
def write_session(database: Database):
    """Take SQLite's writer reservation before checking mutable ownership."""
    with database.session() as session:
        if database.is_sqlite:
            session.execute(text("BEGIN IMMEDIATE"))
        yield session


class QueueError(Exception):
    """Base class for queue failures."""


class JobQueue:
    def __init__(self, database: Database, *, clock: Callable[[], float] | None = None):
        self.database = database
        self.clock = clock or time.time

    # --- reads ---------------------------------------------------------
    def depth(self) -> int:
        with self.database.session() as session:
            return JobRepository(session).count("queued")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            return JobRepository(session).get(job_id)

    def next_claimable(self, device_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            return self._select_candidate(session, device_id)

    @staticmethod
    def _select_candidate(session, device_id: str) -> dict[str, Any] | None:
        row = session.execute(
            select(Job)
            .where(Job.status == "queued")
            .where(~select(DeviceLease.id).where(
                DeviceLease.job_id == Job.id, DeviceLease.released_at.is_(None)
            ).exists())
            .where((Job.pinned_device_id.is_(None)) | (Job.pinned_device_id == device_id))
            .order_by(Job.created_at, Job.id)
            .limit(1)
        ).scalar_one_or_none()
        return JobRepository.to_dict(row) if row else None

    # --- writes --------------------------------------------------------
    def claim(
        self,
        *,
        device_id: str,
        worker_id: str,
        device_serial: str,
        max_conflict_retries: int = 5,
    ) -> dict[str, Any] | None:
        """Move one queued job to ``running`` for this device, or return ``None``."""
        for _ in range(max(1, max_conflict_retries)):
            try:
                with write_session(self.database) as session:
                    return self._claim(session, device_id, worker_id, device_serial)
            except ConcurrentUpdateError:
                continue
        return None

    def _claim(self, session, device_id, worker_id, device_serial):
        candidate = self._select_candidate(session, device_id)
        if candidate is None:
            return None
        return JobRepository(session).update(
            candidate["id"], expected_version=candidate["version"], status="running",
            started_at=utc_now(), finished_at=None, assigned_worker_id=worker_id,
            device_serial=device_serial, attempt_count=candidate["attempt_count"] + 1,
            process_pid=None, process_creation_time=None, process_state=None,
            transport_cleanup_state=None, recovery_state="running",
        )

    def claim_for_dispatch(self, device, *, capacity: int, ttl: int):
        """The claim, exclusive lease, and dispatch record become visible together."""
        with write_session(self.database) as session:
            leases = LeaseRepository(session)
            active = leases.list_active()
            if len(active) >= capacity or any(l["device_id"] == device.device_id for l in active):
                return None
            job = self._claim(session, device.device_id, device.worker_id, device.serial)
            if job is None:
                return None
            lease = leases.acquire(device_id=device.device_id, worker_id=device.worker_id,
                                   job_id=job["id"], ttl_seconds=ttl,
                                   now=datetime.fromtimestamp(self.clock(), tz=timezone.utc))
            EventRepository(session).append(event_type="dispatched", job_id=job["id"], payload={
                "device_id": device.device_id, "worker_id": device.worker_id,
                "serial": device.serial, "attempt": job["attempt_count"],
                "lease_token": lease["lease_token"], "fencing_version": lease["fencing_version"],
            })
            return job, lease

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with write_session(self.database) as session:
            repo = JobRepository(session)
            current = repo.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current["status"] not in {"queued", "running"}:
                return current
            queued = current["status"] == "queued"
            return repo.update(job_id, expected_version=current["version"],
                               status="cancelled" if queued else "cancelling",
                               finished_at=utc_now() if queued else None,
                               cancel_requested_at=utc_now(), recovery_state="cancellation-requested")

    def release(
        self,
        job_id: str,
        *,
        reason: str,
        error: str | None = None,
        pinned_device_id: Any = ...,
        recovery_state: str = "requeued",
    ) -> dict[str, Any]:
        """Put a job back in the queue (failover / rate-limit cooldown).

        ``pinned_device_id`` uses ``...`` as "keep the current pin" so an
        explicit ``None`` really clears it (failover to another device).
        """
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            pin = current["pinned_device_id"] if pinned_device_id is ... else pinned_device_id
            return repo.update(
                job_id,
                expected_version=current["version"],
                status="queued",
                started_at=None,
                finished_at=None,
                assigned_worker_id=None,
                device_serial=None,
                process_state=None,
                error_message=error,
                recovery_state=recovery_state,
                pinned_device_id=pin,
            )

    def complete(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        process_state: str | None = None,
    ) -> dict[str, Any]:
        """Record a terminal status.

        ``process_state`` is *not* invented here: only the executor (which holds
        the process handle and can verify it) may claim that a capture process
        stopped.  Passing ``None`` leaves the column untouched.
        """
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            fields: dict[str, Any] = {
                "status": status,
                "finished_at": utc_now(),
                "result": result,
                "error_message": error,
            }
            if process_state is not None:
                fields["process_state"] = process_state
            return repo.update(job_id, expected_version=current["version"], **fields)

    def mark_cancelling(self, job_id: str) -> dict[str, Any]:
        """Record a cancellation request; the job is *not* cancelled yet."""
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            if current["status"] not in {"queued", "running", "cancelling"}:
                return current
            return repo.update(
                job_id,
                expected_version=current["version"],
                status="cancelling",
                cancel_requested_at=utc_now(),
                recovery_state="cancellation-requested",
            )

    def mark_cancel_unverified(self, job_id: str, *, error: str | None) -> dict[str, Any]:
        """Keep ``cancelling`` when the stop could not be proven.

        This is the "a failed cancellation must not look like a success" rule:
        the status stays ``cancelling`` and the device stays blocked.
        """
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            return repo.update(
                job_id,
                expected_version=current["version"],
                status="cancelling",
                recovery_state="cancel-failed-stop-unverified",
                error_message=error,
            )

    def pin(self, job_id: str, device_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            return repo.update(
                job_id, expected_version=current["version"], pinned_device_id=device_id
            )

    def clear_pin(self, job_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            repo = JobRepository(session)
            current = repo.require(job_id)
            return repo.update(job_id, expected_version=current["version"], pinned_device_id=None)


__all__ = ["JobQueue", "QueueError"]
