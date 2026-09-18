"""Persist diagnosis/proposal audit chain and cycle evidence references.

Revision ID: 0005_iteration_audit
Revises: 0004_evidence_windows
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_iteration_audit"
down_revision: str | None = "0004_evidence_windows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "development_cycles",
        sa.Column("evidence_window_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "development_cycles",
        sa.Column("diagnosis_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "development_cycles",
        sa.Column("change_proposal_id", sa.String(length=128), nullable=True),
    )

    op.create_table(
        "diagnoses",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("evidence_window_id", sa.String(length=128), nullable=False),
        sa.Column("observed_problem", sa.String(length=1200), nullable=False),
        sa.Column("affected_journey", sa.String(length=800), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("competing_hypotheses_json", sa.JSON(), nullable=False),
        sa.Column("likely_root_cause", sa.String(length=1200), nullable=False),
        sa.Column("proposed_change_class", sa.String(length=300), nullable=False),
        sa.Column("expected_outcome", sa.String(length=800), nullable=False),
        sa.Column("risks_json", sa.JSON(), nullable=False),
        sa.Column("requested_paths_json", sa.JSON(), nullable=False),
        sa.Column("required_validation_json", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diagnoses_evidence_window_id",
        "diagnoses",
        ["evidence_window_id"],
        unique=False,
    )

    op.create_table(
        "change_proposals",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("baseline_release_id", sa.String(length=128), nullable=False),
        sa.Column("baseline_commit", sa.String(length=128), nullable=False),
        sa.Column("objective_revision_id", sa.String(length=128), nullable=False),
        sa.Column("diagnosis_id", sa.String(length=128), nullable=True),
        sa.Column("acceptance_criteria_json", sa.JSON(), nullable=False),
        sa.Column("allowed_paths_json", sa.JSON(), nullable=False),
        sa.Column("forbidden_paths_json", sa.JSON(), nullable=False),
        sa.Column("max_implementation_attempts", sa.Integer(), nullable=False),
        sa.Column("mandatory_gates_json", sa.JSON(), nullable=False),
        sa.Column("change_intent", sa.String(length=4000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_change_proposals_target_id",
        "change_proposals",
        ["target_id"],
        unique=False,
    )
    op.create_index(
        "ix_change_proposals_diagnosis_id",
        "change_proposals",
        ["diagnosis_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_change_proposals_diagnosis_id", table_name="change_proposals")
    op.drop_index("ix_change_proposals_target_id", table_name="change_proposals")
    op.drop_table("change_proposals")
    op.drop_index("ix_diagnoses_evidence_window_id", table_name="diagnoses")
    op.drop_table("diagnoses")
    op.drop_column("development_cycles", "change_proposal_id")
    op.drop_column("development_cycles", "diagnosis_id")
    op.drop_column("development_cycles", "evidence_window_id")
