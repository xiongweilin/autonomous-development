from datetime import UTC, datetime

import pytest

from autonomous_development.domain.canary import (
    CanaryGuardrails,
    CanaryStageDecision,
    CanaryStageEvidence,
    evaluate_canary_stage,
)
from autonomous_development.domain.enums import (
    CycleState,
    ReleaseDecisionKind,
    VerificationStatus,
)
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentCycle,
    Experiment,
    VerificationCheck,
    VerificationRun,
)
from autonomous_development.domain.transitions import (
    StaleCycleError,
    TransitionError,
    decide_promotion,
    transition_cycle,
)


def cycle(state: CycleState = CycleState.NEW, **updates: object) -> DevelopmentCycle:
    values: dict[str, object] = {
        "id": "cycle-1",
        "target_id": "target-1",
        "objective_revision_id": "objective-1",
        "baseline_release_id": "release-1",
        "state": state,
    }
    values.update(updates)
    return DevelopmentCycle(**values)  # type: ignore[arg-type]


def verification(*, failed: bool = False) -> VerificationRun:
    now = datetime.now(UTC)
    checks = (
        VerificationCheck(
            id="check-static",
            gate="static",
            status=VerificationStatus.FAILED if failed else VerificationStatus.PASSED,
            started_at=now,
            ended_at=now,
            evidence_refs=("log:static",),
        ),
        VerificationCheck(
            id="check-tests",
            gate="tests",
            status=VerificationStatus.PASSED,
            started_at=now,
            ended_at=now,
            evidence_refs=("log:tests",),
        ),
    )
    return VerificationRun(id="vr-1", candidate_id="candidate-1", checks=checks)


def test_stale_transition_is_rejected() -> None:
    with pytest.raises(StaleCycleError):
        transition_cycle(cycle(), CycleState.BASELINE_VERIFIED, expected_version=1)


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(TransitionError):
        transition_cycle(cycle(), CycleState.BUILT, expected_version=0)


def test_reference_requirements_are_enforced() -> None:
    current = cycle(CycleState.DEVELOPING, version=4)
    with pytest.raises(TransitionError, match="requires a candidate"):
        transition_cycle(current, CycleState.CANDIDATE_READY, expected_version=4)


def test_terminal_cycle_cannot_reopen_implicitly() -> None:
    with pytest.raises(TransitionError, match="terminal"):
        transition_cycle(
            cycle(CycleState.COMPLETED, version=11),
            CycleState.NEW,
            expected_version=11,
        )


def promotion_ready_cycle() -> DevelopmentCycle:
    return cycle(
        CycleState.PROMOTION_READY,
        version=10,
        candidate_id="candidate-1",
        verification_run_id="vr-1",
        artifact_id="artifact-1",
        candidate_deployment_id="deployment-1",
        experiment_id="experiment-1",
    )


def experiment() -> Experiment:
    return Experiment(
        id="experiment-1",
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-1",
        stages=(
            CanaryStage(10, 60, 100),
            CanaryStage(100, 120, 200),
        ),
    )


def canary_history() -> tuple[CanaryStageDecision, ...]:
    guardrails = CanaryGuardrails(0.02, 0.01, 250.0, 1.25)
    first = evaluate_canary_stage(
        experiment(),
        CanaryStageEvidence(
            experiment_id="experiment-1",
            stage_index=0,
            weight_percent=10,
            observed_duration_seconds=60,
            total_requests=100,
            candidate_requests=10,
            control_requests=90,
            candidate_error_rate=0.005,
            control_error_rate=0.004,
            candidate_p95_latency_ms=110,
            control_p95_latency_ms=100,
            evidence_refs=("canary:stage-0",),
        ),
        guardrails,
    )
    final_experiment = Experiment(
        id="experiment-1",
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-1",
        stages=experiment().stages,
        current_stage_index=1,
    )
    final = evaluate_canary_stage(
        final_experiment,
        CanaryStageEvidence(
            experiment_id="experiment-1",
            stage_index=1,
            weight_percent=100,
            observed_duration_seconds=120,
            total_requests=200,
            candidate_requests=200,
            control_requests=0,
            candidate_error_rate=0.005,
            control_error_rate=None,
            candidate_p95_latency_ms=110,
            control_p95_latency_ms=None,
            evidence_refs=("canary:stage-1",),
        ),
        guardrails,
    )
    return (first, final)


def test_promotion_requires_all_mandatory_gates() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        experiment(),
        canary_history(),
        mandatory_gates=frozenset({"static", "tests", "security"}),
    )
    assert decision.kind is ReleaseDecisionKind.REJECT


def test_incomplete_canary_history_blocks_promotion() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        experiment(),
        canary_history()[:1],
        mandatory_gates=frozenset({"static", "tests"}),
    )
    assert decision.kind is ReleaseDecisionKind.BLOCKED


def test_complete_canary_history_promotes() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        experiment(),
        canary_history(),
        mandatory_gates=frozenset({"static", "tests"}),
    )
    assert decision.kind is ReleaseDecisionKind.PROMOTE
    assert decision.evidence_refs == ("canary:stage-0", "canary:stage-1")


def test_promoted_transition_requires_explicit_promote_decision() -> None:
    current = promotion_ready_cycle()
    with pytest.raises(TransitionError, match="promote decision"):
        transition_cycle(current, CycleState.PROMOTED, expected_version=current.version)

    promoted = transition_cycle(
        current,
        CycleState.PROMOTED,
        expected_version=current.version,
        release_decision=ReleaseDecisionKind.PROMOTE,
    )
    assert promoted.state is CycleState.PROMOTED
    assert promoted.version == current.version + 1
