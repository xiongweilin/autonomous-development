import pytest

from autonomous_development.application.canary import CanaryService
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.domain.canary import (
    CanaryGuardrails,
    CanaryStageDecision,
    CanaryStageEvidence,
)
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment
from autonomous_development.ports.persistence import (
    ConcurrentUpdateError,
    ExperimentStageReceipt,
    OperationConflictError,
)
from autonomous_development.ports.traffic import (
    TrafficRouteSnapshot,
    TrafficRouteState,
    TrafficSplit,
)


class MemoryExperimentRepository:
    def __init__(self) -> None:
        self.experiments: dict[str, Experiment] = {}
        self.receipts: dict[str, ExperimentStageReceipt] = {}

    def add(self, experiment: Experiment) -> Experiment:
        if experiment.id in self.experiments:
            raise ValueError("duplicate experiment")
        self.experiments[experiment.id] = experiment
        return experiment

    def get(self, experiment_id: str) -> Experiment | None:
        return self.experiments.get(experiment_id)

    def get_stage_decision(self, operation_id: str) -> ExperimentStageReceipt | None:
        return self.receipts.get(operation_id)

    def commit_stage_decision(
        self,
        experiment: Experiment,
        decision: CanaryStageDecision,
        *,
        expected_stage_index: int,
        operation_id: str,
    ) -> ExperimentStageReceipt:
        receipt = ExperimentStageReceipt(
            operation_id=operation_id,
            experiment_id=experiment.id,
            stage_index=expected_stage_index,
            result_stage_index=experiment.current_stage_index,
            decision_kind=decision.kind,
            evidence_refs=decision.evidence_refs,
            violated_guardrails=decision.violated_guardrails,
            reason=decision.reason,
        )
        existing = self.receipts.get(operation_id)
        if existing is not None:
            if existing != receipt:
                raise OperationConflictError("conflicting operation")
            return existing
        current = self.experiments[experiment.id]
        if current.current_stage_index != expected_stage_index:
            raise ConcurrentUpdateError("stale experiment")
        self.receipts[operation_id] = receipt
        self.experiments[experiment.id] = experiment
        return receipt


class FakeTraffic:
    def __init__(self) -> None:
        self.applied: list[TrafficSplit] = []
        self.restored: list[dict[str, object]] = []
        self.current: TrafficRouteSnapshot | None = None

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        self.applied.append(split)
        state = TrafficRouteState(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            candidate_weight_percent=split.candidate_weight_percent,
            generation=len(self.applied),
            evidence_ref=f"traffic:{len(self.applied)}",
        )
        self.current = TrafficRouteSnapshot(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            control_base_url=split.control_base_url,
            candidate_base_url=split.candidate_base_url,
            candidate_weight_percent=split.candidate_weight_percent,
            operation_id=split.operation_id,
            generation=state.generation,
            evidence_ref=state.evidence_ref,
            target_id=split.target_id,
            control_release_id=split.control_release_id,
            candidate_deployment_id=split.candidate_deployment_id,
        )
        return state

    def read_current(self) -> TrafficRouteSnapshot | None:
        return self.current

    def restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> TrafficRouteState:
        self.restored.append(
            {
                "experiment_id": experiment_id,
                "stage_index": stage_index,
                "operation_id": operation_id,
            }
        )
        state = TrafficRouteState(
            experiment_id=experiment_id,
            stage_index=stage_index,
            candidate_weight_percent=0,
            generation=2,
            evidence_ref="traffic:restore",
        )
        assert self.current is not None
        self.current = TrafficRouteSnapshot(
            experiment_id=experiment_id,
            stage_index=stage_index,
            control_base_url=control_base_url,
            candidate_base_url=candidate_base_url,
            candidate_weight_percent=0,
            operation_id=operation_id,
            generation=state.generation,
            evidence_ref=state.evidence_ref,
            target_id=self.current.target_id,
            control_release_id=self.current.control_release_id,
            candidate_deployment_id=self.current.candidate_deployment_id,
        )
        return state


