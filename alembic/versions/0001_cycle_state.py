"""Create durable development-cycle state.

Revision ID: 0001_cycle_state
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_cycle_state"
down_revision: str | None = None
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
        sa.Column("is_terminal", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_development_cycles_target_id",
        "development_cycles",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        "uq_development_cycles_one_active_per_target",
        "development_cycles",
        ["target_id"],
        unique=True,
        postgresql_where=sa.text("is_terminal = false"),
        sqlite_where=sa.text("is_terminal = 0"),
    )
    op.create_table(
        "cycle_transition_operations",
        sa.Column("operation_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_cycle_transition_operations_cycle_id",
        "cycle_transition_operations",
        ["cycle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cycle_transition_operations_cycle_id",
        table_name="cycle_transition_operations",
    )
    op.drop_table("cycle_transition_operations")
    op.drop_index(
        "uq_development_cycles_one_active_per_target",
        table_name="development_cycles",
    )
    op.drop_index("ix_development_cycles_target_id", table_name="development_cycles")
    op.drop_table("development_cycles")
