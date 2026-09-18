from __future__ import annotations

from dataclasses import replace

from .enums import CycleState, ReleaseDecisionKind, VerificationStatus
from .models import DevelopmentCycle, ReleaseDecision, VerificationRun


class TransitionError(ValueError):
    """A requested lifecycle transition violates the V1 state machine."""


class StaleCycleError(TransitionError):
    """A command targeted an old cycle version."""


TERMINAL_STATES = frozenset(
    {
        CycleState.COMPLETED,
        CycleState.REJECTED,
        CycleState.ROLLED_BACK,
        CycleState.BLOCKED,
        CycleState.CANCELLED,
        CycleState.FAILED_TERMINAL,
    }
)

_ALLOWED: dict[CycleState, frozenset[CycleState]] = {
    CycleState.NEW: frozenset({CycleState.BASELINE_VERIFIED, CycleState.BLOCKED, CycleState.CANCELLED}),
    CycleState.BASELINE_VERIFIED: frozenset(
        {CycleState.EVIDENCE_READY, CycleState.CHANGE_PROPOSED, CycleState.BLOCKED, CycleState.CANCELLED}
    ),
    CycleState.EVIDENCE_READY: frozenset({CycleState.DIAGNOSED, CycleState.BLOCKED, CycleState.CANCELLED}),
    CycleState.DIAGNOSED: frozenset({CycleState.CHANGE_PROPOSED, CycleState.BLOCKED, CycleState.CANCELLED}),
    CycleState.CHANGE_PROPOSED: frozenset({CycleState.DEVELOPING, CycleState.BLOCKED, CycleState.CANCELLED}),
    CycleState.DEVELOPING: frozenset(
        {CycleState.CANDIDATE_READY, CycleState.FAILED_RECOVERABLE, CycleState.BLOCKED, CycleState.CANCELLED}
    ),
    CycleState.FAILED_RECOVERABLE: frozenset(
        {CycleState.DEVELOPING, CycleState.FAILED_TERMINAL, CycleState.BLOCKED, CycleState.CANCELLED}
    ),
    CycleState.CANDIDATE_READY: frozenset({CycleState.VERIFYING, CycleState.REJECTED, CycleState.CANCELLED}),
    CycleState.VERIFYING: frozenset(
        {CycleState.VERIFIED, CycleState.REJECTED, CycleState.FAILED_RECOVERABLE, CycleState.CANCELLED}
    ),
    CycleState.VERIFIED: frozenset({CycleState.BUILT, CycleState.REJECTED, CycleState.CANCELLED}),
    CycleState.BUILT: frozenset({CycleState.STAGED, CycleState.REJECTED, CycleState.CANCELLED}),
    CycleState.STAGED: frozenset({CycleState.CANARYING, CycleState.REJECTED, CycleState.CANCELLED}),
    CycleState.CANARYING: frozenset(
        {CycleState.PROMOTION_READY, CycleState.REJECTED, CycleState.ROLLED_BACK, CycleState.CANCELLED}
    ),
    CycleState.PROMOTION_READY: frozenset(
        {CycleState.PROMOTED, CycleState.REJECTED, CycleState.ROLLED_BACK, CycleState.CANCELLED}
    ),
    CycleState.PROMOTED: frozenset({CycleState.SOAKING, CycleState.ROLLED_BACK}),
    CycleState.SOAKING: frozenset({CycleState.COMPLETED, CycleState.ROLLED_BACK}),
}


def transition_cycle(
    cycle: DevelopmentCycle,
    to_state: CycleState,
    *,
    expected_version: int,
    candidate_id: str | None = None,
    verification_run_id: str | None = None,
    artifact_id: str | None = None,
    candidate_deployment_id: str | None = None,
    experiment_id: str | None = None,
    release_decision: ReleaseDecisionKind | None = None,
) -> DevelopmentCycle:
    if expected_version != cycle.version:
        raise StaleCycleError(
            f"cycle {cycle.id} is at version {cycle.version}, not {expected_version}"
        )
    if cycle.state in TERMINAL_STATES:
        raise TransitionError(f"terminal cycle cannot transition from {cycle.state.value}")
    allowed = _ALLOWED.get(cycle.state, frozenset())
    if to_state not in allowed:
        raise TransitionError(f"invalid transition: {cycle.state.value} -> {to_state.value}")
    _validate_required_reference(
        to_state,
        candidate_id=candidate_id or cycle.candidate_id,
        verification_run_id=verification_run_id or cycle.verification_run_id,
        artifact_id=artifact_id or cycle.artifact_id,
        candidate_deployment_id=candidate_deployment_id or cycle.candidate_deployment_id,
        experiment_id=experiment_id or cycle.experiment_id,
        release_decision=release_decision or cycle.release_decision,
    )
    return replace(
        cycle,
        state=to_state,
        version=cycle.version + 1,
        candidate_id=candidate_id or cycle.candidate_id,
        verification_run_id=verification_run_id or cycle.verification_run_id,
        artifact_id=artifact_id or cycle.artifact_id,
        candidate_deployment_id=candidate_deployment_id or cycle.candidate_deployment_id,
        experiment_id=experiment_id or cycle.experiment_id,
        release_decision=release_decision or cycle.release_decision,
    )


