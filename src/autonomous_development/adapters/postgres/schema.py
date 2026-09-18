from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, MetaData, String, Table

metadata = MetaData()

cycles = Table(
    "development_cycles",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("objective_revision_id", String(128), nullable=False),
    Column("baseline_release_id", String(128), nullable=False),
    Column("state", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("candidate_id", String(128)),
    Column("verification_run_id", String(128)),
    Column("artifact_id", String(128)),
    Column("candidate_deployment_id", String(128)),
    Column("experiment_id", String(128)),
    Column("release_decision", String(64)),
    Column("is_terminal", Boolean, nullable=False, default=False),
)

Index(
    "uq_development_cycles_one_active_per_target",
    cycles.c.target_id,
    unique=True,
    postgresql_where=cycles.c.is_terminal.is_(False),
    sqlite_where=cycles.c.is_terminal.is_(False),
)

cycle_transitions = Table(
    "cycle_transition_operations",
    metadata,
    Column("operation_id", String(160), primary_key=True),
    Column("cycle_id", String(128), nullable=False, index=True),
    Column("from_version", Integer, nullable=False),
    Column("result_version", Integer, nullable=False),
    Column("to_state", String(64), nullable=False),
)


experiments = Table(
    "canary_experiments",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("control_release_id", String(128), nullable=False),
    Column("candidate_deployment_id", String(128), nullable=False),
    Column("stages_json", JSON, nullable=False),
    Column("current_stage_index", Integer, nullable=False),
)

experiment_stage_operations = Table(
    "canary_stage_operations",
    metadata,
    Column("operation_id", String(192), primary_key=True),
    Column("experiment_id", String(128), nullable=False, index=True),
    Column("stage_index", Integer, nullable=False),
    Column("result_stage_index", Integer, nullable=False),
    Column("decision_kind", String(64), nullable=False),
    Column("evidence_refs_json", JSON, nullable=False),
    Column("violated_guardrails_json", JSON, nullable=False),
    Column("reason", String(512), nullable=False),
)
