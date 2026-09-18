from sqlalchemy import create_engine

import pytest

from autonomous_development.adapters.postgres.experiments import SqlExperimentRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment
from autonomous_development.ports.persistence import (
    ConcurrentUpdateError,
    OperationConflictError,
)


def experiment(stage_index: int = 0) -> Experiment:
    return Experiment(
        id="experiment-1",
        target_id="target-1",
        control_release_id="release-1",
        candidate_deployment_id="deployment-1",
        stages=(
            CanaryStage(10, 60, 100),
            CanaryStage(100, 120, 200),
        ),
        current_stage_index=stage_index,
    )


def advance_decision() -> CanaryStageDecision:
    return CanaryStageDecision(
        kind=CanaryDecisionKind.ADVANCE,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=1,
        evidence_refs=("canary:stage-0",),
        violated_guardrails=(),
        reason="stage passed",
    )


def repository() -> SqlExperimentRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return SqlExperimentRepository(engine)


def test_stage_decision_is_persisted_and_idempotent() -> None:
    repo = repository()
    repo.add(experiment())
    updated = experiment(stage_index=1)
    first = repo.commit_stage_decision(
        updated,
        advance_decision(),
        expected_stage_index=0,
        operation_id="op-1",
    )
    replay = repo.commit_stage_decision(
        updated,
        advance_decision(),
        expected_stage_index=0,
        operation_id="op-1",
    )
    assert replay == first
    assert repo.get("experiment-1") == updated


def test_same_operation_with_different_decision_conflicts() -> None:
    repo = repository()
    repo.add(experiment())
    updated = experiment(stage_index=1)
    repo.commit_stage_decision(
        updated,
        advance_decision(),
        expected_stage_index=0,
        operation_id="op-1",
    )
    conflicting = CanaryStageDecision(
        kind=CanaryDecisionKind.HOLD,
        experiment_id="experiment-1",
        stage_index=0,
        next_stage_index=None,
        evidence_refs=("canary:other",),
        violated_guardrails=(),
        reason="not enough data",
    )
    with pytest.raises(OperationConflictError):
        repo.commit_stage_decision(
            experiment(),
            conflicting,
            expected_stage_index=0,
            operation_id="op-1",
        )


def test_distinct_stale_stage_operation_fails_cas() -> None:
    repo = repository()
    repo.add(experiment())
    repo.commit_stage_decision(
        experiment(stage_index=1),
        advance_decision(),
        expected_stage_index=0,
        operation_id="op-1",
    )
    with pytest.raises(ConcurrentUpdateError):
        repo.commit_stage_decision(
            experiment(stage_index=1),
            advance_decision(),
            expected_stage_index=0,
            operation_id="op-2",
        )
