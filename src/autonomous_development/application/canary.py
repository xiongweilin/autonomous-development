from __future__ import annotations

from dataclasses import dataclass

from autonomous_development.domain.canary import (
    CanaryGuardrails,
    CanaryStageDecision,
    advance_experiment,
    evaluate_canary_stage,
)
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import Experiment
from autonomous_development.ports.canary import CanaryObserver
from autonomous_development.ports.traffic import TrafficDirector, TrafficSplit


@dataclass(frozen=True, slots=True)
class CanaryStageResult:
    experiment: Experiment
    decision: CanaryStageDecision


class CanaryService:
    def __init__(
        self,
        traffic: TrafficDirector,
        observer: CanaryObserver,
    ) -> None:
        self._traffic = traffic
        self._observer = observer

    def run_stage(
        self,
        experiment: Experiment,
        guardrails: CanaryGuardrails,
        *,
        control_base_url: str,
        candidate_base_url: str,
    ) -> CanaryStageResult:
        stage_index = experiment.current_stage_index
        stage = experiment.stages[stage_index]
        route = self._traffic.apply(
            TrafficSplit(
                experiment_id=experiment.id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                candidate_weight_percent=stage.weight_percent,
                operation_id=f"canary:{experiment.id}:stage:{stage_index}",
            )
        )
        try:
            evidence = self._observer.observe(
                experiment_id=experiment.id,
                stage_index=stage_index,
                stage=stage,
                route_state=route,
            )
        except Exception:
            self._restore_control(
                experiment,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                suffix="observer-failed",
            )
            raise

        decision = evaluate_canary_stage(experiment, evidence, guardrails)
        if decision.kind in {CanaryDecisionKind.ROLLBACK, CanaryDecisionKind.HOLD}:
            self._restore_control(
                experiment,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                suffix=decision.kind.value,
            )
            return CanaryStageResult(experiment=experiment, decision=decision)
        if decision.kind is CanaryDecisionKind.ADVANCE:
            return CanaryStageResult(
                experiment=advance_experiment(experiment, decision),
                decision=decision,
            )
        return CanaryStageResult(experiment=experiment, decision=decision)

    def _restore_control(
        self,
        experiment: Experiment,
        *,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        suffix: str,
    ) -> None:
        self._traffic.restore_control(
            experiment_id=experiment.id,
            stage_index=stage_index,
            control_base_url=control_base_url,
            candidate_base_url=candidate_base_url,
            operation_id=f"canary:{experiment.id}:stage:{stage_index}:{suffix}",
        )
