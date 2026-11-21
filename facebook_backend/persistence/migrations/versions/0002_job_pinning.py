"""Add ``jobs.pinned_device_id``.

A rate-limited job must stay on the device that hit the limit instead of
migrating to another account/device.  That rule needs to survive a restart, so
it is a column, not in-memory state.

Revision ID: 0002_job_pinning
Revises: 0001_initial
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_job_pinning"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("pinned_device_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_jobs_pinned_device_id", ["pinned_device_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_pinned_device_id")
        batch_op.drop_column("pinned_device_id")
