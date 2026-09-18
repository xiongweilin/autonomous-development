import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.soak_decisions import SqlSoakDecisionRepository
from autonomous_development.domain.enums import SoakDecisionKind
from autonomous_development.domain.soak import PostPromotionSoakDecision
from autonomous_development.ports.persistence import OperationConflictError


def decision(kind: SoakDecisionKind = SoakDecisionKind.COMPLETE) -> PostPromotionSoakDecision:
    violations = ("candidate_error_rate",) if kind is SoakDecisionKind.ROLLBACK else ()
    return PostPromotionSoakDecision(
        kind=kind,
        evidence_refs=("soak:1",),
        violated_guardrails=violations,
        reason="decision",
    )


def test_soak_decision_receipt_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlSoakDecisionRepository(engine)

    first = repository.record("op-1", "cycle-1", decision())
    replay = repository.record("op-1", "cycle-1", decision())

    assert replay == first
    assert repository.get("op-1") == first


def test_soak_operation_id_cannot_be_rebound() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlSoakDecisionRepository(engine)
    repository.record("op-1", "cycle-1", decision())

    with pytest.raises(OperationConflictError):
        repository.record(
            "op-1",
            "cycle-1",
            decision(SoakDecisionKind.ROLLBACK),
        )
