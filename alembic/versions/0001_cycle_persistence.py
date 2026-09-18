"""create durable development cycle tables

Revision ID: 0001_cycle_persistence
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_cycle_persistence"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "development_cycles",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("objective_revision_id", sa.String(length=128), nullable=False),
        sa.Column("baseline_release_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=True),
        sa.Column("verification_run_id", sa.String(length=128), nullable=True),
        sa.Column("artifact_id", sa.String(length=128), nullable=True),
        sa.Column("candidate_deployment_id", sa.String(length=128), nullable=True),
        sa.Column("experiment_id", sa.String(length=128), nullable=True),
        sa.Column("release_decision", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_development_cycles_target_id",
        "development_cycles",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "cycle_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=64), nullable=False),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cycle_id"], ["development_cycles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cycle_id",
            "resulting_version",
            name="uq_cycle_events_cycle_resulting_version",
        ),
        sa.UniqueConstraint("operation_id", name="uq_cycle_events_operation_id"),
    )
    op.create_index(
        "ix_cycle_events_cycle_version",
        "cycle_events",
        ["cycle_id", "resulting_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cycle_events_cycle_version", table_name="cycle_events")
    op.drop_table("cycle_events")
    op.drop_index("ix_development_cycles_target_id", table_name="development_cycles")
    op.drop_table("development_cycles")
