import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.feedback_triggers import (
    SqlFeedbackTriggerRepository,
)
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.ports.persistence import (
    FeedbackTriggerReceipt,
    OperationConflictError,
)


def receipt(*, outcome: str = "prepared") -> FeedbackTriggerReceipt:
    return FeedbackTriggerReceipt(
        feedback_id="feedback-1",
        target_id="target-1",
        release_id="release-1",
        evidence_window_id="window-1",
        cycle_id="cycle-1",
        proposal_id="proposal-1" if outcome == "prepared" else None,
        outcome=outcome,
    )


def test_feedback_trigger_receipt_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlFeedbackTriggerRepository(engine)

    first = repository.record(receipt())
    replay = repository.record(receipt())

    assert replay == first
    assert repository.get("feedback-1") == first


def test_feedback_trigger_cannot_be_rebound() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlFeedbackTriggerRepository(engine)
    repository.record(receipt())

    with pytest.raises(OperationConflictError):
        repository.record(receipt(outcome="blocked-low-confidence"))
