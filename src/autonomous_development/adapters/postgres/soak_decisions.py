from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.enums import SoakDecisionKind
from autonomous_development.domain.soak import PostPromotionSoakDecision
from autonomous_development.ports.persistence import (
    OperationConflictError,
    SoakDecisionReceipt,
    SoakDecisionRepository,
)

from .schema import soak_decision_operations


class SqlSoakDecisionRepository(SoakDecisionRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, operation_id: str) -> SoakDecisionReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(soak_decision_operations).where(
                        soak_decision_operations.c.operation_id == operation_id
                    )
                )
                .mappings()
                .first()
            )
        return _receipt_from_row(row) if row is not None else None

    def record(
        self,
        operation_id: str,
        cycle_id: str,
        decision: PostPromotionSoakDecision,
    ) -> SoakDecisionReceipt:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")
        if not cycle_id.strip():
            raise ValueError("cycle_id must be non-empty")
        receipt = SoakDecisionReceipt(
            operation_id=operation_id,
            cycle_id=cycle_id,
            decision=decision,
        )
        existing = self.get(operation_id)
        if existing is not None:
            return _validate_existing(existing, receipt)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(soak_decision_operations).values(
                        operation_id=operation_id,
                        cycle_id=cycle_id,
                        decision_kind=decision.kind.value,
                        evidence_refs_json=list(decision.evidence_refs),
                        violated_guardrails_json=list(decision.violated_guardrails),
                        reason=decision.reason,
                    )
                )
        except IntegrityError as exc:
            existing = self.get(operation_id)
            if existing is None:
                raise
            try:
                return _validate_existing(existing, receipt)
            except OperationConflictError as conflict:
                raise conflict from exc
        return receipt


def _validate_existing(
    existing: SoakDecisionReceipt,
    requested: SoakDecisionReceipt,
) -> SoakDecisionReceipt:
    if existing != requested:
        raise OperationConflictError(
            f"operation id {requested.operation_id} is already bound to another soak decision"
        )
    return existing


def _receipt_from_row(row: RowMapping) -> SoakDecisionReceipt:
    values = dict(row)
    evidence_refs = values["evidence_refs_json"]
    violated = values["violated_guardrails_json"]
    if not isinstance(evidence_refs, list) or not isinstance(violated, list):
        raise RuntimeError("persisted soak decision receipt is malformed")
    return SoakDecisionReceipt(
        operation_id=str(values["operation_id"]),
        cycle_id=str(values["cycle_id"]),
        decision=PostPromotionSoakDecision(
            kind=SoakDecisionKind(str(values["decision_kind"])),
            evidence_refs=tuple(str(item) for item in evidence_refs),
            violated_guardrails=tuple(str(item) for item in violated),
            reason=str(values["reason"]),
        ),
    )
