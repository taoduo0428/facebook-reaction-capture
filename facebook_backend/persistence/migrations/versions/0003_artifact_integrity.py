"""Content baselines for finalized job and batch artifacts."""
from alembic import op
import sqlalchemy as sa

revision = "0003_artifact_integrity"
down_revision = "0002_job_pinning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_integrity",
        sa.Column("owner_kind", sa.String(8), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("artifact_name", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("file_stamp", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("owner_kind", "owner_id", "artifact_name", name="pk_artifact_integrity"),
    )


def downgrade() -> None:
    op.drop_table("artifact_integrity")
