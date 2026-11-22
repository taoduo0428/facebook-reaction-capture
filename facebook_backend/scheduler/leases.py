"""Device leases: exclusive ownership with a fencing token.

The database already guarantees "one active lease per device" (partial unique
index).  This module adds the **takeover rule**, which is deliberately stricter
than expiry:

> An expired lease is necessary but never sufficient to take a device over.
> The caller must also prove the previous capture process is gone *and* that its
> transport cleanup finished.

That is why :meth:`LeaseManager.acquire` takes :class:`TakeoverEvidence` instead
of trusting the clock.  A crashed scheduler therefore leaves the device blocked
until someone (or the recovery path) can actually verify the state — which is
exactly the behaviour the legacy ``capture-lease-uncertain`` guard had.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from facebook_backend.persistence.database import Database
from facebook_backend.persistence.repositories import (
    JobRepository,
    LeaseConflictError,
    LeaseRepository,
    RecordNotFoundError,
)


class LeaseError(Exception):
    """Base class for lease failures."""


class LeaseHeldError(LeaseError):
    """Another lease is active and cannot be taken over safely."""


class LeaseTakeoverRefused(LeaseError):
    """The lease looks expired but the takeover evidence is incomplete."""


@dataclass(frozen=True)
class TakeoverEvidence:
    """Proof required before an expired lease may be replaced."""

    process_stopped: bool = False
    cleanup_confirmed: bool = False

    @property
    def complete(self) -> bool:
        return self.process_stopped and self.cleanup_confirmed


def safe_to_reuse(process_state: str | None, cleanup_state: str | None) -> bool:
    """Only positive evidence for this attempt authorizes dispatch again."""
    return (process_state, cleanup_state) in {
        ("stopped", "restored"),
        ("not-started", "not-required"),
    }


def settled(job: dict[str, Any] | None) -> bool:
    return bool(job and job["status"] in {"queued", "cancelled", "failed", "completed"}
                and safe_to_reuse(job["process_state"], job["transport_cleanup_state"])
                and job.get("recovery_state") != "failed-result-persistence")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class LeaseManager:
    def __init__(
        self,
        database: Database,
        *,
        ttl_seconds: int = 900,
        clock: Callable[[], float] | None = None,
    ):
        self.database = database
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time

    # --- queries -------------------------------------------------------
    def active(self, device_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            return LeaseRepository(session).active(device_id)

    def active_leases(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            return LeaseRepository(session).list_active()

    def expired(self, lease: dict[str, Any], *, now: float | None = None) -> bool:
        expires = _parse_timestamp(lease.get("expires_at"))
        if expires is None:
            return False
        current = now if now is not None else self.clock()
        return expires.timestamp() <= current

    def takeover_allowed(
        self,
        lease: dict[str, Any] | None,
        evidence: TakeoverEvidence,
        *,
        now: float | None = None,
    ) -> tuple[bool, str]:
        if lease is None:
            return True, "no active lease"
        if not self.expired(lease, now=now):
            return False, "lease is still valid"
        if evidence.process_stopped is not True:
            return False, "previous capture process is not verified stopped"
        if evidence.cleanup_confirmed is not True:
            return False, "transport cleanup is not confirmed"
        return True, "expired lease with verified stop and cleanup"

    # --- mutations -----------------------------------------------------
    def acquire(
        self,
        device_id: str,
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
        evidence: TakeoverEvidence | None = None,
        ttl_seconds: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        evidence = evidence or TakeoverEvidence()
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        moment = datetime.fromtimestamp(
            now if now is not None else self.clock(), tz=timezone.utc
        )
        with self.database.session() as session:
            repo = LeaseRepository(session)
            current = repo.active(device_id)
            if current is not None:
                allowed, reason = self.takeover_allowed(current, evidence, now=now)
                if not allowed:
                    raise LeaseHeldError(f"device {device_id} lease cannot be taken over: {reason}")
                previous = JobRepository(session).get(current["job_id"]) if current["job_id"] else None
                if not settled(previous):
                    raise LeaseHeldError("previous holder has no persisted safe outcome")
                repo.release(current["lease_token"], reason="takeover-verified")
            try:
                return repo.acquire(
                    device_id=device_id,
                    job_id=job_id,
                    worker_id=worker_id,
                    ttl_seconds=ttl,
                    now=moment,
                )
            except LeaseConflictError as exc:
                # Lost a race with another scheduler between release and insert.
                raise LeaseHeldError(str(exc)) from exc

    def heartbeat(self, lease_token: str, *, ttl_seconds: int | None = None) -> dict[str, Any]:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        with self.database.session() as session:
            try:
                return LeaseRepository(session).heartbeat(lease_token, ttl_seconds=ttl)
            except RecordNotFoundError as exc:
                raise LeaseHeldError(str(exc)) from exc

    def release(self, lease_token: str, *, reason: str | None = None) -> dict[str, Any]:
        with self.database.session() as session:
            try:
                repo = LeaseRepository(session)
                holder = next((lease for lease in repo.list_active() if lease["lease_token"] == lease_token), None)
                job = JobRepository(session).get(holder["job_id"]) if holder and holder["job_id"] else None
                if not settled(job):
                    raise LeaseHeldError("lease has no persisted safe outcome")
                return repo.release(lease_token, reason=reason)
            except RecordNotFoundError as exc:
                raise LeaseHeldError(str(exc)) from exc


__all__ = [
    "LeaseError",
    "LeaseHeldError",
    "LeaseManager",
    "LeaseTakeoverRefused",
    "TakeoverEvidence",
]
