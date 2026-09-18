"""Persist immutable evidence windows.

Revision ID: 0004_evidence_windows
Revises: 0003_feedback_attribution
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_evidence_windows"
down_revision: str | None = "0003_feedback_attribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_windows",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("release_ids_json", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telemetry_refs_json", sa.JSON(), nullable=False),
        sa.Column("feedback_refs_json", sa.JSON(), nullable=False),
        sa.Column("regression_refs_json", sa.JSON(), nullable=False),
        sa.Column("incident_refs_json", sa.JSON(), nullable=False),
        sa.Column("missing_evidence_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evidence_windows_target_id",
        "evidence_windows",
        ["target_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_windows_target_id", table_name="evidence_windows")
    op.drop_table("evidence_windows")
