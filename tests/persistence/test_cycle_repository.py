from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from autonomous_development.adapters.postgres.repository import (
    CycleConflict,
    SqlCycleRepository,
)
from autonomous_development.adapters.postgres.schema import Base
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.domain.transitions import StaleCycleError


@pytest.fixture()
def repository(tmp_path: Path) -> SqlCycleRepository:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'app.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlCycleRepository(factory)


def new_cycle() -> DevelopmentCycle:
    return DevelopmentCycle(
        id="cycle-1",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-1",
    )


def test_create_is_idempotent_for_same_identity(repository: SqlCycleRepository) -> None:
    created = repository.create(new_cycle())
    replayed = repository.create(new_cycle())
    assert created == replayed


def test_create_rejects_identity_collision(repository: SqlCycleRepository) -> None:
    repository.create(new_cycle())
    conflicting = DevelopmentCycle(
        id="cycle-1",
        target_id="other-target",
        objective_revision_id="objective-1",
        baseline_release_id="release-1",
    )
    with pytest.raises(CycleConflict):
        repository.create(conflicting)


def test_transition_is_durable_and_idempotent(repository: SqlCycleRepository) -> None:
    repository.create(new_cycle())

    first = repository.transition(
        "cycle-1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-baseline",
    )
    replay = repository.transition(
        "cycle-1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-baseline",
    )

    assert first == replay
    assert repository.get("cycle-1") == first
    assert first.version == 1


def test_stale_distinct_operation_fails_closed(repository: SqlCycleRepository) -> None:
    repository.create(new_cycle())
    repository.transition(
        "cycle-1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-1",
    )

    with pytest.raises(StaleCycleError):
        repository.transition(
            "cycle-1",
            CycleState.BASELINE_VERIFIED,
            expected_version=0,
            operation_id="op-2",
        )
