from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

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
    Column("evidence_window_id", String(128)),
    Column("diagnosis_id", String(128)),
    Column("change_proposal_id", String(128)),
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
    Column("sequence_id", Integer, primary_key=True, autoincrement=True),
    Column("operation_id", String(192), nullable=False, unique=True),
    Column("experiment_id", String(128), nullable=False, index=True),
    Column("stage_index", Integer, nullable=False),
    Column("result_stage_index", Integer, nullable=False),
    Column("decision_kind", String(64), nullable=False),
    Column("evidence_refs_json", JSON, nullable=False),
    Column("violated_guardrails_json", JSON, nullable=False),
    Column("reason", String(512), nullable=False),
)


release_decision_operations = Table(
    "release_decision_operations",
    metadata,
    Column("operation_id", String(192), primary_key=True),
    Column("cycle_id", String(128), nullable=False, index=True),
    Column("decision_kind", String(64), nullable=False),
    Column("gate_refs_json", JSON, nullable=False),
    Column("evidence_refs_json", JSON, nullable=False),
    Column("reason", String(512), nullable=False),
)


released_versions = Table(
    "released_versions",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("source_commit", String(128), nullable=False),
    Column("source_tree", String(128), nullable=False),
    Column("artifact_digest", String(160), nullable=False),
    Column("objective_revision_id", String(128), nullable=False),
    Column("deployment_id", String(128), nullable=False),
    Column("promoted_at", DateTime(timezone=True), nullable=False),
)

serving_releases = Table(
    "serving_releases",
    metadata,
    Column("target_id", String(128), primary_key=True),
    Column("release_id", String(128), nullable=False),
)

serving_release_operations = Table(
    "serving_release_operations",
    metadata,
    Column("operation_id", String(192), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("release_id", String(128), nullable=False),
    Column("previous_release_id", String(128)),
)

user_feedback = Table(
    "user_feedback",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("received_at", DateTime(timezone=True), nullable=False, index=True),
    Column("kind", String(32), nullable=False),
    Column("category", String(128), nullable=False),
    Column("severity", Integer, nullable=False),
    Column("provenance", String(256), nullable=False),
    Column("release_id", String(128), index=True),
    Column("deployment_id", String(128), index=True),
    Column("experiment_id", String(128), index=True),
    Column("request_ref", String(256)),
    Column("free_text", String(4000)),
)


evidence_windows = Table(
    "evidence_windows",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("release_ids_json", JSON, nullable=False),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=False),
    Column("telemetry_refs_json", JSON, nullable=False),
    Column("feedback_refs_json", JSON, nullable=False),
    Column("regression_refs_json", JSON, nullable=False),
    Column("incident_refs_json", JSON, nullable=False),
    Column("missing_evidence_json", JSON, nullable=False),
)


diagnoses = Table(
    "diagnoses",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("evidence_window_id", String(128), nullable=False, index=True),
    Column("observed_problem", String(1200), nullable=False),
    Column("affected_journey", String(800), nullable=False),
    Column("evidence_refs_json", JSON, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("competing_hypotheses_json", JSON, nullable=False),
    Column("likely_root_cause", String(1200), nullable=False),
    Column("proposed_change_class", String(300), nullable=False),
    Column("expected_outcome", String(800), nullable=False),
    Column("risks_json", JSON, nullable=False),
    Column("requested_paths_json", JSON, nullable=False),
    Column("required_validation_json", JSON, nullable=False),
)

change_proposals = Table(
    "change_proposals",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("baseline_release_id", String(128), nullable=False),
    Column("baseline_commit", String(128), nullable=False),
    Column("objective_revision_id", String(128), nullable=False),
    Column("diagnosis_id", String(128), index=True),
    Column("acceptance_criteria_json", JSON, nullable=False),
    Column("allowed_paths_json", JSON, nullable=False),
    Column("forbidden_paths_json", JSON, nullable=False),
    Column("max_implementation_attempts", Integer, nullable=False),
    Column("mandatory_gates_json", JSON, nullable=False),
    Column("change_intent", String(4000)),
)


soak_decision_operations = Table(
    "soak_decision_operations",
    metadata,
    Column("operation_id", String(192), primary_key=True),
    Column("cycle_id", String(128), nullable=False, index=True),
    Column("decision_kind", String(64), nullable=False),
    Column("evidence_refs_json", JSON, nullable=False),
    Column("violated_guardrails_json", JSON, nullable=False),
    Column("reason", String(512), nullable=False),
)


development_targets = Table(
    "development_targets",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("repository", String(1024), nullable=False),
    Column("default_branch", String(256), nullable=False),
    Column("target_contract_revision", String(128), nullable=False),
    Column("active_objective_revision_id", String(128), nullable=False, index=True),
    Column("allowed_paths_json", JSON, nullable=False),
    Column("forbidden_paths_json", JSON, nullable=False),
    Column("max_changed_files", Integer, nullable=False),
    Column("max_implementation_attempts", Integer, nullable=False),
    Column("current_release_id", String(128)),
)

product_objective_revisions = Table(
    "product_objective_revisions",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("target_id", String(128), nullable=False, index=True),
    Column("statement", String(4000), nullable=False),
    Column("acceptance_criteria_json", JSON, nullable=False),
    Column("primary_metrics_json", JSON, nullable=False),
    Column("reliability_constraints_json", JSON, nullable=False),
    Column("performance_constraints_json", JSON, nullable=False),
    Column("security_constraints_json", JSON, nullable=False),
    Column("allowed_paths_json", JSON, nullable=False),
    Column("forbidden_paths_json", JSON, nullable=False),
    Column("max_changed_files", Integer, nullable=False),
    Column("max_implementation_attempts", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
