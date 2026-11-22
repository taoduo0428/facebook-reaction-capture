"""Context and per-job idempotency keys for the append-only job journal.

Revision ID: 0004_job_event_context
Revises: 0003_artifact_integrity
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_job_event_context"
down_revision = "0003_artifact_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_events") as batch:
        batch.add_column(sa.Column("event_key", sa.String(128), nullable=True))
        for name in ("attempt_id", "worker_id", "device_serial"):
            batch.add_column(sa.Column(name, sa.String(64), nullable=True))
        # SQLite permits multiple NULLs. Historical rows need no invented key.
        batch.create_index("uq_job_events_job_key", ["job_id", "event_key"], unique=True)


def downgrade() -> None:
    # Dropping a live key would allow the same operation to be delivered again
    # after a downgrade/upgrade. Require the pre-upgrade backup in that case.
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM job_events WHERE event_key IS NOT NULL OR attempt_id IS NOT NULL "
        "OR worker_id IS NOT NULL OR device_serial IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("lifecycle data exists; restore the pre-upgrade backup to roll back")
    with op.batch_alter_table("job_events") as batch:
        batch.drop_index("uq_job_events_job_key")
        for name in ("device_serial", "worker_id", "attempt_id", "event_key"):
            batch.drop_column(name)
