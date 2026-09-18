import pytest

from autonomous_development.domain.canary import (
    CanaryGuardrails,
    CanaryStageEvidence,
    advance_experiment,
    evaluate_canary_stage,
    promotion_evidence_refs,
)
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment


def experiment(*, stage_index: int = 0) -> Experiment:
    return Experiment(
        id="experiment-1",
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-1",
        stages=(
            CanaryStage(weight_percent=10, min_duration_seconds=60, min_requests=100),
            CanaryStage(weight_percent=50, min_duration_seconds=120, min_requests=200),
            CanaryStage(weight_percent=100, min_duration_seconds=180, min_requests=300),
        ),
        current_stage_index=stage_index,
    )


def guardrails() -> CanaryGuardrails:
    return CanaryGuardrails(
        max_candidate_error_rate=0.02,
        max_error_rate_delta=0.01,
        max_candidate_p95_latency_ms=250.0,
        max_p95_latency_ratio=1.25,
    )


def evidence(
    *,
    stage_index: int = 0,
    weight_percent: int = 10,
    duration: int = 60,
    total: int = 100,
    candidate: int = 10,
    control: int = 90,
    candidate_error: float = 0.005,
    control_error: float | None = 0.004,
    candidate_p95: float = 110.0,
    control_p95: float | None = 100.0,
    ref: str = "canary:stage-0",
) -> CanaryStageEvidence:
    return CanaryStageEvidence(
        experiment_id="experiment-1",
        stage_index=stage_index,
        weight_percent=weight_percent,
        observed_duration_seconds=duration,
        total_requests=total,
        candidate_requests=candidate,
        control_requests=control,
        candidate_error_rate=candidate_error,
        control_error_rate=control_error,
        candidate_p95_latency_ms=candidate_p95,
        control_p95_latency_ms=control_p95,
        evidence_refs=(ref,),
    )


def test_stage_advances_only_with_sufficient_comparative_evidence() -> None:
    decision = evaluate_canary_stage(experiment(), evidence(), guardrails())
    assert decision.kind is CanaryDecisionKind.ADVANCE
    advanced = advance_experiment(experiment(), decision)
    assert advanced.current_stage_index == 1


def test_stage_holds_when_sample_is_too_small() -> None:
    decision = evaluate_canary_stage(
        experiment(),
        evidence(total=80, candidate=8, control=72),
        guardrails(),
    )
    assert decision.kind is CanaryDecisionKind.HOLD
    assert "total_requests" in decision.reason


def test_absolute_error_regression_rolls_back() -> None:
    decision = evaluate_canary_stage(
        experiment(),
        evidence(candidate_error=0.03),
        guardrails(),
    )
    assert decision.kind is CanaryDecisionKind.ROLLBACK
    assert "candidate_error_rate" in decision.violated_guardrails


def test_relative_latency_regression_rolls_back() -> None:
    decision = evaluate_canary_stage(
        experiment(),
        evidence(candidate_p95=140.0, control_p95=100.0),
        guardrails(),
    )
    assert decision.kind is CanaryDecisionKind.ROLLBACK
    assert "p95_latency_ratio" in decision.violated_guardrails


def test_final_100_percent_stage_can_finish_without_control_arm() -> None:
    decision = evaluate_canary_stage(
        experiment(stage_index=2),
        evidence(
            stage_index=2,
            weight_percent=100,
            duration=180,
            total=300,
            candidate=300,
            control=0,
            control_error=None,
            candidate_p95=120.0,
            control_p95=None,
            ref="canary:stage-2",
        ),
        guardrails(),
    )
    assert decision.kind is CanaryDecisionKind.PROMOTION_READY


def test_promotion_history_requires_every_stage_in_order() -> None:
    first = evaluate_canary_stage(experiment(), evidence(), guardrails())
    second = evaluate_canary_stage(
        experiment(stage_index=1),
        evidence(
            stage_index=1,
            weight_percent=50,
            duration=120,
            total=200,
            candidate=100,
            control=100,
            ref="canary:stage-1",
        ),
        guardrails(),
    )
    final = evaluate_canary_stage(
        experiment(stage_index=2),
        evidence(
            stage_index=2,
            weight_percent=100,
            duration=180,
            total=300,
            candidate=300,
            control=0,
            control_error=None,
            control_p95=None,
            ref="canary:stage-2",
        ),
        guardrails(),
    )
    refs = promotion_evidence_refs(experiment(), (first, second, final))
    assert refs == ("canary:stage-0", "canary:stage-1", "canary:stage-2")

    with pytest.raises(ValueError, match="history"):
        promotion_evidence_refs(experiment(), (first, final))
