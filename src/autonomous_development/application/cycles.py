from __future__ import annotations

from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.domain.transitions import StaleCycleError, transition_cycle
from autonomous_development.ports.persistence import (
    CycleRepository,
    OperationConflictError,
)


class CycleNotFoundError(LookupError):
    """The requested development cycle does not exist."""


class CycleService:
    def __init__(self, repository: CycleRepository) -> None:
        self._repository = repository

    def create(self, cycle: DevelopmentCycle) -> DevelopmentCycle:
        active = self._repository.find_active_for_target(cycle.target_id)
        if active is not None:
            raise ValueError(
                f"target {cycle.target_id} already has active cycle {active.id}"
            )
        return self._repository.add(cycle)

    def get(self, cycle_id: str) -> DevelopmentCycle:
        cycle = self._repository.get(cycle_id)
        if cycle is None:
            raise CycleNotFoundError(cycle_id)
        return cycle

    def transition(
        self,
        cycle_id: str,
        to_state: CycleState,
        *,
        expected_version: int,
        operation_id: str,
        evidence_window_id: str | None = None,
        diagnosis_id: str | None = None,
        change_proposal_id: str | None = None,
        candidate_id: str | None = None,
        verification_run_id: str | None = None,
        artifact_id: str | None = None,
        candidate_deployment_id: str | None = None,
        experiment_id: str | None = None,
        release_decision: ReleaseDecisionKind | None = None,
    ) -> DevelopmentCycle:
        existing = self._repository.get_transition(operation_id)
        if existing is not None:
            if (
                existing.cycle_id != cycle_id
                or existing.from_version != expected_version
                or existing.to_state is not to_state
            ):
                raise OperationConflictError(
                    f"operation id {operation_id} is already bound to another transition"
                )
            current = self.get(cycle_id)
            if current.version < existing.result_version:
                raise RuntimeError("transition receipt is ahead of persisted cycle state")
            return current

        current = self.get(cycle_id)
        if current.version != expected_version:
            raise StaleCycleError(
                f"cycle {cycle_id} is at version {current.version}, not {expected_version}"
            )
        updated = transition_cycle(
            current,
            to_state,
            expected_version=expected_version,
            evidence_window_id=evidence_window_id,
            diagnosis_id=diagnosis_id,
            change_proposal_id=change_proposal_id,
            candidate_id=candidate_id,
            verification_run_id=verification_run_id,
            artifact_id=artifact_id,
            candidate_deployment_id=candidate_deployment_id,
            experiment_id=experiment_id,
            release_decision=release_decision,
        )
        self._repository.commit_transition(
            updated,
            expected_version=expected_version,
            operation_id=operation_id,
            expected_to_state=to_state,
        )
        return updated
