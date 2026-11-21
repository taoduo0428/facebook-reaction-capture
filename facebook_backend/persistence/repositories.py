"""Repositories: the only place that reads or writes tables.

Every method takes an open ``Session`` (from ``Database.session()``) and works
inside that unit of work.  Repositories never commit: the ``Database.session()``
context manager owns the transaction boundary, so a batch creation with its
child jobs is one atomic write.

Two correctness properties are enforced here rather than by convention:

* **Optimistic concurrency** — ``JobRepository.update`` takes an
  ``expected_version``; a stale writer gets :class:`ConcurrentUpdateError`
  instead of silently overwriting a newer status.
* **One active lease per device** — ``LeaseRepository.acquire`` relies on the
  partial unique index and translates the integrity error into
  :class:`LeaseConflictError`.  Expiry is never treated as permission to take
  over: the caller must still verify the old process and cleanup.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from facebook_backend.persistence.models import (
    Artifact,
    Batch,
    BatchJob,
    Device,
    DeviceLease,
    Job,
    JobAttempt,
    JobEvent,
    utc_now,
)


class RepositoryError(Exception):
    """Base class for repository-level failures."""


class DuplicateRecordError(RepositoryError):
    """The record already exists and the caller asked for a strict create."""


class RecordNotFoundError(RepositoryError):
    """The requested record does not exist."""


class ConcurrentUpdateError(RepositoryError):
    """The optimistic-concurrency token no longer matches."""


class LeaseConflictError(RepositoryError):
    """Another lease is still active for this device."""


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class JobRepository:
    def __init__(self, session: Session):
        self.session = session

    # --- reads ---------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.session.get(Job, job_id)
        return self.to_dict(row) if row else None

    def require(self, job_id: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record is None:
            raise RecordNotFoundError(f"job not found: {job_id}")
        return record

    def latest(self) -> dict[str, Any] | None:
        row = self.session.execute(
            select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(1)
        ).scalar_one_or_none()
        return self.to_dict(row) if row else None

    def count(self, status: str | Iterable[str] | None = None) -> int:
        statement = select(func.count()).select_from(Job)
        if status is not None:
            values = [status] if isinstance(status, str) else list(status)
            statement = statement.where(Job.status.in_(values))
        return int(self.session.execute(statement).scalar_one())

    def list_by_status(
        self, status: str | Sequence[str], *, limit: int = 100
    ) -> list[dict[str, Any]]:
        values = [status] if isinstance(status, str) else list(status)
        rows = self.session.execute(
            select(Job).where(Job.status.in_(values)).order_by(Job.created_at).limit(limit)
        ).scalars()
        return [self.to_dict(row) for row in rows]

    def list_all(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Every job, oldest first (used by the database-backed state store)."""
        statement = select(Job).order_by(Job.created_at, Job.id)
        if limit is not None:
            statement = statement.limit(int(limit))
        return [self.to_dict(row) for row in self.session.execute(statement).scalars()]

    def for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Job).where(Job.batch_id == batch_id).order_by(Job.batch_index)
        ).scalars()
        return [self.to_dict(row) for row in rows]

    # --- writes --------------------------------------------------------
    def create(
        self,
        *,
        job_id: str,
        status: str,
        payload: dict[str, Any],
        created_at: str | None = None,
        batch_id: str | None = None,
        batch_index: int | None = None,
        run_dir: str | None = None,
        capture_run_dir: str | None = None,
        device_serial: str | None = None,
        assigned_worker_id: str | None = None,
        completion_scope: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        cancel_requested_at: str | None = None,
        result: dict[str, Any] | None = None,
        attempt_count: int = 0,
        failover_count: int = 0,
        process_pid: int | None = None,
        process_creation_time: float | None = None,
        process_state: str | None = None,
        transport_cleanup_state: str | None = None,
        recovery_state: str | None = None,
        pinned_device_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or utc_now()
        row = Job(
            id=job_id,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
            started_at=started_at,
            finished_at=finished_at,
            cancel_requested_at=cancel_requested_at,
            payload=_dump(payload) or "{}",
            result=_dump(result),
            batch_id=batch_id,
            batch_index=batch_index,
            run_dir=run_dir,
            capture_run_dir=capture_run_dir,
            device_serial=device_serial,
            assigned_worker_id=assigned_worker_id,
            completion_scope=completion_scope,
            error_code=error_code,
            error_message=error_message,
            attempt_count=attempt_count,
            failover_count=failover_count,
            process_pid=process_pid,
            process_creation_time=process_creation_time,
            process_state=process_state,
            transport_cleanup_state=transport_cleanup_state,
            recovery_state=recovery_state,
            pinned_device_id=pinned_device_id,
        )
        if self.session.get(Job, job_id) is not None:
            raise DuplicateRecordError(f"job already exists: {job_id}")
        self.session.add(row)
        # Any other IntegrityError (for example a batch_id that does not exist)
        # must surface as-is instead of being mislabelled as a duplicate.
        self.session.flush()
        return self.to_dict(row)

    def update(        self,
        job_id: str,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Update a job; ``expected_version`` makes the write optimistic."""
        values = dict(fields)
        if "payload" in values:
            values["payload"] = _dump(values["payload"]) or "{}"
        if "result" in values:
            values["result"] = _dump(values["result"])
        values["updated_at"] = utc_now()
        values["version"] = Job.version + 1

        statement = update(Job).where(Job.id == job_id)
        if expected_version is not None:
            statement = statement.where(Job.version == expected_version)
        outcome = self.session.execute(statement.values(**values))
        if outcome.rowcount == 0:
            self.session.rollback()
            if self.session.get(Job, job_id) is None:
                raise RecordNotFoundError(f"job not found: {job_id}")
            raise ConcurrentUpdateError(
                f"job {job_id} was modified by another writer (expected version {expected_version})"
            )
        self.session.flush()
        return self.require(job_id)

    def set_status(
        self,
        job_id: str,
        status: str,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        return self.update(job_id, expected_version=expected_version, status=status, **fields)

    def backfill_batch_relation(self, batch_id: str, job_ids: Sequence[str]) -> int:
        """Set ``jobs.batch_id``/``batch_index`` when the manifest did not.

        Older manifests stored the relation only in the batch manifest, so the
        database backfills it from there; jobs that already carry a batch_id are
        left untouched.
        """
        updated = 0
        for position, job_id in enumerate(job_ids, start=1):
            outcome = self.session.execute(
                update(Job)
                .where(Job.id == job_id, Job.batch_id.is_(None))
                .values(
                    batch_id=batch_id,
                    batch_index=position,
                    updated_at=utc_now(),
                    version=Job.version + 1,
                )
            )
            updated += outcome.rowcount
        self.session.flush()
        return updated

    def record_attempt(
        self,
        *,
        job_id: str,
        attempt_index: int,
        worker_id: str | None = None,
        device_serial: str | None = None,
        process_id: int | None = None,
        process_creation_time: float | None = None,
        started_at: str | None = None,
        run_dir: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        row = JobAttempt(
            attempt_id=attempt_id or f"att_{uuid.uuid4().hex[:16]}",
            job_id=job_id,
            attempt_index=attempt_index,
            worker_id=worker_id,
            device_serial=device_serial,
            process_id=process_id,
            process_creation_time=process_creation_time,
            started_at=started_at or utc_now(),
            run_dir=run_dir,
        )
        self.session.add(row)
        self.session.flush()
        return {
            "attempt_id": row.attempt_id,
            "job_id": row.job_id,
            "attempt_index": row.attempt_index,
            "worker_id": row.worker_id,
            "device_serial": row.device_serial,
            "process_id": row.process_id,
            "started_at": row.started_at,
            "run_dir": row.run_dir,
        }

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        exit_code: int | None,
        result_status: str,
        finished_at: str | None = None,
    ) -> None:
        outcome = self.session.execute(
            update(JobAttempt)
            .where(JobAttempt.attempt_id == attempt_id)
            .values(
                exit_code=exit_code,
                result_status=result_status,
                finished_at=finished_at or utc_now(),
            )
        )
        if outcome.rowcount == 0:
            raise RecordNotFoundError(f"attempt not found: {attempt_id}")
        self.session.flush()

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_index)
        ).scalars()
        return [
            {
                "attempt_id": row.attempt_id,
                "attempt_index": row.attempt_index,
                "worker_id": row.worker_id,
                "device_serial": row.device_serial,
                "process_id": row.process_id,
                "process_creation_time": row.process_creation_time,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "exit_code": row.exit_code,
                "result_status": row.result_status,
                "run_dir": row.run_dir,
            }
            for row in rows
        ]

    @staticmethod
    def to_dict(row: Job) -> dict[str, Any]:
        return {
            "id": row.id,
            "status": row.status,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "cancel_requested_at": row.cancel_requested_at,
            "payload": _load(row.payload) or {},
            "result": _load(row.result),
            "error_code": row.error_code,
            "error_message": row.error_message,
            "assigned_worker_id": row.assigned_worker_id,
            "device_serial": row.device_serial,
            "pinned_device_id": row.pinned_device_id,
            "attempt_count": row.attempt_count,
            "failover_count": row.failover_count,
            "version": row.version,
            "batch_id": row.batch_id,
            "batch_index": row.batch_index,
            "completion_scope": row.completion_scope,
            "run_dir": row.run_dir,
            "capture_run_dir": row.capture_run_dir,
            "process_pid": row.process_pid,
            "process_creation_time": row.process_creation_time,
            "process_state": row.process_state,
            "transport_cleanup_state": row.transport_cleanup_state,
            "recovery_state": row.recovery_state,
        }


class BatchRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, batch_id: str) -> dict[str, Any] | None:
        row = self.session.get(Batch, batch_id)
        return self.to_dict(row) if row else None

    def require(self, batch_id: str) -> dict[str, Any]:
        record = self.get(batch_id)
        if record is None:
            raise RecordNotFoundError(f"batch not found: {batch_id}")
        return record

    def latest(self) -> dict[str, Any] | None:
        row = self.session.execute(
            select(Batch).order_by(Batch.created_at.desc(), Batch.id.desc()).limit(1)
        ).scalar_one_or_none()
        return self.to_dict(row) if row else None

    def count(self) -> int:
        return int(self.session.execute(select(func.count()).select_from(Batch)).scalar_one())

    def list_all(self, limit: int | None = None) -> list[dict[str, Any]]:
        statement = select(Batch).order_by(Batch.created_at, Batch.id)
        if limit is not None:
            statement = statement.limit(int(limit))
        return [self.to_dict(row) for row in self.session.execute(statement).scalars()]

    def update(self, batch_id: str, **fields: Any) -> dict[str, Any]:
        """Update mutable batch fields (run_dir, completion_scope, status...)."""
        if not fields:
            return self.require(batch_id)
        values = dict(fields)
        values["updated_at"] = utc_now()
        outcome = self.session.execute(
            update(Batch).where(Batch.id == batch_id).values(**values)
        )
        if outcome.rowcount == 0:
            self.session.rollback()
            raise RecordNotFoundError(f"batch not found: {batch_id}")
        self.session.flush()
        return self.require(batch_id)

    def job_ids(self, batch_id: str) -> list[str]:
        rows = self.session.execute(
            select(BatchJob.job_id).where(BatchJob.batch_id == batch_id).order_by(BatchJob.position)
        ).scalars()
        return list(rows)

    def create(
        self,
        *,
        batch_id: str,
        status: str,
        created_at: str | None = None,
        run_dir: str | None = None,
        completion_scope: str | None = None,
        total_jobs: int = 0,
    ) -> dict[str, Any]:
        timestamp = created_at or utc_now()
        batch = Batch(
            id=batch_id,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
            total_jobs=total_jobs,
            run_dir=run_dir,
            completion_scope=completion_scope,
        )
        if self.session.get(Batch, batch_id) is not None:
            raise DuplicateRecordError(f"batch already exists: {batch_id}")
        self.session.add(batch)
        self.session.flush()
        return self.to_dict(batch)

    def link_jobs(self, batch_id: str, job_ids: Sequence[str]) -> int:
        """Attach already-existing jobs to a batch (positions start at 1)."""
        existing = set(self.job_ids(batch_id))
        linked = 0
        for position, job_id in enumerate(job_ids, start=1):
            if job_id in existing:
                continue
            self.session.add(BatchJob(batch_id=batch_id, job_id=job_id, position=position))
            linked += 1
        self.session.flush()
        self.recompute_counts(batch_id)
        return linked

    def create_with_jobs(
        self,
        *,
        batch_id: str,
        status: str,
        jobs: Sequence[dict[str, Any]],
        created_at: str | None = None,
        run_dir: str | None = None,
        completion_scope: str | None = None,
    ) -> dict[str, Any]:
        """Create the batch, its jobs and the relations in one transaction.

        ``jobs`` entries are passed to :meth:`JobRepository.create` (``job_id``
        and ``status`` are required).  Any failure rolls the whole batch back —
        a half-written batch is exactly what the legacy manifest path had to
        guard against with "uncommitted child" checks.
        """
        timestamp = created_at or utc_now()
        if self.session.get(Batch, batch_id) is not None:
            raise DuplicateRecordError(f"batch already exists: {batch_id}")
        batch = Batch(
            id=batch_id,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
            total_jobs=len(jobs),
            run_dir=run_dir,
            completion_scope=completion_scope,
        )
        self.session.add(batch)
        self.session.flush()

        jobs_repo = JobRepository(self.session)
        for position, spec in enumerate(jobs, start=1):
            values = dict(spec)
            values.setdefault("status", "queued")
            values.setdefault("batch_id", batch_id)
            values.setdefault("batch_index", position)
            jobs_repo.create(created_at=timestamp, **values)
            self.session.add(BatchJob(batch_id=batch_id, job_id=values["job_id"], position=position))
        self.session.flush()
        self.recompute_counts(batch_id)
        return self.require(batch_id)

    def recompute_counts(self, batch_id: str) -> dict[str, Any]:
        rows = self.session.execute(select(Job.status).where(Job.batch_id == batch_id)).scalars()
        statuses = list(rows)
        completed = sum(1 for status in statuses if status == "completed")
        failed = sum(1 for status in statuses if status == "failed")
        cancelled = sum(1 for status in statuses if status == "cancelled")
        batch = self.session.get(Batch, batch_id)
        if batch is None:
            raise RecordNotFoundError(f"batch not found: {batch_id}")
        batch.total_jobs = len(statuses)
        batch.completed_jobs = completed
        batch.failed_jobs = failed
        batch.cancelled_jobs = cancelled
        if statuses and completed + failed + cancelled == len(statuses):
            batch.status = "completed_with_errors" if (failed or cancelled) else "completed"
        elif any(status in {"running", "cancelling"} for status in statuses):
            batch.status = "running"
        else:
            batch.status = "queued"
        batch.updated_at = utc_now()
        self.session.flush()
        return self.to_dict(batch)

    def set_status(self, batch_id: str, status: str) -> dict[str, Any]:
        batch = self.session.get(Batch, batch_id)
        if batch is None:
            raise RecordNotFoundError(f"batch not found: {batch_id}")
        batch.status = status
        batch.updated_at = utc_now()
        self.session.flush()
        return self.to_dict(batch)

    @staticmethod
    def to_dict(row: Batch) -> dict[str, Any]:
        return {
            "id": row.id,
            "status": row.status,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "total_jobs": row.total_jobs,
            "completed_jobs": row.completed_jobs,
            "failed_jobs": row.failed_jobs,
            "cancelled_jobs": row.cancelled_jobs,
            "run_dir": row.run_dir,
            "completion_scope": row.completion_scope,
        }


class ArtifactRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self,
        *,
        job_id: str,
        artifact_name: str,
        artifact_path: str,
        size: int = 0,
        sha256: str | None = None,
        content_type: str | None = None,
        verification_status: str = "unknown",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        row = self.session.execute(
            select(Artifact).where(
                Artifact.job_id == job_id, Artifact.artifact_name == artifact_name
            )
        ).scalar_one_or_none()
        if row is None:
            row = Artifact(
                job_id=job_id,
                artifact_name=artifact_name,
                artifact_path=artifact_path,
                size=size,
                sha256=sha256,
                content_type=content_type,
                created_at=created_at or utc_now(),
                verification_status=verification_status,
            )
            self.session.add(row)
        else:
            row.artifact_path = artifact_path
            row.size = size
            row.sha256 = sha256
            row.content_type = content_type
            row.verification_status = verification_status
        self.session.flush()
        return self.to_dict(row)

    def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Artifact).where(Artifact.job_id == job_id).order_by(Artifact.artifact_name)
        ).scalars()
        return [self.to_dict(row) for row in rows]

    def count(self) -> int:
        return int(self.session.execute(select(func.count()).select_from(Artifact)).scalar_one())

    @staticmethod
    def to_dict(row: Artifact) -> dict[str, Any]:
        return {
            "artifact_id": row.artifact_id,
            "job_id": row.job_id,
            "artifact_name": row.artifact_name,
            "artifact_path": row.artifact_path,
            "size": row.size,
            "sha256": row.sha256,
            "content_type": row.content_type,
            "created_at": row.created_at,
            "verification_status": row.verification_status,
        }


class EventRepository:
    """Append-only event log."""

    def __init__(self, session: Session):
        self.session = session

    def append(
        self,
        *,
        event_type: str,
        job_id: str | None = None,
        batch_id: str | None = None,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        row = JobEvent(
            job_id=job_id,
            batch_id=batch_id,
            event_type=event_type,
            timestamp=timestamp or utc_now(),
            payload=_dump(payload),
            trace_id=trace_id,
        )
        self.session.add(row)
        self.session.flush()
        return self.to_dict(row)

    def list_for_job(self, job_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id)
            .order_by(JobEvent.event_id)
            .limit(limit)
        ).scalars()
        return [self.to_dict(row) for row in rows]

    def count(self) -> int:
        return int(self.session.execute(select(func.count()).select_from(JobEvent)).scalar_one())

    @staticmethod
    def to_dict(row: JobEvent) -> dict[str, Any]:
        return {
            "event_id": row.event_id,
            "job_id": row.job_id,
            "batch_id": row.batch_id,
            "event_type": row.event_type,
            "timestamp": row.timestamp,
            "payload": _load(row.payload),
            "trace_id": row.trace_id,
        }


class DeviceRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert(
        self,
        *,
        device_id: str,
        worker_id: str,
        serial: str,
        avd_name: str | None = None,
        capture_port: int | None = None,
        native_port: int | None = None,
        enabled: bool = True,
        health_status: str | None = None,
        capabilities: dict[str, Any] | None = None,
        current_job_id: str | None = None,
        cooldown_until: float | None = None,
    ) -> dict[str, Any]:
        row = self.session.get(Device, device_id)
        if row is None:
            row = Device(id=device_id, worker_id=worker_id, serial=serial, updated_at=utc_now())
            self.session.add(row)
        row.worker_id = worker_id
        row.serial = serial
        row.avd_name = avd_name
        row.capture_port = capture_port
        row.native_port = native_port
        row.enabled = 1 if enabled else 0
        if health_status is not None:
            row.health_status = health_status
        if capabilities is not None:
            row.capabilities = _dump(capabilities)
        row.current_job_id = current_job_id
        row.cooldown_until = cooldown_until
        row.updated_at = utc_now()
        self.session.flush()
        return self.to_dict(row)

    def get(self, device_id: str) -> dict[str, Any] | None:
        row = self.session.get(Device, device_id)
        return self.to_dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        rows = self.session.execute(select(Device).order_by(Device.id)).scalars()
        return [self.to_dict(row) for row in rows]

    @staticmethod
    def to_dict(row: Device) -> dict[str, Any]:
        return {
            "device_id": row.id,
            "worker_id": row.worker_id,
            "serial": row.serial,
            "avd_name": row.avd_name,
            "capture_port": row.capture_port,
            "native_port": row.native_port,
            "enabled": bool(row.enabled),
            "health_status": row.health_status,
            "capabilities": _load(row.capabilities),
            "current_job_id": row.current_job_id,
            "cooldown_until": row.cooldown_until,
            "updated_at": row.updated_at,
        }


class LeaseRepository:
    def __init__(self, session: Session):
        self.session = session

    def active(self, device_id: str) -> dict[str, Any] | None:
        row = self.session.execute(
            select(DeviceLease).where(
                DeviceLease.device_id == device_id, DeviceLease.released_at.is_(None)
            )
        ).scalar_one_or_none()
        return self.to_dict(row) if row else None

    def acquire(
        self,
        *,
        device_id: str,
        lease_token: str | None = None,
        job_id: str | None = None,
        worker_id: str | None = None,
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        previous = self.session.execute(
            select(func.max(DeviceLease.fencing_version)).where(DeviceLease.device_id == device_id)
        ).scalar()
        row = DeviceLease(
            device_id=device_id,
            job_id=job_id,
            worker_id=worker_id,
            lease_token=lease_token or f"lease_{uuid.uuid4().hex[:16]}",
            fencing_version=int(previous or 0) + 1,
            acquired_at=current.isoformat().replace("+00:00", "Z"),
            heartbeat_at=current.isoformat().replace("+00:00", "Z"),
            expires_at=(current + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z"),
        )
        try:
            # SAVEPOINT: a conflict must not abort the caller's whole unit of
            # work (it may still hold other pending rows).  The row is added
            # inside the savepoint so a rollback discards it as well.
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            raise LeaseConflictError(f"device {device_id} already has an active lease") from exc
        return self.to_dict(row)

    def heartbeat(
        self, lease_token: str, *, ttl_seconds: int = 900, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        row = self.session.execute(
            select(DeviceLease).where(DeviceLease.lease_token == lease_token)
        ).scalar_one_or_none()
        if row is None or row.released_at is not None:
            raise RecordNotFoundError(f"active lease not found: {lease_token}")
        row.heartbeat_at = current.isoformat().replace("+00:00", "Z")
        row.expires_at = (current + timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
        self.session.flush()
        return self.to_dict(row)

    def release(self, lease_token: str, *, reason: str | None = None) -> dict[str, Any]:
        row = self.session.execute(
            select(DeviceLease).where(DeviceLease.lease_token == lease_token)
        ).scalar_one_or_none()
        if row is None:
            raise RecordNotFoundError(f"lease not found: {lease_token}")
        if row.released_at is None:
            row.released_at = utc_now()
            row.release_reason = reason
            self.session.flush()
        return self.to_dict(row)

    def list_active(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(DeviceLease).where(DeviceLease.released_at.is_(None)).order_by(DeviceLease.device_id)
        ).scalars()
        return [self.to_dict(row) for row in rows]

    @staticmethod
    def to_dict(row: DeviceLease) -> dict[str, Any]:
        return {
            "lease_id": row.id,
            "device_id": row.device_id,
            "job_id": row.job_id,
            "worker_id": row.worker_id,
            "lease_token": row.lease_token,
            "fencing_version": row.fencing_version,
            "acquired_at": row.acquired_at,
            "heartbeat_at": row.heartbeat_at,
            "expires_at": row.expires_at,
            "released_at": row.released_at,
            "release_reason": row.release_reason,
        }


def delete_all(session: Session) -> None:
    """Test helper: empty every table (FK-safe order)."""
    for model in (JobEvent, Artifact, JobAttempt, BatchJob, Job, Batch, DeviceLease, Device):
        session.execute(delete(model))
    session.flush()


__all__ = [
    "ArtifactRepository",
    "BatchRepository",
    "ConcurrentUpdateError",
    "DeviceRepository",
    "DuplicateRecordError",
    "EventRepository",
    "JobRepository",
    "LeaseConflictError",
    "LeaseRepository",
    "RecordNotFoundError",
    "RepositoryError",
    "delete_all",
]
