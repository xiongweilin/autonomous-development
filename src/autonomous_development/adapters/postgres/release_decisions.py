from __future__ import annotations

from sqlalchemy import Engine, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.enums import ReleaseDecisionKind
from autonomous_development.domain.models import ReleaseDecision
from autonomous_development.ports.persistence import (
    OperationConflictError,
    ReleaseDecisionReceipt,
    ReleaseDecisionRepository,
)

from .schema import release_decision_operations


class SqlReleaseDecisionRepository(ReleaseDecisionRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, operation_id: str) -> ReleaseDecisionReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(release_decision_operations).where(
                        release_decision_operations.c.operation_id == operation_id
                    )
                )
                .mappings()
                .first()
            )
        return _receipt_from_row(row) if row is not None else None

    def record(
        self,
        operation_id: str,
        decision: ReleaseDecision,
    ) -> ReleaseDecisionReceipt:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")
        receipt = ReleaseDecisionReceipt(operation_id=operation_id, decision=decision)
        existing = self.get(operation_id)
        if existing is not None:
            return _validate_existing(existing, receipt)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(release_decision_operations).values(
                        operation_id=operation_id,
                        cycle_id=decision.cycle_id,
                        decision_kind=decision.kind.value,
                        gate_refs_json=list(decision.gate_refs),
                        evidence_refs_json=list(decision.evidence_refs),
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
    existing: ReleaseDecisionReceipt,
    requested: ReleaseDecisionReceipt,
) -> ReleaseDecisionReceipt:
    if existing != requested:
        raise OperationConflictError(
            f"operation id {requested.operation_id} is already bound to another release decision"
        )
    return existing


def _receipt_from_row(row: RowMapping) -> ReleaseDecisionReceipt:
    values = dict(row)
    gate_refs = values["gate_refs_json"]
    evidence_refs = values["evidence_refs_json"]
    if not isinstance(gate_refs, list) or not isinstance(evidence_refs, list):
        raise RuntimeError("persisted release decision receipt is malformed")
    return ReleaseDecisionReceipt(
        operation_id=str(values["operation_id"]),
        decision=ReleaseDecision(
            kind=ReleaseDecisionKind(str(values["decision_kind"])),
            cycle_id=str(values["cycle_id"]),
            gate_refs=tuple(str(item) for item in gate_refs),
            evidence_refs=tuple(str(item) for item in evidence_refs),
            reason=str(values["reason"]),
        ),
    )
