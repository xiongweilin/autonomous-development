from __future__ import annotations

from dataclasses import dataclass

from autonomous_development.application.cycles import CycleService
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import (
    CanaryDecisionKind,
    CycleState,
    ReleaseDecisionKind,
)
from autonomous_development.domain.models import (
    DevelopmentCycle,
    ReleaseDecision,
    VerificationRun,
)
from autonomous_development.domain.transitions import (
    StaleCycleError,
    decide_promotion,
)


@dataclass(frozen=True, slots=True)
class ReleaseApplicationResult:
    decision: ReleaseDecision | None
    cycle: DevelopmentCycle


class ReleaseService:
    def __init__(
        self,
        cycles: CycleService,
        experiments: ExperimentService,
    ) -> None:
        self._cycles = cycles
        self._experiments = experiments

    def apply_canary_decision(
        self,
        cycle_id: str,
        decision: CanaryStageDecision,
        *,
        expected_version: int,
        operation_id: str,
    ) -> DevelopmentCycle:
        cycle = self._cycles.get(cycle_id)
        _require_version(cycle, expected_version)
        if cycle.experiment_id != decision.experiment_id:
            raise ValueError("canary decision does not belong to cycle experiment")

        if decision.kind in {CanaryDecisionKind.ADVANCE, CanaryDecisionKind.HOLD}:
            return cycle

        self._experiments.require_recorded_decision(decision.experiment_id, decision)

        if decision.kind is CanaryDecisionKind.ROLLBACK:
            return self._cycles.transition(
                cycle_id,
                CycleState.ROLLED_BACK,
                expected_version=expected_version,
                operation_id=f"{operation_id}:rollback",
                release_decision=ReleaseDecisionKind.ROLLBACK,
            )

        history = self._experiments.promotion_history(decision.experiment_id)
        if not history or history[-1] != decision:
            raise ValueError("promotion-ready decision is not the durable final canary decision")
        return self._cycles.transition(
            cycle_id,
            CycleState.PROMOTION_READY,
            expected_version=expected_version,
            operation_id=f"{operation_id}:promotion-ready",
        )

    def decide_and_apply_promotion(
        self,
        cycle_id: str,
        verification: VerificationRun,
        *,
        mandatory_gates: frozenset[str],
        expected_version: int,
        operation_id: str,
    ) -> ReleaseApplicationResult:
        cycle = self._cycles.get(cycle_id)
        _require_version(cycle, expected_version)
        if cycle.experiment_id is None:
            raise ValueError("promotion-ready cycle has no experiment")

        experiment = self._experiments.get(cycle.experiment_id)
        history = self._experiments.promotion_history(experiment.id)
        decision = decide_promotion(
            cycle,
            verification,
            experiment,
            history,
            mandatory_gates=mandatory_gates,
        )

        target_state = _decision_state(decision.kind)
        if target_state is None:
            return ReleaseApplicationResult(decision=decision, cycle=cycle)

        updated = self._cycles.transition(
            cycle_id,
            target_state,
            expected_version=expected_version,
            operation_id=f"{operation_id}:{decision.kind.value}",
            release_decision=decision.kind,
        )
        return ReleaseApplicationResult(decision=decision, cycle=updated)


def _decision_state(kind: ReleaseDecisionKind) -> CycleState | None:
    if kind is ReleaseDecisionKind.PROMOTE:
        return CycleState.PROMOTED
    if kind is ReleaseDecisionKind.REJECT:
        return CycleState.REJECTED
    if kind is ReleaseDecisionKind.ROLLBACK:
        return CycleState.ROLLED_BACK
    return None


def _require_version(cycle: DevelopmentCycle, expected_version: int) -> None:
    if cycle.version != expected_version:
        raise StaleCycleError(
            f"cycle {cycle.id} is at version {cycle.version}, not {expected_version}"
        )
