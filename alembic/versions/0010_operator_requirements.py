"""Add durable operator requirements, interventions and event outbox.

Revision ID: 0010_operator_requirements
Revises: 0009_acceptance_boundaries
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_operator_requirements"
down_revision: str | None = "0009_acceptance_boundaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "development_requests",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("external_reference_digest", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("normalized_requirement_text", sa.String(length=100000), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=True),
        sa.Column("pending_intervention_id", sa.String(length=160), nullable=True),
        sa.Column("workflow_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_workflow_id", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_reference_digest"),
    )
    op.create_index("ix_development_requests_target_id", "development_requests", ["target_id"])
    op.create_index("ix_development_requests_created_at", "development_requests", ["created_at"])
    op.create_index("ix_development_requests_status", "development_requests", ["status"])
    op.create_index("ix_development_requests_cycle_id", "development_requests", ["cycle_id"])
    op.create_index(
        "ix_development_requests_pending_intervention_id",
        "development_requests",
        ["pending_intervention_id"],
    )

    op.create_table(
        "requirement_analyses",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.String(length=4000), nullable=False),
        sa.Column("acceptance_criteria_json", sa.JSON(), nullable=False),
        sa.Column("requested_paths_json", sa.JSON(), nullable=False),
        sa.Column("expected_behavior_json", sa.JSON(), nullable=False),
        sa.Column("risks_json", sa.JSON(), nullable=False),
        sa.Column("missing_information_json", sa.JSON(), nullable=False),
        sa.Column("ambiguity_json", sa.JSON(), nullable=False),
        sa.Column("validation_expectations_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_requirement_analyses_request_id", "requirement_analyses", ["request_id"])

    op.create_table(
        "human_interventions",
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("cycle_id", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("question", sa.String(length=4000), nullable=False),
        sa.Column("choices_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response", sa.String(length=8000), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_interventions_request_id", "human_interventions", ["request_id"])
    op.create_index("ix_human_interventions_cycle_id", "human_interventions", ["cycle_id"])
    op.create_index("ix_human_interventions_status", "human_interventions", ["status"])

    op.create_table(
        "operator_events",
        sa.Column("sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=160), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("cycle_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id"),
    )
    op.create_index("ix_operator_events_request_id", "operator_events", ["request_id"])
    op.create_index("ix_operator_events_cycle_id", "operator_events", ["cycle_id"])
    op.create_index("ix_operator_events_event_type", "operator_events", ["event_type"])
    op.create_index("ix_operator_events_created_at", "operator_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_operator_events_created_at", table_name="operator_events")
    op.drop_index("ix_operator_events_event_type", table_name="operator_events")
    op.drop_index("ix_operator_events_cycle_id", table_name="operator_events")
    op.drop_index("ix_operator_events_request_id", table_name="operator_events")
    op.drop_table("operator_events")
    op.drop_index("ix_human_interventions_status", table_name="human_interventions")
    op.drop_index("ix_human_interventions_cycle_id", table_name="human_interventions")
    op.drop_index("ix_human_interventions_request_id", table_name="human_interventions")
    op.drop_table("human_interventions")
    op.drop_index("ix_requirement_analyses_request_id", table_name="requirement_analyses")
    op.drop_table("requirement_analyses")
    op.drop_index(
        "ix_development_requests_pending_intervention_id",
        table_name="development_requests",
    )
    op.drop_index("ix_development_requests_cycle_id", table_name="development_requests")
    op.drop_index("ix_development_requests_status", table_name="development_requests")
    op.drop_index("ix_development_requests_created_at", table_name="development_requests")
    op.drop_index("ix_development_requests_target_id", table_name="development_requests")
    op.drop_table("development_requests")
