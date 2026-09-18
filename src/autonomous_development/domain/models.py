from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from .enums import (
    CycleState,
    DeploymentState,
    FeedbackKind,
    ReleaseDecisionKind,
    VerificationStatus,
)


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


@dataclass(frozen=True, slots=True)
class MutationPolicy:
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = ()
    max_changed_files: int = 50
    max_implementation_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.allowed_paths:
            raise ValueError("mutation policy requires at least one allowed path")
        if self.max_changed_files < 1 or self.max_implementation_attempts < 1:
            raise ValueError("mutation limits must be positive")


@dataclass(frozen=True, slots=True)
class ProductObjectiveRevision:
    id: str
    target_id: str
    statement: str
    acceptance_criteria: tuple[str, ...]
    primary_metrics: tuple[str, ...]
    reliability_constraints: tuple[str, ...]
    performance_constraints: tuple[str, ...]
    security_constraints: tuple[str, ...]
    mutation_policy: MutationPolicy
    created_at: datetime

    def __post_init__(self) -> None:
        _required(self.id, "objective id")
        _required(self.target_id, "target id")
        _required(self.statement, "objective statement")
        if not self.acceptance_criteria:
            raise ValueError("objective requires acceptance criteria")


@dataclass(frozen=True, slots=True)
class ReleasedVersion:
    id: str
    target_id: str
    source_commit: str
    source_tree: str
    artifact_digest: str
    objective_revision_id: str
    deployment_id: str
    promoted_at: datetime

    def __post_init__(self) -> None:
        for field_name, value in (
            ("release id", self.id),
            ("target id", self.target_id),
            ("source commit", self.source_commit),
            ("source tree", self.source_tree),
            ("artifact digest", self.artifact_digest),
            ("objective revision id", self.objective_revision_id),
            ("deployment id", self.deployment_id),
        ):
            _required(value, field_name)


@dataclass(frozen=True, slots=True)
class DevelopmentTarget:
    id: str
    repository: str
    default_branch: str
    target_contract_revision: str
    active_objective_revision_id: str
    mutation_policy: MutationPolicy
    current_release_id: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("target id", self.id),
            ("repository", self.repository),
            ("default branch", self.default_branch),
            ("target contract revision", self.target_contract_revision),
            ("active objective revision id", self.active_objective_revision_id),
        ):
            _required(value, field_name)


@dataclass(frozen=True, slots=True)
class EvidenceWindow:
    id: str
    target_id: str
    release_ids: tuple[str, ...]
    opened_at: datetime
    closed_at: datetime
    telemetry_refs: tuple[str, ...] = ()
    feedback_refs: tuple[str, ...] = ()
    regression_refs: tuple[str, ...] = ()
    incident_refs: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.id, "evidence window id")
        _required(self.target_id, "target id")
        if self.closed_at < self.opened_at:
            raise ValueError("evidence window closes before it opens")
        if not self.release_ids:
            raise ValueError("evidence window must identify at least one release")


@dataclass(frozen=True, slots=True)
class Diagnosis:
    id: str
    evidence_window_id: str
    observed_problem: str
    affected_journey: str
    evidence_refs: tuple[str, ...]
    confidence: float
    competing_hypotheses: tuple[str, ...]
    likely_root_cause: str
    proposed_change_class: str
    expected_outcome: str
    risks: tuple[str, ...]
    requested_paths: tuple[str, ...]
    required_validation: tuple[str, ...]

    def __post_init__(self) -> None:
        _required(self.id, "diagnosis id")
        _required(self.evidence_window_id, "evidence window id")
        _required(self.observed_problem, "observed problem")
        _required(self.likely_root_cause, "likely root cause")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("diagnosis confidence must be between 0 and 1")
        if not self.evidence_refs:
            raise ValueError("diagnosis requires evidence references")