def _validate_required_reference(
    state: CycleState,
    *,
    candidate_id: str | None,
    verification_run_id: str | None,
    artifact_id: str | None,
    candidate_deployment_id: str | None,
    experiment_id: str | None,
    release_decision: ReleaseDecisionKind | None,
) -> None:
    if state in {
        CycleState.CANDIDATE_READY,
        CycleState.VERIFYING,
        CycleState.VERIFIED,
        CycleState.BUILT,
        CycleState.STAGED,
        CycleState.CANARYING,
        CycleState.PROMOTION_READY,
        CycleState.PROMOTED,
        CycleState.SOAKING,
        CycleState.COMPLETED,
    } and candidate_id is None:
        raise TransitionError(f"{state.value} requires a candidate")
    if state in {
        CycleState.VERIFIED,
        CycleState.BUILT,
        CycleState.STAGED,
        CycleState.CANARYING,
        CycleState.PROMOTION_READY,
        CycleState.PROMOTED,
        CycleState.SOAKING,
        CycleState.COMPLETED,
    } and verification_run_id is None:
        raise TransitionError(f"{state.value} requires a verification run")
    if state in {
        CycleState.BUILT,
        CycleState.STAGED,
        CycleState.CANARYING,
        CycleState.PROMOTION_READY,
        CycleState.PROMOTED,
        CycleState.SOAKING,
        CycleState.COMPLETED,
    } and artifact_id is None:
        raise TransitionError(f"{state.value} requires a build artifact")
    if state in {
        CycleState.STAGED,
        CycleState.CANARYING,
        CycleState.PROMOTION_READY,
        CycleState.PROMOTED,
        CycleState.SOAKING,
        CycleState.COMPLETED,
    } and candidate_deployment_id is None:
        raise TransitionError(f"{state.value} requires a candidate deployment")
    if state in {
        CycleState.CANARYING,
        CycleState.PROMOTION_READY,
        CycleState.PROMOTED,
        CycleState.SOAKING,
        CycleState.COMPLETED,
    } and experiment_id is None:
        raise TransitionError(f"{state.value} requires an experiment")
    if state in {CycleState.PROMOTED, CycleState.SOAKING, CycleState.COMPLETED}:
        if release_decision is not ReleaseDecisionKind.PROMOTE:
            raise TransitionError(f"{state.value} requires an explicit promote decision")


def decide_promotion(
    cycle: DevelopmentCycle,
    verification: VerificationRun,
    *,
    mandatory_gates: frozenset[str],
    canary_evidence_sufficient: bool,
    hard_regression: bool,
    evidence_refs: tuple[str, ...],
) -> ReleaseDecision:
    if cycle.state is not CycleState.PROMOTION_READY:
        raise TransitionError("promotion decision requires promotion-ready cycle")
    if verification.id != cycle.verification_run_id:
        raise TransitionError("verification run does not belong to this cycle")
    if hard_regression:
        return ReleaseDecision(
            kind=ReleaseDecisionKind.ROLLBACK,
            cycle_id=cycle.id,
            gate_refs=tuple(sorted(verification.passed_gates)),
            evidence_refs=evidence_refs,
            reason="hard canary regression observed",
        )
    if not canary_evidence_sufficient:
        return ReleaseDecision(
            kind=ReleaseDecisionKind.HOLD_INSUFFICIENT_EVIDENCE,
            cycle_id=cycle.id,
            gate_refs=tuple(sorted(verification.passed_gates)),
            evidence_refs=evidence_refs,
            reason="canary evidence is insufficient",
        )
    failed_checks = tuple(
        check for check in verification.checks if check.status is not VerificationStatus.PASSED
    )
    missing_gates = mandatory_gates - verification.passed_gates
    if failed_checks or missing_gates:
        return ReleaseDecision(
            kind=ReleaseDecisionKind.REJECT,
            cycle_id=cycle.id,
            gate_refs=tuple(sorted(verification.passed_gates)),
            evidence_refs=evidence_refs,
            reason="mandatory verification gates are not all satisfied",
        )
    if not evidence_refs:
        return ReleaseDecision(
            kind=ReleaseDecisionKind.BLOCKED,
            cycle_id=cycle.id,
            gate_refs=tuple(sorted(verification.passed_gates)),
            evidence_refs=(),
            reason="promotion cannot be reconstructed without canary evidence",
        )
    return ReleaseDecision(
        kind=ReleaseDecisionKind.PROMOTE,
        cycle_id=cycle.id,
        gate_refs=tuple(sorted(verification.passed_gates)),
        evidence_refs=evidence_refs,
        reason="all mandatory gates passed and canary evidence is sufficient",
    )
