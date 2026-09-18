from __future__ import annotations

import os
from uuid import uuid4

import pytest
from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.experiments import SqlExperimentRepository
from autonomous_development.adapters.postgres.release_decisions import (
    SqlReleaseDecisionRepository,
)
from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import (
    CanaryDecisionKind,
    CycleState,
    ReleaseDecisionKind,
)
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentCycle,
    Experiment,
    ReleaseDecision,
)
from autonomous_development.workflows.development_cycle import DevelopmentCycleWorkflow

pytestmark = pytest.mark.integration


def test_postgres_cycle_and_dbos_idempotency() -> None:
    database_url = os.environ["AUTODEV_DATABASE_URL"]
    dbos_url = os.environ["DBOS_SYSTEM_DATABASE_URL"]
    engine = create_engine(database_url)
    service = CycleService(SqlCycleRepository(engine))
    suffix = uuid4().hex
    cycle_id = f"cycle-{suffix}"
    target_id = f"target-{suffix}"
    service.create(
        DevelopmentCycle(
            id=cycle_id,
            target_id=target_id,
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    config: DBOSConfig = {
        "name": "autodev-integration",
        "application_version": "0.1.0",
        "system_database_url": dbos_url,
    }
    DBOS(config=config)
    workflow = DevelopmentCycleWorkflow(
        service,
        config_name=f"integration-{suffix}",
    )
    DBOS.launch()
    workflow_id = f"{cycle_id}:baseline"
    operation_id = f"{cycle_id}:0:baseline"
    try:
        with SetWorkflowID(workflow_id):
            first = workflow.transition(
                cycle_id,
                CycleState.BASELINE_VERIFIED.value,
                0,
                operation_id,
            )
        with SetWorkflowID(workflow_id):
            second = workflow.transition(
                cycle_id,
                CycleState.BASELINE_VERIFIED.value,
                0,
                operation_id,
            )
        persisted = service.get(cycle_id)
        assert first == second
        assert persisted.version == 1
        assert persisted.state is CycleState.BASELINE_VERIFIED
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
        engine.dispose()


def test_postgres_experiment_stage_decision_is_durable_and_idempotent() -> None:
    database_url = os.environ["AUTODEV_DATABASE_URL"]
    engine = create_engine(database_url)
    repository = SqlExperimentRepository(engine)
    suffix = uuid4().hex
    experiment_id = f"experiment-{suffix}"
    operation_id = f"{experiment_id}:stage:0"
    initial = Experiment(
        id=experiment_id,
        target_id=f"target-{suffix}",
        control_release_id="release-1",
        candidate_deployment_id=f"deployment-{suffix}",
        stages=(
            CanaryStage(10, 60, 100),
            CanaryStage(100, 120, 200),
        ),
    )
    updated = Experiment(
        id=initial.id,
        target_id=initial.target_id,
        control_release_id=initial.control_release_id,
        candidate_deployment_id=initial.candidate_deployment_id,
        stages=initial.stages,
        current_stage_index=1,
    )
    decision = CanaryStageDecision(
        kind=CanaryDecisionKind.ADVANCE,
        experiment_id=experiment_id,
        stage_index=0,
        next_stage_index=1,
        evidence_refs=(f"canary:{suffix}:stage-0",),
        violated_guardrails=(),
        reason="stage passed",
    )

    try:
        repository.add(initial)
        first = repository.commit_stage_decision(
            updated,
            decision,
            expected_stage_index=0,
            operation_id=operation_id,
        )
        replay = repository.commit_stage_decision(
            updated,
            decision,
            expected_stage_index=0,
            operation_id=operation_id,
        )
        engine.dispose()

        reconnected = create_engine(database_url)
        try:
            recovered = SqlExperimentRepository(reconnected)
            persisted = recovered.get(experiment_id)
            history = recovered.list_stage_decisions(experiment_id)
            assert replay == first
            assert persisted == updated
            assert history == (first,)
        finally:
            reconnected.dispose()
    finally:
        engine.dispose()



def test_postgres_release_decision_receipt_survives_reconnect() -> None:
    database_url = os.environ["AUTODEV_DATABASE_URL"]
    engine = create_engine(database_url)
    repository = SqlReleaseDecisionRepository(engine)
    suffix = uuid4().hex
    operation_id = f"release-decision-{suffix}"
    decision = ReleaseDecision(
        kind=ReleaseDecisionKind.PROMOTE,
        cycle_id=f"cycle-{suffix}",
        gate_refs=("static", "tests"),
        evidence_refs=(f"canary:{suffix}:stage-0", f"canary:{suffix}:stage-1"),
        reason="all required evidence passed",
    )

    try:
        first = repository.record(operation_id, decision)
        replay = repository.record(operation_id, decision)
        engine.dispose()

        reconnected = create_engine(database_url)
        try:
            recovered = SqlReleaseDecisionRepository(reconnected).get(operation_id)
            assert replay == first
            assert recovered == first
        finally:
            reconnected.dispose()
    finally:
        engine.dispose()