@dataclass(frozen=True, slots=True)
class ChangeProposal:
    id: str
    target_id: str
    baseline_release_id: str
    baseline_commit: str
    objective_revision_id: str
    diagnosis_id: str | None
    acceptance_criteria: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    max_implementation_attempts: int
    mandatory_gates: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("proposal id", self.id),
            ("target id", self.target_id),
            ("baseline release id", self.baseline_release_id),
            ("baseline commit", self.baseline_commit),
            ("objective revision id", self.objective_revision_id),
        ):
            _required(value, field_name)
        if not self.acceptance_criteria or not self.allowed_paths or not self.mandatory_gates:
            raise ValueError("proposal requires acceptance criteria, scope and mandatory gates")
        if self.max_implementation_attempts < 1:
            raise ValueError("implementation attempt budget must be positive")


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    id: str
    cycle_id: str
    worktree_path: str
    branch_name: str
    base_commit: str
    candidate_commit: str
    tree_hash: str
    changed_paths: tuple[str, ...]
    codex_thread_id: str
    implementation_attempt: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("candidate id", self.id),
            ("cycle id", self.cycle_id),
            ("worktree path", self.worktree_path),
            ("branch name", self.branch_name),
            ("base commit", self.base_commit),
            ("candidate commit", self.candidate_commit),
            ("tree hash", self.tree_hash),
            ("codex thread id", self.codex_thread_id),
        ):
            _required(value, field_name)
        if self.implementation_attempt < 1:
            raise ValueError("implementation attempt must be positive")


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    id: str
    gate: str
    status: VerificationStatus
    started_at: datetime
    ended_at: datetime
    evidence_refs: tuple[str, ...]
    measurements: Mapping[str, float | int | str | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required(self.id, "verification check id")
        _required(self.gate, "verification gate")
        if self.ended_at < self.started_at:
            raise ValueError("verification check ends before it starts")
        if not self.evidence_refs:
            raise ValueError("verification check requires evidence")


@dataclass(frozen=True, slots=True)
class VerificationRun:
    id: str
    candidate_id: str
    checks: tuple[VerificationCheck, ...]

    def __post_init__(self) -> None:
        _required(self.id, "verification run id")
        _required(self.candidate_id, "candidate id")
        if not self.checks:
            raise ValueError("verification run requires checks")

    @property
    def passed(self) -> bool:
        return all(check.status is VerificationStatus.PASSED for check in self.checks)

    @property
    def passed_gates(self) -> frozenset[str]:
        return frozenset(
            check.gate for check in self.checks if check.status is VerificationStatus.PASSED
        )


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    id: str
    candidate_id: str
    image_digest: str
    source_tree_hash: str
    build_definition_digest: str
    dependency_lock_digest: str
    sbom_digest: str
    vulnerability_scan_ref: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("artifact id", self.id),
            ("candidate id", self.candidate_id),
            ("image digest", self.image_digest),
            ("source tree hash", self.source_tree_hash),
            ("build definition digest", self.build_definition_digest),
            ("dependency lock digest", self.dependency_lock_digest),
            ("sbom digest", self.sbom_digest),
            ("vulnerability scan ref", self.vulnerability_scan_ref),
        ):
            _required(value, field_name)


@dataclass(frozen=True, slots=True)
class Deployment:
    id: str
    target_id: str
    artifact_id: str
    environment: str
    state: DeploymentState
    observed_at: datetime | None = None
    observation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("deployment id", self.id),
            ("target id", self.target_id),
            ("artifact id", self.artifact_id),
            ("environment", self.environment),
        ):
            _required(value, field_name)
        if (
            self.state in {DeploymentState.READY, DeploymentState.SERVING}
            and (self.observed_at is None or not self.observation_refs)
        ):
            raise ValueError("ready/serving deployment requires independent observation")


@dataclass(frozen=True, slots=True)
class CanaryStage:
    weight_percent: int
    min_duration_seconds: int
    min_requests: int

    def __post_init__(self) -> None:
        if not 1 <= self.weight_percent <= 100:
            raise ValueError("canary weight must be between 1 and 100")
        if self.min_duration_seconds < 1 or self.min_requests < 1:
            raise ValueError("canary evidence thresholds must be positive")


@dataclass(frozen=True, slots=True)
class Experiment:
    id: str
    target_id: str
    control_release_id: str
    candidate_deployment_id: str
    stages: tuple[CanaryStage, ...]
    current_stage_index: int = 0

    def __post_init__(self) -> None:
        _required(self.id, "experiment id")
        _required(self.target_id, "target id")
        _required(self.control_release_id, "control release id")
        _required(self.candidate_deployment_id, "candidate deployment id")
        if not self.stages:
            raise ValueError("experiment requires canary stages")
        if tuple(stage.weight_percent for stage in self.stages) != tuple(
            sorted(stage.weight_percent for stage in self.stages)
        ):
            raise ValueError("canary stage weights must be monotonically increasing")
        if self.stages[-1].weight_percent != 100:
            raise ValueError("final canary stage must reach 100 percent")
        if not 0 <= self.current_stage_index < len(self.stages):
            raise ValueError("current canary stage index is invalid")


@dataclass(frozen=True, slots=True)
class UserFeedback:
    id: str
    target_id: str
    received_at: datetime
    kind: FeedbackKind
    category: str
    severity: int
    provenance: str
    release_id: str | None = None
    deployment_id: str | None = None
    experiment_id: str | None = None
    request_ref: str | None = None
    free_text: str | None = None

    def __post_init__(self) -> None:
        _required(self.id, "feedback id")
        _required(self.target_id, "target id")
        _required(self.category, "feedback category")
        _required(self.provenance, "feedback provenance")
        if not 0 <= self.severity <= 5:
            raise ValueError("feedback severity must be between 0 and 5")

    @property
    def attributable(self) -> bool:
        return self.release_id is not None or self.deployment_id is not None


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    kind: ReleaseDecisionKind
    cycle_id: str
    gate_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _required(self.cycle_id, "cycle id")
        _required(self.reason, "release decision reason")


@dataclass(frozen=True, slots=True)
class DevelopmentCycle:
    id: str
    target_id: str
    objective_revision_id: str
    baseline_release_id: str
    state: CycleState = CycleState.NEW
    version: int = 0
    candidate_id: str | None = None
    verification_run_id: str | None = None
    artifact_id: str | None = None
    candidate_deployment_id: str | None = None
    experiment_id: str | None = None
    release_decision: ReleaseDecisionKind | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("cycle id", self.id),
            ("target id", self.target_id),
            ("objective revision id", self.objective_revision_id),
            ("baseline release id", self.baseline_release_id),
        ):
            _required(value, field_name)
        if self.version < 0:
            raise ValueError("cycle version cannot be negative")
