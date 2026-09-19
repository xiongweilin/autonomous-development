from __future__ import annotations

from dataclasses import dataclass

from autonomous_development.application.experiments import ExperimentService
from autonomous_development.domain.canary import (
    CanaryGuardrails,
    CanaryStageDecision,
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
        experiments: ExperimentService,
    ) -> None:
        self._traffic = traffic
        self._observer = observer
        self._experiments = experiments

    def run_stage(
        self,
        experiment_id: str,
        guardrails: CanaryGuardrails,
        *,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> CanaryStageResult:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")

        decision_operation_id = f"{operation_id}:decision"
        replayed = self._experiments.replay_decision(decision_operation_id)
        if replayed is not None:
            experiment, decision = replayed
            self._ensure_post_decision_routing(
                experiment,
                decision,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                operation_id=operation_id,
            )
            return CanaryStageResult(experiment=experiment, decision=decision)

        experiment = self._experiments.get(experiment_id)
        stage_index = experiment.current_stage_index
        stage = experiment.stages[stage_index]
        route = self._traffic.apply(
            TrafficSplit(
                experiment_id=experiment.id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                candidate_weight_percent=stage.weight_percent,
                operation_id=f"{operation_id}:traffic",
                target_id=experiment.target_id,
                control_release_id=experiment.control_release_id,
                candidate_deployment_id=experiment.candidate_deployment_id,
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
                experiment_id=experiment.id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                operation_id=f"{operation_id}:observer-failed:restore",
            )
            raise

        decision = evaluate_canary_stage(experiment, evidence, guardrails)
        persisted = self._experiments.record_decision(
            experiment.id,
            decision,
            expected_stage_index=stage_index,
            operation_id=decision_operation_id,
        )
        self._ensure_post_decision_routing(
            persisted,
            decision,
            control_base_url=control_base_url,
            candidate_base_url=candidate_base_url,
            operation_id=operation_id,
        )
        return CanaryStageResult(experiment=persisted, decision=decision)

    def _ensure_post_decision_routing(
        self,
        experiment: Experiment,
        decision: CanaryStageDecision,
        *,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> None:
        if decision.kind not in {CanaryDecisionKind.ROLLBACK, CanaryDecisionKind.HOLD}:
            return
        self._restore_control(
            experiment_id=experiment.id,
            stage_index=decision.stage_index,
            control_base_url=control_base_url,
            candidate_base_url=candidate_base_url,
            operation_id=f"{operation_id}:{decision.kind.value}:restore",
        )

    def _restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> None:
        self._traffic.restore_control(
            experiment_id=experiment_id,
            stage_index=stage_index,
            control_base_url=control_base_url,
            candidate_base_url=candidate_base_url,
            operation_id=operation_id,
        )
