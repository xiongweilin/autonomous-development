from __future__ import annotations

from pathlib import Path

from autonomous_development.application.cycles import CycleService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.release_runtime import ReleaseRuntimeService
from autonomous_development.application.source_promotion import SourcePromotionService
from autonomous_development.domain.canary import CanaryGuardrails
from autonomous_development.domain.enums import (
    CycleState,
    ReleaseDecisionKind,
    SoakDecisionKind,
)
from autonomous_development.domain.models import CanaryStage, DevelopmentCycle
from autonomous_development.domain.soak import (
    PostPromotionSoakDecision,
    evaluate_post_promotion_soak,
)
from autonomous_development.ports.canary import CanaryObserver
from autonomous_development.ports.persistence import SoakDecisionRepository
from autonomous_development.ports.traffic import TrafficDirector, TrafficSplit


class PostPromotionSoakService:
    def __init__(
        self,
        cycles: CycleService,
        traffic: TrafficDirector,
        observer: CanaryObserver,
        releases: ReleaseCatalogService,
        decisions: SoakDecisionRepository,
        runtime: ReleaseRuntimeService,
        source_promotion: SourcePromotionService,
        *,
        repository_root: Path,
        default_branch: str,
    ) -> None:
        self._cycles = cycles
        self._traffic = traffic
        self._observer = observer
        self._releases = releases
        self._decisions = decisions
        self._runtime = runtime
        self._source_promotion = source_promotion
        self._repository_root = repository_root
        self._default_branch = default_branch

    def start(self, cycle_id: str, *, operation_id: str) -> DevelopmentCycle:
        cycle = self._cycles.get(cycle_id)
        if cycle.state is CycleState.SOAKING:
            return cycle
        if cycle.state is not CycleState.PROMOTED:
            raise ValueError("post-promotion soak requires a promoted cycle")
        serving = self._releases.serving(cycle.target_id)
        if (
            serving is None
            or serving.deployment_id != cycle.candidate_deployment_id
        ):
            raise ValueError("promoted deployment is not the current serving release")
        return self._cycles.transition(
            cycle.id,
            CycleState.SOAKING,
            expected_version=cycle.version,
            operation_id=operation_id,
        )

    def observe(
        self,
        cycle_id: str,
        stage: CanaryStage,
        guardrails: CanaryGuardrails,
        *,
        route_operation_id: str,
        decision_operation_id: str,
    ) -> PostPromotionSoakDecision:
        existing = self._decisions.get(decision_operation_id)
        if existing is not None:
            if existing.cycle_id != cycle_id:
                raise ValueError("soak decision operation belongs to another cycle")
            return existing.decision

        cycle = self._cycles.get(cycle_id)
        if cycle.state is not CycleState.SOAKING:
            raise ValueError("soak observation requires a soaking cycle")
        if cycle.experiment_id is None:
            raise ValueError("soaking cycle has no canary experiment identity")
        if stage.weight_percent != 100:
            raise ValueError("post-promotion soak stage must be 100 percent")

        control_runtime = self._runtime.resolve(cycle.baseline_release_id)
        serving, candidate_runtime = self._runtime.resolve_serving(cycle.target_id)
        if serving.deployment_id != cycle.candidate_deployment_id:
            raise ValueError("serving release is not the promoted candidate deployment")

        soak_experiment_id = f"{cycle.experiment_id}-soak"
        route = self._traffic.apply(
            TrafficSplit(
                experiment_id=soak_experiment_id,
                stage_index=0,
                control_base_url=control_runtime.base_url,
                candidate_base_url=candidate_runtime.base_url,
                candidate_weight_percent=100,
                operation_id=route_operation_id,
                target_id=cycle.target_id,
                control_release_id=cycle.baseline_release_id,
                candidate_deployment_id=cycle.candidate_deployment_id,
            )
        )
        evidence = self._observer.observe(
            experiment_id=soak_experiment_id,
            stage_index=0,
            stage=stage,
            route_state=route,
        )
        decision = evaluate_post_promotion_soak(stage, evidence, guardrails)
        return self._decisions.record(
            decision_operation_id,
            cycle_id,
            decision,
        ).decision

    def apply(
        self,
        cycle_id: str,
        *,
        decision_operation_id: str,
        effect_operation_id: str,
    ) -> DevelopmentCycle:
        receipt = self._decisions.get(decision_operation_id)
        if receipt is None or receipt.cycle_id != cycle_id:
            raise ValueError("soak decision is not durably recorded for this cycle")
        decision = receipt.decision
        cycle = self._cycles.get(cycle_id)

        if decision.kind is SoakDecisionKind.HOLD:
            if cycle.state is not CycleState.SOAKING:
                raise ValueError("hold decision requires cycle to remain soaking")
            return cycle

        if decision.kind is SoakDecisionKind.COMPLETE:
            if cycle.state is CycleState.COMPLETED:
                return cycle
            if cycle.state is not CycleState.SOAKING:
                raise ValueError("complete decision requires a soaking cycle")
            return self._cycles.transition(
                cycle.id,
                CycleState.COMPLETED,
                expected_version=cycle.version,
                operation_id=f"{effect_operation_id}:complete",
            )

        if decision.kind is not SoakDecisionKind.ROLLBACK:
            raise RuntimeError(f"unsupported soak decision: {decision.kind.value}")
        return self._rollback_to_baseline(cycle, effect_operation_id)

    def rollback_unobserved(
        self,
        cycle_id: str,
        *,
        effect_operation_id: str,
    ) -> DevelopmentCycle:
        cycle = self._cycles.get(cycle_id)
        if cycle.state is CycleState.ROLLED_BACK:
            return cycle
        if cycle.state is not CycleState.SOAKING:
            raise ValueError("unobserved soak rollback requires a soaking cycle")
        return self._rollback_to_baseline(cycle, effect_operation_id)

    def _rollback_to_baseline(
        self,
        cycle: DevelopmentCycle,
        effect_operation_id: str,
    ) -> DevelopmentCycle:
        if cycle.experiment_id is None:
            raise ValueError("soaking cycle has no experiment identity")

        control_runtime = self._runtime.resolve(cycle.baseline_release_id)
        serving, candidate_runtime = self._runtime.resolve_serving(cycle.target_id)
        if serving.deployment_id != cycle.candidate_deployment_id:
            raise ValueError("serving release is not the promoted candidate deployment")
        self._traffic.restore_control(
            experiment_id=f"{cycle.experiment_id}-soak",
            stage_index=0,
            control_base_url=control_runtime.base_url,
            candidate_base_url=candidate_runtime.base_url,
            operation_id=f"{effect_operation_id}:traffic",
        )
        self._releases.set_serving(
            cycle.target_id,
            cycle.baseline_release_id,
            operation_id=f"{effect_operation_id}:serving",
        )
        baseline = self._releases.get(cycle.baseline_release_id)
        self._source_promotion.restore_baseline(
            repository_root=self._repository_root,
            default_branch=self._default_branch,
            baseline_commit=baseline.source_commit,
        )
        return self._cycles.transition(
            cycle.id,
            CycleState.ROLLED_BACK,
            expected_version=cycle.version,
            operation_id=f"{effect_operation_id}:cycle",
            release_decision=ReleaseDecisionKind.ROLLBACK,
        )
