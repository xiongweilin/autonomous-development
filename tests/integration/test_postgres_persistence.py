from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from autonomous_development.adapters.postgres.repository import SqlCycleRepository
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle

DATABASE_URL = os.environ.get("AUTODEV_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="AUTODEV_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def repository() -> SqlCycleRepository:
    assert DATABASE_URL is not None
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE cycle_events, development_cycles RESTART IDENTITY"))
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlCycleRepository(factory)


def test_concurrent_duplicate_operation_converges(repository: SqlCycleRepository) -> None:
    repository.create(
        DevelopmentCycle(
            id="cycle-concurrent",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    def apply() -> DevelopmentCycle:
        return repository.transition(
            "cycle-concurrent",
            CycleState.BASELINE_VERIFIED,
            expected_version=0,
            operation_id="op-concurrent",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: apply(), range(2)))

    assert results[0] == results[1]
    assert results[0].version == 1
    assert repository.get("cycle-concurrent").version == 1
