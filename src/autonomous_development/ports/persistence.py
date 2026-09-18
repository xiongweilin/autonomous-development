from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import CanaryDecisionKind, CycleState
from autonomous_development.domain.models import (
    DevelopmentCycle,
    EvidenceWindow,
    Experiment,
    ReleaseDecision,
    ReleasedVersion,
    UserFeedback,
)


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    operation_id: str
    cycle_id: str
    from_version: int
    result_version: int
    to_state: CycleState


@dataclass(frozen=True, slots=True)
class ExperimentStageReceipt:
    operation_id: str
    experiment_id: str
    stage_index: int
    result_stage_index: int
    decision_kind: CanaryDecisionKind
    evidence_refs: tuple[str, ...]
    violated_guardrails: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ReleaseDecisionReceipt:
    operation_id: str
    decision: ReleaseDecision


@dataclass(frozen=True, slots=True)
class ServingReleaseReceipt:
    operation_id: str
    target_id: str
    release_id: str
    previous_release_id: str | None


class ConcurrentCycleError(RuntimeError):
    """A target already has another active mutating cycle."""


class ConcurrentUpdateError(RuntimeError):
    """Persisted state no longer matches the expected version or stage."""


class OperationConflictError(RuntimeError):
    """An operation id was reused with incompatible semantics."""


class CycleRepository(Protocol):
    def add(self, cycle: DevelopmentCycle) -> DevelopmentCycle: ...

    def get(self, cycle_id: str) -> DevelopmentCycle | None: ...

    def find_active_for_target(self, target_id: str) -> DevelopmentCycle | None: ...

    def get_transition(self, operation_id: str) -> TransitionReceipt | None: ...

    def commit_transition(
        self,
        cycle: DevelopmentCycle,
        *,
        expected_version: int,
        operation_id: str,
        expected_to_state: CycleState,
    ) -> TransitionReceipt: ...


class ExperimentRepository(Protocol):
    def add(self, experiment: Experiment) -> Experiment: ...

    def get(self, experiment_id: str) -> Experiment | None: ...

    def get_stage_decision(self, operation_id: str) -> ExperimentStageReceipt | None: ...

    def list_stage_decisions(
        self,
        experiment_id: str,
    ) -> tuple[ExperimentStageReceipt, ...]: ...

    def commit_stage_decision(
        self,
        experiment: Experiment,
        decision: CanaryStageDecision,
        *,
        expected_stage_index: int,
        operation_id: str,
    ) -> ExperimentStageReceipt: ...



class ReleaseDecisionRepository(Protocol):
    def get(self, operation_id: str) -> ReleaseDecisionReceipt | None: ...

    def record(
        self,
        operation_id: str,
        decision: ReleaseDecision,
    ) -> ReleaseDecisionReceipt: ...



class ReleasedVersionRepository(Protocol):
    def add(self, release: ReleasedVersion) -> ReleasedVersion: ...

    def get(self, release_id: str) -> ReleasedVersion | None: ...

    def get_serving(self, target_id: str) -> ReleasedVersion | None: ...

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ServingReleaseReceipt: ...


class FeedbackRepository(Protocol):
    def add(self, feedback: UserFeedback) -> UserFeedback: ...

    def get(self, feedback_id: str) -> UserFeedback | None: ...

    def list_attributable(
        self,
        target_id: str,
        release_id: str,
        *,
        opened_at: datetime,
        closed_at: datetime,
    ) -> tuple[UserFeedback, ...]: ...



class EvidenceWindowRepository(Protocol):
    def add(self, window: EvidenceWindow) -> EvidenceWindow: ...

    def get(self, window_id: str) -> EvidenceWindow | None: ...
