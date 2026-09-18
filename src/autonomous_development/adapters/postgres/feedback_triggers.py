from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.ports.persistence import (
    FeedbackTriggerReceipt,
    FeedbackTriggerRepository,
    OperationConflictError,
)

from .schema import feedback_iteration_triggers


class SqlFeedbackTriggerRepository(FeedbackTriggerRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, feedback_id: str) -> FeedbackTriggerReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(feedback_iteration_triggers).where(
                        feedback_iteration_triggers.c.feedback_id == feedback_id
                    )
                )
                .mappings()
                .first()
            )
        return _receipt_from_row(row) if row is not None else None

    def record(self, receipt: FeedbackTriggerReceipt) -> FeedbackTriggerReceipt:
        existing = self.get(receipt.feedback_id)
        if existing is not None:
            return _validate_existing(existing, receipt)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(feedback_iteration_triggers).values(
                        feedback_id=receipt.feedback_id,
                        target_id=receipt.target_id,
                        release_id=receipt.release_id,
                        evidence_window_id=receipt.evidence_window_id,
                        cycle_id=receipt.cycle_id,
                        proposal_id=receipt.proposal_id,
                        outcome=receipt.outcome,
                    )
                )
        except IntegrityError as exc:
            existing = self.get(receipt.feedback_id)
            if existing is None:
                raise
            try:
                return _validate_existing(existing, receipt)
            except OperationConflictError as conflict:
                raise conflict from exc
        return receipt


def _validate_existing(
    existing: FeedbackTriggerReceipt,
    requested: FeedbackTriggerReceipt,
) -> FeedbackTriggerReceipt:
    if existing != requested:
        raise OperationConflictError(
            f"feedback {requested.feedback_id} is already bound to another iteration trigger"
        )
    return existing


def _receipt_from_row(row: RowMapping) -> FeedbackTriggerReceipt:
    values = dict(row)
    proposal_id = values.get("proposal_id")
    return FeedbackTriggerReceipt(
        feedback_id=str(values["feedback_id"]),
        target_id=str(values["target_id"]),
        release_id=str(values["release_id"]),
        evidence_window_id=str(values["evidence_window_id"]),
        cycle_id=str(values["cycle_id"]),
        proposal_id=str(proposal_id) if proposal_id is not None else None,
        outcome=str(values["outcome"]),
    )
