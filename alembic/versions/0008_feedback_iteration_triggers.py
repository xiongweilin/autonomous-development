"""Persist feedback-to-iteration trigger receipts.

Revision ID: 0008_feedback_iteration_triggers
Revises: 0007_target_registry
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_feedback_iteration_triggers"
down_revision: str | None = "0007_target_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_iteration_triggers",
        sa.Column("feedback_id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_window_id", sa.String(length=128), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=False),
        sa.Column("proposal_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("feedback_id"),
        sa.UniqueConstraint("cycle_id"),
    )
    op.create_index(
        "ix_feedback_iteration_triggers_target_id",
        "feedback_iteration_triggers",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_iteration_triggers_release_id",
        "feedback_iteration_triggers",
        ["release_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_feedback_iteration_triggers_release_id",
        table_name="feedback_iteration_triggers",
    )
    op.drop_index(
        "ix_feedback_iteration_triggers_target_id",
        table_name="feedback_iteration_triggers",
    )
    op.drop_table("feedback_iteration_triggers")
