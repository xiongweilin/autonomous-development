from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.ports.persistence import OperationConflictError


def make_service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'app.db'}")
    metadata.create_all(engine)
    return CycleService(SqlCycleRepository(engine))


def make_cycle(cycle_id: str = "c1", target_id: str = "t1") -> DevelopmentCycle:
    return DevelopmentCycle(
        id=cycle_id,
        target_id=target_id,
        objective_revision_id="o1",
        baseline_release_id="r1",
    )


def test_transition_operation_is_idempotent(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create(make_cycle())

    first = service.transition(
        "c1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-1",
    )
    second = service.transition(
        "c1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-1",
    )

    assert first.version == 1
    assert second == first


def test_operation_id_cannot_be_rebound(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create(make_cycle())
    service.transition(
        "c1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-1",
    )

    try:
        service.transition(
            "c1",
            CycleState.BLOCKED,
            expected_version=0,
            operation_id="op-1",
        )
    except OperationConflictError:
        return
    raise AssertionError("operation id was rebound")


def test_state_survives_repository_recreation(tmp_path) -> None:
    database = tmp_path / "app.db"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    metadata.create_all(engine)
    service = CycleService(SqlCycleRepository(engine))
    service.create(make_cycle())
    service.transition(
        "c1",
        CycleState.BASELINE_VERIFIED,
        expected_version=0,
        operation_id="op-1",
    )
    engine.dispose()

    reopened_engine = create_engine(f"sqlite+pysqlite:///{database}")
    reopened = CycleService(SqlCycleRepository(reopened_engine)).get("c1")
    assert reopened.state is CycleState.BASELINE_VERIFIED
    assert reopened.version == 1
