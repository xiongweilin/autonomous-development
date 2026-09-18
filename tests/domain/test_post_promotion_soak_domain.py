from autonomous_development.domain.canary import CanaryGuardrails, CanaryStageEvidence
from autonomous_development.domain.enums import SoakDecisionKind
from autonomous_development.domain.models import CanaryStage
from autonomous_development.domain.soak import evaluate_post_promotion_soak


def stage() -> CanaryStage:
    return CanaryStage(weight_percent=100, min_duration_seconds=60, min_requests=100)


def guardrails() -> CanaryGuardrails:
    return CanaryGuardrails(
        max_candidate_error_rate=0.02,
        max_error_rate_delta=0.01,
        max_candidate_p95_latency_ms=250.0,
        max_p95_latency_ratio=1.25,
    )


def evidence(
    *,
    duration: int = 60,
    requests: int = 100,
    error_rate: float = 0.0,
    p95: float = 100.0,
    telemetry_complete: bool = True,
) -> CanaryStageEvidence:
    return CanaryStageEvidence(
        experiment_id="experiment-1-soak",
        stage_index=0,
        weight_percent=100,
        observed_duration_seconds=duration,
        total_requests=requests,
        candidate_requests=requests,
        control_requests=0,
        candidate_error_rate=error_rate,
        control_error_rate=None,
        candidate_p95_latency_ms=p95,
        control_p95_latency_ms=None,
        evidence_refs=("soak:1",),
        telemetry_complete=telemetry_complete,
    )


def test_soak_completes_only_with_sufficient_clean_evidence() -> None:
    decision = evaluate_post_promotion_soak(stage(), evidence(), guardrails())
    assert decision.kind is SoakDecisionKind.COMPLETE


def test_soak_holds_when_evidence_is_incomplete() -> None:
    decision = evaluate_post_promotion_soak(
        stage(),
        evidence(duration=10, requests=20, telemetry_complete=False),
        guardrails(),
    )
    assert decision.kind is SoakDecisionKind.HOLD
    assert "insufficient" in decision.reason


def test_soak_rolls_back_before_considering_evidence_sufficiency() -> None:
    decision = evaluate_post_promotion_soak(
        stage(),
        evidence(duration=10, requests=20, error_rate=0.2),
        guardrails(),
    )
    assert decision.kind is SoakDecisionKind.ROLLBACK
    assert decision.violated_guardrails == ("candidate_error_rate",)
