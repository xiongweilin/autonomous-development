"""Persist releases, serving pointers and attributable feedback.

Revision ID: 0003_feedback_attribution
Revises: 0002_canary_experiments
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_feedback_attribution"
down_revision: str | None = "0002_canary_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "released_versions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("source_commit", sa.String(length=128), nullable=False),
        sa.Column("source_tree", sa.String(length=128), nullable=False),
        sa.Column("artifact_digest", sa.String(length=160), nullable=False),
        sa.Column("objective_revision_id", sa.String(length=128), nullable=False),
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_released_versions_target_id",
        "released_versions",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "serving_releases",
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("target_id"),
    )
    op.create_table(
        "serving_release_operations",
        sa.Column("operation_id", sa.String(length=192), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=False),
        sa.Column("previous_release_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_serving_release_operations_target_id",
        "serving_release_operations",
        ["target_id"],
        unique=False,
    )
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.String(length=256), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=True),
        sa.Column("experiment_id", sa.String(length=128), nullable=True),
        sa.Column("request_ref", sa.String(length=256), nullable=True),
        sa.Column("free_text", sa.String(length=4000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_feedback_target_id", "user_feedback", ["target_id"])
    op.create_index("ix_user_feedback_received_at", "user_feedback", ["received_at"])
    op.create_index("ix_user_feedback_release_id", "user_feedback", ["release_id"])
    op.create_index("ix_user_feedback_deployment_id", "user_feedback", ["deployment_id"])
    op.create_index("ix_user_feedback_experiment_id", "user_feedback", ["experiment_id"])


def downgrade() -> None:
    op.drop_index("ix_user_feedback_experiment_id", table_name="user_feedback")
    op.drop_index("ix_user_feedback_deployment_id", table_name="user_feedback")
    op.drop_index("ix_user_feedback_release_id", table_name="user_feedback")
    op.drop_index("ix_user_feedback_received_at", table_name="user_feedback")
    op.drop_index("ix_user_feedback_target_id", table_name="user_feedback")
    op.drop_table("user_feedback")
    op.drop_index(
        "ix_serving_release_operations_target_id",
        table_name="serving_release_operations",
    )
    op.drop_table("serving_release_operations")
    op.drop_table("serving_releases")
    op.drop_index("ix_released_versions_target_id", table_name="released_versions")
    op.drop_table("released_versions")
