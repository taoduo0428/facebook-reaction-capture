"""SQLAlchemy 2.0 models for the task/batch/device/artifact state.

Design decisions worth knowing before changing anything here:

* **Timestamps are ISO-8601 UTC strings** (``2026-11-21T08:00:00Z``), exactly the
  format the manifests and the API already use.  SQLite has no timezone type;
  storing the canonical string keeps byte-compatibility with existing manifests
  and sorts lexicographically.
* **JSON payloads stay as TEXT** (``payload``, ``result``, ``events.payload``).
  The database stores metadata and indexes; large CSV/PCAP/MITM files stay on the
  filesystem and only their path/size/hash live in ``artifacts``.
* **``version`` is an optimistic-concurrency token.**  Every status write goes
  through ``UPDATE ... WHERE id = ? AND version = ?``; a zero rowcount means
  another writer won and the caller must reload instead of overwriting.
* **``job_events`` is append-only.**  Nothing in the application updates or
  deletes an event row.
* **One active lease per device** is enforced by a partial unique index
  (``WHERE released_at IS NULL``), not by application convention.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> str:
    """Canonical timestamp string (same format as ``facebook_capture_api.utc_now``)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    completed_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failed_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    cancelled_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    run_dir: Mapped[str | None] = mapped_column(Text)
    completion_scope: Mapped[str | None] = mapped_column(String(32))

    jobs: Mapped[list["BatchJob"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="BatchJob.position"
    )

    __table_args__ = (
        Index("ix_batches_status_created_at", "status", "created_at"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))
    cancel_requested_at: Mapped[str | None] = mapped_column(String(32))

    #: JSON document with the normalised request plus the run/attempt layout.
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    #: JSON document with the verified result (``build_api_result`` output).
    result: Mapped[str | None] = mapped_column(Text)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    assigned_worker_id: Mapped[str | None] = mapped_column(String(64), index=True)
    device_serial: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failover_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    batch_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("batches.id", ondelete="SET NULL"), index=True
    )
    batch_index: Mapped[int | None] = mapped_column(Integer)
    completion_scope: Mapped[str | None] = mapped_column(String(32))

    run_dir: Mapped[str | None] = mapped_column(Text)
    capture_run_dir: Mapped[str | None] = mapped_column(Text)
    process_pid: Mapped[int | None] = mapped_column(Integer)
    process_creation_time: Mapped[float | None] = mapped_column()
    process_state: Mapped[str | None] = mapped_column(String(32))
    transport_cleanup_state: Mapped[str | None] = mapped_column(String(32))
    recovery_state: Mapped[str | None] = mapped_column(String(64))

    attempts: Mapped[list["JobAttempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobAttempt.attempt_index"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_batch_position", "batch_id", "batch_index"),
    )


class BatchJob(Base):
    """Explicit batch↔job relation (position is unique inside a batch)."""

    __tablename__ = "batch_jobs"

    batch_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True
    )
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    batch: Mapped[Batch] = relationship(back_populates="jobs")

    __table_args__ = (
        UniqueConstraint("batch_id", "position", name="uq_batch_jobs_batch_position"),
        Index("ix_batch_jobs_job_id", "job_id"),
    )


class JobAttempt(Base):
    """One execution attempt: worker, device, process identity, outcome."""

    __tablename__ = "job_attempts"

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    device_serial: Mapped[str | None] = mapped_column(String(64))
    process_id: Mapped[int | None] = mapped_column(Integer)
    process_creation_time: Mapped[float | None] = mapped_column()
    started_at: Mapped[str | None] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    result_status: Mapped[str | None] = mapped_column(String(32))
    run_dir: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_index", name="uq_job_attempts_job_attempt_index"),
    )


class JobEvent(Base):
    """Append-only event log (status transitions, cleanup, failover, recovery)."""

    __tablename__ = "job_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        Index("ix_job_events_job_timestamp", "job_id", "timestamp"),
    )


class Device(Base):
    """Static device identity plus the last observed health snapshot.

    The worker configuration file stays the source of truth for the *configuration*
    (the task forbids managing it in two places); this table records identity and
    observed state for scheduling and audit.
    """

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    serial: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    avd_name: Mapped[str | None] = mapped_column(String(128))
    capture_port: Mapped[int | None] = mapped_column(Integer)
    native_port: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    health_status: Mapped[str | None] = mapped_column(String(32))
    capabilities: Mapped[str | None] = mapped_column(Text)
    current_job_id: Mapped[str | None] = mapped_column(String(64))
    cooldown_until: Mapped[float | None] = mapped_column()
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    leases: Mapped[list["DeviceLease"]] = relationship(back_populates="device")


class DeviceLease(Base):
    """Exclusive device ownership with a fencing token.

    ``released_at IS NULL`` means the lease is still open; the partial unique
    index below makes "two active leases for one device" impossible.  Expiry is
    *not* permission to take over: the scheduler must still confirm that the old
    process is gone and the transport cleanup finished.
    """

    __tablename__ = "device_leases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    fencing_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    acquired_at: Mapped[str] = mapped_column(String(32), nullable=False)
    heartbeat_at: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    released_at: Mapped[str | None] = mapped_column(String(32))
    release_reason: Mapped[str | None] = mapped_column(String(64))

    device: Mapped[Device] = relationship(back_populates="leases")

    __table_args__ = (
        Index(
            "uq_device_leases_active_device",
            "device_id",
            unique=True,
            sqlite_where=text("released_at IS NULL"),
        ),
    )


class Artifact(Base):
    """Artifact metadata; the bytes stay on the filesystem."""

    __tablename__ = "artifacts"

    artifact_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_name: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    sha256: Mapped[str | None] = mapped_column(String(64))
    content_type: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default=text("'unknown'")
    )

    job: Mapped[Job] = relationship(back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("job_id", "artifact_name", name="uq_artifacts_job_artifact_name"),
    )


__all__ = [
    "Artifact",
    "Base",
    "Batch",
    "BatchJob",
    "Device",
    "DeviceLease",
    "Job",
    "JobAttempt",
    "JobEvent",
    "utc_now",
]
