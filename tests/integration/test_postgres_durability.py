from __future__ import annotations

import os
from uuid import uuid4

import pytest
from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
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
