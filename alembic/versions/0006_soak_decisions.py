"""Persist post-promotion soak decisions.

Revision ID: 0006_soak_decisions
Revises: 0005_iteration_audit
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_soak_decisions"
down_revision: str | None = "0005_iteration_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soak_decision_operations",
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=False),
        sa.Column("decision_kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("violated_guardrails_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_soak_decision_operations_cycle_id",
        "soak_decision_operations",
        ["cycle_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_soak_decision_operations_cycle_id",
        table_name="soak_decision_operations",
    )
    op.drop_table("soak_decision_operations")
