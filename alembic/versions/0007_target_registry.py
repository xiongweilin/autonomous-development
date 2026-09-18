"""Persist development targets and immutable objective revisions.

Revision ID: 0007_target_registry
Revises: 0006_soak_decisions
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_target_registry"
down_revision: str | None = "0006_soak_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "development_targets",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("repository", sa.String(length=1024), nullable=False),
        sa.Column("default_branch", sa.String(length=256), nullable=False),
        sa.Column("target_contract_revision", sa.String(length=128), nullable=False),
        sa.Column("active_objective_revision_id", sa.String(length=128), nullable=False),
        sa.Column("allowed_paths_json", sa.JSON(), nullable=False),
        sa.Column("forbidden_paths_json", sa.JSON(), nullable=False),
        sa.Column("max_changed_files", sa.Integer(), nullable=False),
        sa.Column("max_implementation_attempts", sa.Integer(), nullable=False),
        sa.Column("current_release_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_development_targets_active_objective_revision_id",
        "development_targets",
        ["active_objective_revision_id"],
        unique=False,
    )

    op.create_table(
        "product_objective_revisions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("statement", sa.String(length=4000), nullable=False),
        sa.Column("acceptance_criteria_json", sa.JSON(), nullable=False),
        sa.Column("primary_metrics_json", sa.JSON(), nullable=False),
        sa.Column("reliability_constraints_json", sa.JSON(), nullable=False),
        sa.Column("performance_constraints_json", sa.JSON(), nullable=False),
        sa.Column("security_constraints_json", sa.JSON(), nullable=False),
        sa.Column("allowed_paths_json", sa.JSON(), nullable=False),
        sa.Column("forbidden_paths_json", sa.JSON(), nullable=False),
        sa.Column("max_changed_files", sa.Integer(), nullable=False),
        sa.Column("max_implementation_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_objective_revisions_target_id",
        "product_objective_revisions",
        ["target_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_objective_revisions_target_id",
        table_name="product_objective_revisions",
    )
    op.drop_table("product_objective_revisions")
    op.drop_index(
        "ix_development_targets_active_objective_revision_id",
        table_name="development_targets",
    )
    op.drop_table("development_targets")
