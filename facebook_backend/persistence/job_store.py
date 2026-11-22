"""Persistence seam for job and batch state.

The runtime state owner is a **store**, not a file format.  ``JobManager`` only
ever calls four methods — ``load_job_manifests``, ``save_job``,
``load_batch_manifests``, ``save_batch`` — so switching the owner from JSON
manifests to SQLite cannot touch recovery, cancellation or process logic.

Two implementations:

* :class:`ManifestJobStore` — the historical ``api-jobs/*.job.json`` layout,
  unchanged (atomic replace, ``version: 1``, filename must match the id).
* :class:`DatabaseJobStore` — the ``jobs``/``batches``/``batch_jobs`` tables.

Both are expected to be used by exactly one process (the API owns the state),
which is why ``save_job`` is a plain upsert without a caller-supplied version.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence, runtime_checkable

from . import atomic_write
from .database import Database
from .repositories import (
    BatchRepository,
    EventRepository,
    JobRepository,
    RepositoryError,
)

MANIFEST_VERSION = 1

#: Fields that have no column and therefore live in ``jobs.payload``.
_JOB_PAYLOAD_FIELDS = (
    "request",
    "attempt_dirs",
    "checkpoint",
    "error",
    "last_worker_id",
    "last_serial",
    "process_started_at",
    "transport_cleanup_error",
    "submission_key",
)

#: Job dict key -> ``jobs`` column, for the fields that are columns.
_JOB_COLUMNS = (
    ("status", "status"),
    ("created_at", "created_at"),
    ("started_at", "started_at"),
    ("finished_at", "finished_at"),
    ("cancel_requested_at", "cancel_requested_at"),
    ("completion_scope", "completion_scope"),
    ("run_dir", "run_dir"),
    ("capture_run_dir", "capture_run_dir"),
    ("worker_id", "assigned_worker_id"),
    ("serial", "device_serial"),
    ("attempts", "attempt_count"),
    ("failover_count", "failover_count"),
    ("recovery_state", "recovery_state"),
    ("process_pid", "process_pid"),
    ("process_creation_time", "process_creation_time"),
    ("process_state", "process_state"),
    ("transport_cleanup_state", "transport_cleanup_state"),
    ("batch_id", "batch_id"),
    ("batch_index", "batch_index"),
    ("pinned_device_id", "pinned_device_id"),
)


def job_payload(job: dict[str, Any]) -> dict[str, Any]:
    """The manifest document for a job.

    Key order and field set are the historical ``version: 1`` shape exactly:
    the result and the scheduler-only pinning column are deliberately absent
    (a manifest never carried them).
    """
    return {
        "version": MANIFEST_VERSION,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "cancel_requested_at": job.get("cancel_requested_at"),
        "request": job.get("request"),
        "completion_scope": job.get("completion_scope"),
        "error": job.get("error"),
        "run_dir": job.get("run_dir"),
        "capture_run_dir": job.get("capture_run_dir"),
        "attempt_dirs": list(job.get("attempt_dirs") or []),
        "batch_id": job.get("batch_id"),
        "batch_index": job.get("batch_index"),
        "worker_id": job.get("worker_id"),
        "serial": job.get("serial"),
        "attempts": int(job.get("attempts") or 0),
        "failover_count": int(job.get("failover_count") or 0),
        "last_worker_id": job.get("last_worker_id"),
        "last_serial": job.get("last_serial"),
        "checkpoint": job.get("checkpoint"),
        "recovery_state": job.get("recovery_state"),
        "process_pid": job.get("process_pid"),
        "process_creation_time": job.get("process_creation_time"),
        "process_started_at": job.get("process_started_at"),
        "process_state": job.get("process_state"),
        "transport_cleanup_state": job.get("transport_cleanup_state"),
        "transport_cleanup_error": job.get("transport_cleanup_error"),
        "submission_key": job.get("submission_key"),
    }


def batch_payload(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "batch_id": batch.get("batch_id"),
        "created_at": batch.get("created_at"),
        "job_ids": list(batch.get("job_ids") or []),
        "run_dir": batch.get("run_dir"),
        "completion_scope": batch.get("completion_scope"),
    }


@runtime_checkable
class JobStore(Protocol):
    """What ``JobManager`` needs from a state store."""

    name: str

    def load_job_manifests(self) -> Iterator[tuple[dict[str, Any], str]]: ...

    def save_job(self, job: dict[str, Any]) -> None: ...

    def load_batch_manifests(self) -> Iterator[tuple[dict[str, Any], str]]: ...

    def save_batch(self, batch: dict[str, Any]) -> None: ...


class ManifestJobStore:
    """JSON manifests under ``api-jobs/`` and ``api-batches/`` (default)."""

    name = "manifest"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_dir = self.root / "api-jobs"
        self.batches_dir = self.root / "api-batches"

    def job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.job.json"

    def batch_path(self, batch_id: str) -> Path:
        return self.batches_dir / f"{batch_id}.batch.json"

    def load_job_manifests(self) -> Iterator[tuple[dict[str, Any], str]]:
        if not self.jobs_dir.exists():
            return
        for path in sorted(self.jobs_dir.glob("fb_*.job.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            yield raw, path.name.removesuffix(".job.json")

    def save_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("cannot persist job without job_id")
        atomic_write.write_json(self.job_path(job_id), job_payload(job))

    def load_batch_manifests(self) -> Iterator[tuple[dict[str, Any], str]]:
        if not self.batches_dir.exists():
            return
        for path in sorted(self.batches_dir.glob("batch_*.batch.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            yield raw, path.name.removesuffix(".batch.json")

    def save_batch(self, batch: dict[str, Any]) -> None:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            raise ValueError("cannot persist batch without batch_id")
        atomic_write.write_json(self.batch_path(batch_id), batch_payload(batch))


class DatabaseJobStore:
    """SQLite as the authoritative job/batch state store."""

    name = "database"

    def __init__(self, database: Database):
        self.database = database

    # -- jobs ---------------------------------------------------------------
    def load_job_manifests(self) -> Iterator[tuple[dict[str, Any], str]]:
        with self.database.session() as session:
            rows = JobRepository(session).list_all()
        for row in rows:
            yield self._job_from_row(row), row["id"]

    def save_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("cannot persist job without job_id")
        fields = self._job_columns(job)
        with self.database.session() as session:
            repo = JobRepository(session)
            existing = repo.get(job_id)
            if existing is None:
                batch_id = job.get("batch_id")
                if batch_id:
                    # Children are written before their batch manifest in the
                    # legacy flow, so the FK target has to exist first.  The
                    # row is completed by save_batch right after.
                    batches = BatchRepository(session)
                    if batches.get(str(batch_id)) is None:
                        batches.create(
                            batch_id=str(batch_id),
                            status="queued",
                            created_at=job.get("created_at"),
                        )
                repo.create(job_id=job_id, created_at=job.get("created_at"), **fields)
                self._record(session, job_id, "job.created", {"status": fields["status"]})
            else:
                repo.update(job_id, **fields)
                if existing["status"] != fields["status"]:
                    # Append-only history: the status transition is never
                    # overwritten, so the timeline survives later edits.
                    self._record(
                        session,
                        job_id,
                        "job.status_changed",
                        {"from": existing["status"], "to": fields["status"]},
                    )

    @staticmethod
    def _record(session: Any, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        from facebook_backend.observability.context import snapshot

        context = snapshot()
        EventRepository(session).append(
            event_type=event_type,
            job_id=job_id,
            batch_id=context.get("batch_id"),
            payload=payload,
            trace_id=context.get("trace_id"),
        )

    @staticmethod
    def _job_payload_json(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: job.get(key)
            for key in _JOB_PAYLOAD_FIELDS
            if job.get(key) is not None
        }

    def _job_columns(self, job: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "payload": self._job_payload_json(job),
            "result": job.get("result"),
        }
        for key, column in _JOB_COLUMNS:
            if key in {"status", "created_at"}:
                continue  # create() supplies status; created_at is set once
            fields[column] = job.get(key)
        fields["status"] = job.get("status") or "queued"
        fields["attempt_count"] = int(job.get("attempts") or 0)
        fields["failover_count"] = int(job.get("failover_count") or 0)
        error = job.get("error")
        if error is not None:
            fields["error_message"] = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
        else:
            fields["error_message"] = None
        return fields

    def _job_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row.get("payload") or {})
        job: dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "job_id": row["id"],
        }
        for key, column in _JOB_COLUMNS:
            job[key] = row.get(column)
        for key in _JOB_PAYLOAD_FIELDS:
            job[key] = payload.get(key)
        job["result"] = row.get("result")
        job["attempts"] = int(row.get("attempt_count") or 0)
        job["failover_count"] = int(row.get("failover_count") or 0)
        job["attempt_dirs"] = [str(value) for value in (job.get("attempt_dirs") or [])]
        job["created_at"] = row.get("created_at")
        return job

    # -- batches ------------------------------------------------------------
    def load_batch_manifests(self) -> Iterator[tuple[dict[str, Any], str]]:
        with self.database.session() as session:
            repo = BatchRepository(session)
            rows = repo.list_all()
            batches = [(row, repo.job_ids(row["id"])) for row in rows]
        for row, job_ids in batches:
            yield {
                "version": MANIFEST_VERSION,
                "batch_id": row["id"],
                "created_at": row.get("created_at"),
                "job_ids": job_ids,
                "run_dir": row.get("run_dir"),
                "completion_scope": row.get("completion_scope"),
            }, row["id"]

    def save_batch(self, batch: dict[str, Any]) -> None:
        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            raise ValueError("cannot persist batch without batch_id")
        job_ids: Sequence[str] = [str(value) for value in batch.get("job_ids") or []]
        with self.database.session() as session:
            repo = BatchRepository(session)
            if repo.get(batch_id) is None:
                repo.create(
                    batch_id=batch_id,
                    status="queued",
                    created_at=batch.get("created_at"),
                    run_dir=batch.get("run_dir"),
                    completion_scope=batch.get("completion_scope"),
                    total_jobs=len(job_ids),
                )
            else:
                repo.update(
                    batch_id,
                    run_dir=batch.get("run_dir"),
                    completion_scope=batch.get("completion_scope"),
                )
            if job_ids:
                missing = [
                    job_id for job_id in job_ids if session.get(JobModel, job_id) is None
                ]
                if missing:
                    raise RepositoryError(
                        f"batch {batch_id} references unknown jobs: {missing}"
                    )
                repo.link_jobs(batch_id, job_ids)


def open_store(kind: str, *, root: Path, database: Database | None = None) -> JobStore:
    """Build the configured store, failing loudly on an unusable request."""
    kind = (kind or "manifest").strip().lower()
    if kind == "manifest":
        return ManifestJobStore(root)
    if kind == "database":
        if database is None:
            raise ValueError("the database job store needs a database")
        return DatabaseJobStore(database)
    raise ValueError(f"unknown job store: {kind!r} (expected 'manifest' or 'database')")


# Imported late to keep the module importable without the ORM metadata loaded.
from .models import Job as JobModel  # noqa: E402

__all__ = [
    "DatabaseJobStore",
    "JobStore",
    "ManifestJobStore",
    "batch_payload",
    "job_payload",
    "open_store",
]
