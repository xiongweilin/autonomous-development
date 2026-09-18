"""Persist canary experiments and stage decisions.

Revision ID: 0002_canary_experiments
Revises: 0001_cycle_state
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_canary_experiments"
down_revision: str | None = "0001_cycle_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canary_experiments",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("control_release_id", sa.String(length=128), nullable=False),
        sa.Column("candidate_deployment_id", sa.String(length=128), nullable=False),
        sa.Column("stages_json", sa.JSON(), nullable=False),
        sa.Column("current_stage_index", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canary_experiments_target_id",
        "canary_experiments",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "canary_stage_operations",
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("experiment_id", sa.String(length=128), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("result_stage_index", sa.Integer(), nullable=False),
        sa.Column("decision_kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("violated_guardrails_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_canary_stage_operations_experiment_id",
        "canary_stage_operations",
        ["experiment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canary_stage_operations_experiment_id",
        table_name="canary_stage_operations",
    )
    op.drop_table("canary_stage_operations")
    op.drop_index("ix_canary_experiments_target_id", table_name="canary_experiments")
    op.drop_table("canary_experiments")
