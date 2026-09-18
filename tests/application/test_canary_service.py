import pytest

from autonomous_development.application.canary import CanaryService
from autonomous_development.domain.canary import CanaryGuardrails, CanaryStageEvidence
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment
from autonomous_development.ports.traffic import TrafficRouteState, TrafficSplit


class FakeTraffic:
    def __init__(self) -> None:
        self.applied: list[TrafficSplit] = []
        self.restored: list[dict[str, object]] = []

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        self.applied.append(split)
        return TrafficRouteState(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            candidate_weight_percent=split.candidate_weight_percent,
            generation=1,
            evidence_ref="traffic:1",
        )

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
        return TrafficRouteState(
            experiment_id=experiment_id,
            stage_index=stage_index,
            candidate_weight_percent=0,
            generation=2,
            evidence_ref="traffic:restore",
        )


class FakeObserver:
    def __init__(
        self,
        evidence: CanaryStageEvidence | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.evidence = evidence
        self.fail = fail

    def observe(self, **_: object) -> CanaryStageEvidence:
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


def test_advance_keeps_canary_and_moves_stage() -> None:
    traffic = FakeTraffic()
    result = CanaryService(traffic, FakeObserver(stage_evidence())).run_stage(
        experiment(),
        guardrails(),
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
    )
    assert result.decision.kind is CanaryDecisionKind.ADVANCE
    assert result.experiment.current_stage_index == 1
    assert not traffic.restored


def test_regression_restores_control() -> None:
    traffic = FakeTraffic()
    result = CanaryService(
        traffic,
        FakeObserver(stage_evidence(candidate_error=0.2)),
    ).run_stage(
        experiment(),
        guardrails(),
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
    )
    assert result.decision.kind is CanaryDecisionKind.ROLLBACK
    assert traffic.restored


def test_insufficient_evidence_restores_control() -> None:
    traffic = FakeTraffic()
    result = CanaryService(
        traffic,
        FakeObserver(stage_evidence(total=80)),
    ).run_stage(
        experiment(),
        guardrails(),
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
    )
    assert result.decision.kind is CanaryDecisionKind.HOLD
    assert traffic.restored


def test_observer_failure_restores_control_before_propagating() -> None:
    traffic = FakeTraffic()
    with pytest.raises(RuntimeError, match="observer unavailable"):
        CanaryService(traffic, FakeObserver(fail=True)).run_stage(
            experiment(),
            guardrails(),
            control_base_url="http://127.0.0.1:4100",
            candidate_base_url="http://127.0.0.1:4200",
        )
    assert traffic.restored
