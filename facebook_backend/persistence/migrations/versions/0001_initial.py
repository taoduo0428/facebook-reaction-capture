"""initial schema: jobs, batches, attempts, events, devices, leases, artifacts

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-21

Notes:

* Timestamps are ISO-8601 UTC strings (same format as the manifests).
* ``jobs.version`` is the optimistic-concurrency token.
* ``uq_device_leases_active_device`` is a **partial** unique index
  (``WHERE released_at IS NULL``): SQLite enforces "one active lease per device"
  in the database, not in application code.
* ``render_as_batch`` is enabled in ``env.py`` because SQLite cannot ALTER most
  table definitions in place; future migrations must not be blocked by that.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batches",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("total_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cancelled_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("run_dir", sa.Text(), nullable=True),
        sa.Column("completion_scope", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batches")),
    )
    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_batches_status"), ["status"], unique=False)
        batch_op.create_index("ix_batches_status_created_at", ["status", "created_at"], unique=False)

    op.create_table(
        "devices",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("serial", sa.String(length=64), nullable=False),
        sa.Column("avd_name", sa.String(length=128), nullable=True),
        sa.Column("capture_port", sa.Integer(), nullable=True),
        sa.Column("native_port", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=True),
        sa.Column("capabilities", sa.Text(), nullable=True),
        sa.Column("current_job_id", sa.String(length=64), nullable=True),
        sa.Column("cooldown_until", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("serial", name=op.f("uq_devices_serial")),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_worker_id"), ["worker_id"], unique=False)

    op.create_table(
        "device_leases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=False),
        sa.Column("fencing_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("acquired_at", sa.String(length=32), nullable=False),
        sa.Column("heartbeat_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("released_at", sa.String(length=32), nullable=True),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_device_leases_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_leases")),
        sa.UniqueConstraint("lease_token", name=op.f("uq_device_leases_lease_token")),
    )
    with op.batch_alter_table("device_leases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_device_leases_device_id"), ["device_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_device_leases_job_id"), ["job_id"], unique=False)
        batch_op.create_index(
            "uq_device_leases_active_device",
            ["device_id"],
            unique=True,
            sqlite_where=sa.text("released_at IS NULL"),
        )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.String(length=32), nullable=True),
        sa.Column("cancel_requested_at", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("assigned_worker_id", sa.String(length=64), nullable=True),
        sa.Column("device_serial", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failover_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("batch_index", sa.Integer(), nullable=True),
        sa.Column("completion_scope", sa.String(length=32), nullable=True),
        sa.Column("run_dir", sa.Text(), nullable=True),
        sa.Column("capture_run_dir", sa.Text(), nullable=True),
        sa.Column("process_pid", sa.Integer(), nullable=True),
        sa.Column("process_creation_time", sa.Float(), nullable=True),
        sa.Column("process_state", sa.String(length=32), nullable=True),
        sa.Column("transport_cleanup_state", sa.String(length=32), nullable=True),
        sa.Column("recovery_state", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name=op.f("fk_jobs_batch_id_batches"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_assigned_worker_id"), ["assigned_worker_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_batch_id"), ["batch_id"], unique=False)
        batch_op.create_index("ix_jobs_batch_position", ["batch_id", "batch_index"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_status"), ["status"], unique=False)
        batch_op.create_index("ix_jobs_status_created_at", ["status", "created_at"], unique=False)

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_name", sa.String(length=128), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column(
            "verification_status", sa.String(length=32), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_artifacts_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("artifact_id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint("job_id", "artifact_name", name="uq_artifacts_job_artifact_name"),
    )
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_artifacts_job_id"), ["job_id"], unique=False)

    op.create_table(
        "batch_jobs",
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name=op.f("fk_batch_jobs_batch_id_batches"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_batch_jobs_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("batch_id", "job_id", name=op.f("pk_batch_jobs")),
        sa.UniqueConstraint("batch_id", "position", name="uq_batch_jobs_batch_position"),
    )
    with op.batch_alter_table("batch_jobs", schema=None) as batch_op:
        batch_op.create_index("ix_batch_jobs_job_id", ["job_id"], unique=False)

    op.create_table(
        "job_attempts",
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("device_serial", sa.String(length=64), nullable=True),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("process_creation_time", sa.Float(), nullable=True),
        sa.Column("started_at", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.String(length=32), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("result_status", sa.String(length=32), nullable=True),
        sa.Column("run_dir", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_attempts_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("attempt_id", name=op.f("pk_job_attempts")),
        sa.UniqueConstraint("job_id", "attempt_index", name="uq_job_attempts_job_attempt_index"),
    )
    with op.batch_alter_table("job_attempts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_attempts_job_id"), ["job_id"], unique=False)

    op.create_table(
        "job_events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_events_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_job_events")),
    )
    with op.batch_alter_table("job_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_events_batch_id"), ["batch_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_events_event_type"), ["event_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_events_job_id"), ["job_id"], unique=False)
        batch_op.create_index("ix_job_events_job_timestamp", ["job_id", "timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_events_timestamp"), ["timestamp"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_events_trace_id"), ["trace_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("job_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_events_trace_id"))
        batch_op.drop_index(batch_op.f("ix_job_events_timestamp"))
        batch_op.drop_index("ix_job_events_job_timestamp")
        batch_op.drop_index(batch_op.f("ix_job_events_job_id"))
        batch_op.drop_index(batch_op.f("ix_job_events_event_type"))
        batch_op.drop_index(batch_op.f("ix_job_events_batch_id"))
    op.drop_table("job_events")

    with op.batch_alter_table("job_attempts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_attempts_job_id"))
    op.drop_table("job_attempts")

    with op.batch_alter_table("batch_jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_batch_jobs_job_id")
    op.drop_table("batch_jobs")

    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifacts_job_id"))
    op.drop_table("artifacts")

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_status_created_at")
        batch_op.drop_index(batch_op.f("ix_jobs_status"))
        batch_op.drop_index("ix_jobs_batch_position")
        batch_op.drop_index(batch_op.f("ix_jobs_batch_id"))
        batch_op.drop_index(batch_op.f("ix_jobs_assigned_worker_id"))
    op.drop_table("jobs")

    with op.batch_alter_table("device_leases", schema=None) as batch_op:
        batch_op.drop_index("uq_device_leases_active_device", sqlite_where=sa.text("released_at IS NULL"))
        batch_op.drop_index(batch_op.f("ix_device_leases_job_id"))
        batch_op.drop_index(batch_op.f("ix_device_leases_device_id"))
    op.drop_table("device_leases")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_devices_worker_id"))
    op.drop_table("devices")

    with op.batch_alter_table("batches", schema=None) as batch_op:
        batch_op.drop_index("ix_batches_status_created_at")
        batch_op.drop_index(batch_op.f("ix_batches_status"))
    op.drop_table("batches")
