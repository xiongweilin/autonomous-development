from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from autonomous_development.adapters.postgres.repository import SqlCycleRepository
from autonomous_development.adapters.postgres.schema import Base
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.workflows.development_cycle import (
    bootstrap_cycle_workflow,
    configure_cycle_workflow_repository,
)


@pytest.fixture()
def durable_runtime(tmp_path: Path) -> Iterator[SqlCycleRepository]:
    DBOS.destroy()
    app_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'app.db'}")
    Base.metadata.create_all(app_engine)
    factory = sessionmaker(bind=app_engine, expire_on_commit=False, class_=Session)
    repository = SqlCycleRepository(factory)
    configure_cycle_workflow_repository(repository)

    config: DBOSConfig = {
        "name": "autonomous-development",
        "application_version": "test",
        "system_database_url": f"sqlite:///{tmp_path / 'dbos.db'}",
    }
    DBOS(config=config)
    DBOS.launch()
    yield repository
    DBOS.destroy(workflow_completion_timeout_sec=5)


def test_workflow_id_is_idempotent(durable_runtime: SqlCycleRepository) -> None:
    durable_runtime.create(
        DevelopmentCycle(
            id="cycle-workflow",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    with SetWorkflowID("wf-cycle-workflow"):
        first = bootstrap_cycle_workflow("cycle-workflow")
    with SetWorkflowID("wf-cycle-workflow"):
        replay = bootstrap_cycle_workflow("cycle-workflow")

    assert first == replay
    stored = durable_runtime.get("cycle-workflow")
    assert stored.state is CycleState.BASELINE_VERIFIED
    assert stored.version == 1
