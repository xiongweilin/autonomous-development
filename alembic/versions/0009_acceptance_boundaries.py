"""Close V1 production-acceptance persistence gaps.

Revision ID: 0009_acceptance_boundaries
Revises: 0008_feedback_iteration_triggers
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_acceptance_boundaries"
down_revision: str | None = "0008_feedback_iteration_triggers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "change_proposals",
        sa.Column(
            "max_changed_files",
            sa.Integer(),
            nullable=False,
            server_default="50",
        ),
    )
    op.create_table(
        "request_attributions",
        sa.Column("request_ref", sa.String(length=256), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arm", sa.String(length=32), nullable=False),
        sa.Column("experiment_id", sa.String(length=128), nullable=False),
        sa.Column("release_id", sa.String(length=128), nullable=True),
        sa.Column("deployment_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("request_ref"),
    )
    op.create_index(
        "ix_request_attributions_target_id",
        "request_attributions",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        "ix_request_attributions_observed_at",
        "request_attributions",
        ["observed_at"],
        unique=False,
    )
    op.create_index(
        "ix_request_attributions_experiment_id",
        "request_attributions",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        "ix_request_attributions_release_id",
        "request_attributions",
        ["release_id"],
        unique=False,
    )
    op.create_index(
        "ix_request_attributions_deployment_id",
        "request_attributions",
        ["deployment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_attributions_deployment_id",
        table_name="request_attributions",
    )
    op.drop_index(
        "ix_request_attributions_release_id",
        table_name="request_attributions",
    )
    op.drop_index(
        "ix_request_attributions_experiment_id",
        table_name="request_attributions",
    )
    op.drop_index(
        "ix_request_attributions_observed_at",
        table_name="request_attributions",
    )
    op.drop_index(
        "ix_request_attributions_target_id",
        table_name="request_attributions",
    )
    op.drop_table("request_attributions")
    op.drop_column("change_proposals", "max_changed_files")
