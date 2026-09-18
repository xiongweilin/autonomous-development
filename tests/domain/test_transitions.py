from datetime import UTC, datetime

import pytest

from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind, VerificationStatus
from autonomous_development.domain.models import DevelopmentCycle, VerificationCheck, VerificationRun
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


def test_promotion_requires_all_mandatory_gates() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        mandatory_gates=frozenset({"static", "tests", "security"}),
        canary_evidence_sufficient=True,
        hard_regression=False,
        evidence_refs=("canary:1",),
    )
    assert decision.kind is ReleaseDecisionKind.REJECT


def test_insufficient_evidence_holds_instead_of_promotes() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        canary_evidence_sufficient=False,
        hard_regression=False,
        evidence_refs=("canary:1",),
    )
    assert decision.kind is ReleaseDecisionKind.HOLD_INSUFFICIENT_EVIDENCE


def test_hard_regression_rolls_back() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        canary_evidence_sufficient=True,
        hard_regression=True,
        evidence_refs=("canary:1",),
    )
    assert decision.kind is ReleaseDecisionKind.ROLLBACK


def test_promotion_requires_reconstructable_evidence() -> None:
    decision = decide_promotion(
        promotion_ready_cycle(),
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        canary_evidence_sufficient=True,
        hard_regression=False,
        evidence_refs=(),
    )
    assert decision.kind is ReleaseDecisionKind.BLOCKED


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