class FakeObserver:
    def __init__(
        self,
        evidence: CanaryStageEvidence | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.evidence = evidence
        self.fail = fail
        self.calls = 0

    def observe(self, **_: object) -> CanaryStageEvidence:
        self.calls += 1
        if self.fail:
            raise RuntimeError("observer unavailable")
        assert self.evidence is not None
        return self.evidence


def experiment() -> Experiment:
    return Experiment(
        id="experiment-1",
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-1",
        stages=(
            CanaryStage(weight_percent=10, min_duration_seconds=60, min_requests=100),
            CanaryStage(weight_percent=100, min_duration_seconds=120, min_requests=200),
        ),
    )


def guardrails() -> CanaryGuardrails:
    return CanaryGuardrails(0.02, 0.01, 250.0, 1.25)


def stage_evidence(*, candidate_error: float = 0.005, total: int = 100) -> CanaryStageEvidence:
    return CanaryStageEvidence(
        experiment_id="experiment-1",
        stage_index=0,
        weight_percent=10,
        observed_duration_seconds=60,
        total_requests=total,
        candidate_requests=10 if total >= 100 else 8,
        control_requests=90 if total >= 100 else 72,
        candidate_error_rate=candidate_error,
        control_error_rate=0.004,
        candidate_p95_latency_ms=110,
        control_p95_latency_ms=100,
        evidence_refs=("canary:1",),
    )


def service(
    traffic: FakeTraffic,
    observer: FakeObserver,
) -> CanaryService:
    repository = MemoryExperimentRepository()
    experiments = ExperimentService(repository)
    experiments.create(experiment())
    return CanaryService(traffic, observer, experiments)


def run(service: CanaryService, operation_id: str = "run-1"):
    return service.run_stage(
        "experiment-1",
        guardrails(),
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        operation_id=operation_id,
    )


def test_advance_is_persisted_before_return() -> None:
    traffic = FakeTraffic()
    observer = FakeObserver(stage_evidence())
    canary = service(traffic, observer)
    result = run(canary)
    assert result.decision.kind is CanaryDecisionKind.ADVANCE
    assert result.experiment.current_stage_index == 1
    assert not traffic.restored


def test_failure_reconciliation_restores_routed_candidate_before_cleanup() -> None:
    traffic = FakeTraffic()
    observer = FakeObserver(stage_evidence())
    canary = service(traffic, observer)
    result = run(canary, "run-advance")
    assert result.decision.kind is CanaryDecisionKind.ADVANCE
    assert traffic.current is not None
    assert traffic.current.candidate_weight_percent == 10

    canary.restore_candidate_control(
        "experiment-1",
        operation_id="run-advance:failure-restore",
    )

    assert traffic.current is not None
    assert traffic.current.candidate_weight_percent == 0
    assert traffic.restored[-1]["operation_id"] == "run-advance:failure-restore"


def test_regression_persists_decision_and_restores_control() -> None:
    traffic = FakeTraffic()
    observer = FakeObserver(stage_evidence(candidate_error=0.2))
    canary = service(traffic, observer)
    result = run(canary, "run-rollback")
    assert result.decision.kind is CanaryDecisionKind.ROLLBACK
    assert observer.calls == 1
    assert traffic.restored


def test_replay_does_not_reobserve_committed_decision() -> None:
    traffic = FakeTraffic()
    observer = FakeObserver(stage_evidence(candidate_error=0.2))
    canary = service(traffic, observer)
    first = run(canary, "run-replay")
    observer.fail = True
    second = run(canary, "run-replay")
    assert first.decision == second.decision
    assert observer.calls == 1
    assert len(traffic.restored) == 2


def test_insufficient_evidence_restores_control() -> None:
    traffic = FakeTraffic()
    canary = service(traffic, FakeObserver(stage_evidence(total=80)))
    result = run(canary, "run-hold")
    assert result.decision.kind is CanaryDecisionKind.HOLD
    assert traffic.restored


def test_observer_failure_restores_control_before_propagating() -> None:
    traffic = FakeTraffic()
    canary = service(traffic, FakeObserver(fail=True))
    with pytest.raises(RuntimeError, match="observer unavailable"):
        run(canary, "run-observer-failure")
    assert traffic.restored
