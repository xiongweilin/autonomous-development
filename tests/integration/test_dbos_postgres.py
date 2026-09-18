from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from autonomous_development.adapters.postgres.repository import SqlCycleRepository
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.workflows.development_cycle import (
    bootstrap_cycle_workflow,
    configure_cycle_workflow_repository,
)

DATABASE_URL = os.environ.get("AUTODEV_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="AUTODEV_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def postgres_runtime() -> Iterator[SqlCycleRepository]:
    assert DATABASE_URL is not None
    DBOS.destroy()

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE cycle_events, development_cycles RESTART IDENTITY"))
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    repository = SqlCycleRepository(factory)
    configure_cycle_workflow_repository(repository)

    config: DBOSConfig = {
        "name": "autonomous-development",
        "application_version": "integration-test",
        "system_database_url": DATABASE_URL,
    }
    DBOS(config=config)
    DBOS.launch()
    yield repository
    DBOS.destroy(workflow_completion_timeout_sec=5)


def test_completed_workflow_is_retrievable_after_runtime_restart(
    postgres_runtime: SqlCycleRepository,
) -> None:
    postgres_runtime.create(
        DevelopmentCycle(
            id="cycle-restart",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    workflow_id = "wf-cycle-restart"
    with SetWorkflowID(workflow_id):
        result = bootstrap_cycle_workflow("cycle-restart")

    DBOS.destroy(workflow_completion_timeout_sec=5)
    assert DATABASE_URL is not None
    config: DBOSConfig = {
        "name": "autonomous-development",
        "application_version": "integration-test",
        "system_database_url": DATABASE_URL,
    }
    DBOS(config=config)
    DBOS.launch()

    recovered = DBOS.retrieve_workflow(workflow_id).get_result()
    assert recovered == result
    assert postgres_runtime.get("cycle-restart").state is CycleState.BASELINE_VERIFIED
