from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.experiments import SqlExperimentRepository
from autonomous_development.adapters.postgres.release_decisions import (
    SqlReleaseDecisionRepository,
)
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.application.releases import ReleaseService
from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import (
    CanaryDecisionKind,
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
from autonomous_development.ports.persistence import OperationConflictError


def services() -> tuple[CycleService, ExperimentService, ReleaseService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    cycles = CycleService(SqlCycleRepository(engine))
    experiments = ExperimentService(SqlExperimentRepository(engine))
    decisions = SqlReleaseDecisionRepository(engine)
    return cycles, experiments, ReleaseService(cycles, experiments, decisions)


def cycle(state: CycleState = CycleState.CANARYING, version: int = 9) -> DevelopmentCycle:
    return DevelopmentCycle(
        id="cycle-1",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-1",
        state=state,
        version=version,
        candidate_id="candidate-1",
        verification_run_id="verification-1",
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
        stages=(CanaryStage(10, 60, 100), CanaryStage(100, 120, 200)),
    )


def advance() -> CanaryStageDecision:
    return CanaryStageDecision(
        kind=CanaryDecisionKind.ADVANCE,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=1,
        evidence_refs=("canary:stage-0",),
        violated_guardrails=(),
        reason="stage passed",
    )


def promotion_ready() -> CanaryStageDecision:
    return CanaryStageDecision(
        kind=CanaryDecisionKind.PROMOTION_READY,
        experiment_id="experiment-1",
        stage_index=1,
        next_stage_index=None,
        evidence_refs=("canary:stage-1",),
        violated_guardrails=(),
        reason="final stage passed",
    )


def verification(*, failed: bool = False) -> VerificationRun:
    now = datetime.now(UTC)
    return VerificationRun(
        id="verification-1",
        candidate_id="candidate-1",
        checks=(
            VerificationCheck(
                id="check-static",
                gate="static",
                status=VerificationStatus.FAILED if failed else VerificationStatus.PASSED,
                started_at=now,
                ended_at=now,
                evidence_refs=("check:static",),
            ),
            VerificationCheck(
                id="check-tests",
                gate="tests",
                status=VerificationStatus.PASSED,
                started_at=now,
                ended_at=now,
                evidence_refs=("check:tests",),
            ),
        ),
    )


def seed_complete_canary(
    cycles: CycleService,
    experiments: ExperimentService,
) -> CanaryStageDecision:
    cycles.create(cycle())
    experiments.create(experiment())
    experiments.record_decision(
        "experiment-1",
        advance(),
        expected_stage_index=0,
        operation_id="experiment:stage-0",
    )
    final = promotion_ready()
    experiments.record_decision(
        "experiment-1",
        final,
        expected_stage_index=1,
        operation_id="experiment:stage-1",
    )
    return final


def test_complete_canary_history_allows_promotion_ready_then_promote() -> None:
    cycles, experiments, releases = services()
    final = seed_complete_canary(cycles, experiments)

    ready = releases.apply_canary_decision(
        "cycle-1",
        final,
        expected_version=9,
        operation_id="release-1",
    )
    assert ready.state is CycleState.PROMOTION_READY
    assert ready.version == 10

    result = releases.decide_and_apply_promotion(
        "cycle-1",
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        expected_version=10,
        operation_id="release-1",
    )
    assert result.decision is not None
    assert result.decision.kind is ReleaseDecisionKind.PROMOTE
    assert result.cycle.state is CycleState.PROMOTED
    assert result.cycle.release_decision is ReleaseDecisionKind.PROMOTE


def test_missing_mandatory_gate_rejects_after_canary() -> None:
    cycles, experiments, releases = services()
    final = seed_complete_canary(cycles, experiments)
    releases.apply_canary_decision(
        "cycle-1",
        final,
        expected_version=9,
        operation_id="release-2",
    )

    result = releases.decide_and_apply_promotion(
        "cycle-1",
        verification(),
        mandatory_gates=frozenset({"static", "tests", "security"}),
        expected_version=10,
        operation_id="release-2",
    )
    assert result.decision is not None
    assert result.decision.kind is ReleaseDecisionKind.REJECT
    assert result.cycle.state is CycleState.REJECTED


def test_canary_rollback_transitions_directly_to_rolled_back() -> None:
    cycles, experiments, releases = services()
    cycles.create(cycle())
    experiments.create(experiment())
    rollback = CanaryStageDecision(
        kind=CanaryDecisionKind.ROLLBACK,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=None,
        evidence_refs=("canary:rollback",),
        violated_guardrails=("candidate_error_rate",),
        reason="regression",
    )
    experiments.record_decision(
        "experiment-1",
        rollback,
        expected_stage_index=0,
        operation_id="experiment:rollback",
    )

    updated = releases.apply_canary_decision(
        "cycle-1",
        rollback,
        expected_version=9,
        operation_id="release-rollback",
    )
    assert updated.state is CycleState.ROLLED_BACK
    assert updated.release_decision is ReleaseDecisionKind.ROLLBACK


def test_hold_does_not_advance_cycle() -> None:
    cycles, experiments, releases = services()
    cycles.create(cycle())
    experiments.create(experiment())
    hold = CanaryStageDecision(
        kind=CanaryDecisionKind.HOLD,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=None,
        evidence_refs=("canary:hold",),
        violated_guardrails=(),
        reason="insufficient evidence",
    )
    experiments.record_decision(
        "experiment-1",
        hold,
        expected_stage_index=0,
        operation_id="experiment:hold",
    )

    unchanged = releases.apply_canary_decision(
        "cycle-1",
        hold,
        expected_version=9,
        operation_id="release-hold",
    )
    assert unchanged.state is CycleState.CANARYING
    assert unchanged.version == 9


def test_unrecorded_rollback_cannot_change_cycle() -> None:
    cycles, experiments, releases = services()
    cycles.create(cycle())
    experiments.create(experiment())
    rollback = CanaryStageDecision(
        kind=CanaryDecisionKind.ROLLBACK,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=None,
        evidence_refs=("canary:unrecorded",),
        violated_guardrails=("candidate_error_rate",),
        reason="unrecorded regression",
    )

    with pytest.raises(ValueError, match="durable"):
        releases.apply_canary_decision(
            "cycle-1",
            rollback,
            expected_version=9,
            operation_id="release-unrecorded",
        )
    persisted = cycles.get("cycle-1")
    assert persisted.state is CycleState.CANARYING
    assert persisted.version == 9


def test_promote_operation_replays_after_cycle_advanced() -> None:
    cycles, experiments, releases = services()
    final = seed_complete_canary(cycles, experiments)
    releases.apply_canary_decision(
        "cycle-1",
        final,
        expected_version=9,
        operation_id="release-replay",
    )
    first = releases.decide_and_apply_promotion(
        "cycle-1",
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        expected_version=10,
        operation_id="release-replay",
    )
    second = releases.decide_and_apply_promotion(
        "cycle-1",
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        expected_version=10,
        operation_id="release-replay",
    )
    assert second.decision == first.decision
    assert second.cycle == first.cycle


def test_rollback_operation_replays_after_terminal_transition() -> None:
    cycles, experiments, releases = services()
    cycles.create(cycle())
    experiments.create(experiment())
    rollback = CanaryStageDecision(
        kind=CanaryDecisionKind.ROLLBACK,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=None,
        evidence_refs=("canary:rollback-replay",),
        violated_guardrails=("candidate_error_rate",),
        reason="regression",
    )
    experiments.record_decision(
        "experiment-1",
        rollback,
        expected_stage_index=0,
        operation_id="experiment:rollback-replay",
    )
    first = releases.apply_canary_decision(
        "cycle-1",
        rollback,
        expected_version=9,
        operation_id="release-rollback-replay",
    )
    second = releases.apply_canary_decision(
        "cycle-1",
        rollback,
        expected_version=9,
        operation_id="release-rollback-replay",
    )
    assert first == second
    assert second.state is CycleState.ROLLED_BACK



def test_release_operation_conflicts_if_recomputed_decision_changes() -> None:
    cycles, experiments, releases = services()
    final = seed_complete_canary(cycles, experiments)
    releases.apply_canary_decision(
        "cycle-1",
        final,
        expected_version=9,
        operation_id="release-conflict",
    )
    releases.decide_and_apply_promotion(
        "cycle-1",
        verification(),
        mandatory_gates=frozenset({"static", "tests"}),
        expected_version=10,
        operation_id="release-conflict",
    )

    with pytest.raises(OperationConflictError):
        releases.decide_and_apply_promotion(
            "cycle-1",
            verification(),
            mandatory_gates=frozenset({"static", "tests", "security"}),
            expected_version=10,
            operation_id="release-conflict",
        )
