from __future__ import annotations

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.domain.transitions import TERMINAL_STATES
from autonomous_development.ports.persistence import (
    ConcurrentCycleError,
    ConcurrentUpdateError,
    CycleRepository,
    OperationConflictError,
    TransitionReceipt,
)

from .schema import cycle_transitions, cycles


class SqlCycleRepository(CycleRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, cycle: DevelopmentCycle) -> DevelopmentCycle:
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(cycles).values(**_cycle_values(cycle)))
        except IntegrityError as exc:
            if self.get(cycle.id) is not None:
                raise ValueError(f"cycle already exists: {cycle.id}") from exc
            active = self.find_active_for_target(cycle.target_id)
            if active is not None:
                raise ConcurrentCycleError(
                    f"target {cycle.target_id} already has active cycle {active.id}"
                ) from exc
            raise
        return cycle

    def get(self, cycle_id: str) -> DevelopmentCycle | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(cycles).where(cycles.c.id == cycle_id))
                .mappings()
                .first()
            )
        return _cycle_from_row(row) if row is not None else None

    def find_active_for_target(self, target_id: str) -> DevelopmentCycle | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(cycles).where(
                    cycles.c.target_id == target_id,
                    cycles.c.is_terminal.is_(False),
                )
            ).mappings().first()
        return _cycle_from_row(row) if row is not None else None

    def get_transition(self, operation_id: str) -> TransitionReceipt | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(cycle_transitions).where(
                    cycle_transitions.c.operation_id == operation_id
                )
            ).mappings().first()
        return _receipt_from_row(row) if row is not None else None

    def commit_transition(
        self,
        cycle: DevelopmentCycle,
        *,
        expected_version: int,
        operation_id: str,
        expected_to_state: CycleState,
    ) -> TransitionReceipt:
        existing = self.get_transition(operation_id)
        if existing is not None:
            return _validate_existing_receipt(
                existing,
                cycle=cycle,
                expected_version=expected_version,
                expected_to_state=expected_to_state,
            )

        receipt = TransitionReceipt(
            operation_id=operation_id,
            cycle_id=cycle.id,
            from_version=expected_version,
            result_version=cycle.version,
            to_state=expected_to_state,
        )
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(cycle_transitions).values(**_receipt_values(receipt))
                )
                result = connection.execute(
                    update(cycles)
                    .where(cycles.c.id == cycle.id, cycles.c.version == expected_version)
                    .values(**_cycle_values(cycle))
                )
                if result.rowcount != 1:
                    raise ConcurrentUpdateError(
                        f"cycle {cycle.id} no longer matches version {expected_version}"
                    )
        except IntegrityError as exc:
            existing = self.get_transition(operation_id)
            if existing is None:
                raise
            try:
                return _validate_existing_receipt(
                    existing,
                    cycle=cycle,
                    expected_version=expected_version,
                    expected_to_state=expected_to_state,
                )
            except OperationConflictError as conflict:
                raise conflict from exc
        return receipt


def _validate_existing_receipt(
    receipt: TransitionReceipt,
    *,
    cycle: DevelopmentCycle,
    expected_version: int,
    expected_to_state: CycleState,
) -> TransitionReceipt:
    if (
        receipt.cycle_id != cycle.id
        or receipt.from_version != expected_version
        or receipt.result_version != cycle.version
        or receipt.to_state is not expected_to_state
    ):
        raise OperationConflictError(
            f"operation id {receipt.operation_id} is already bound to another transition"
        )
    return receipt


def _cycle_values(cycle: DevelopmentCycle) -> dict[str, object]:
    return {
        "id": cycle.id,
        "target_id": cycle.target_id,
        "objective_revision_id": cycle.objective_revision_id,
        "baseline_release_id": cycle.baseline_release_id,
        "state": cycle.state.value,
        "version": cycle.version,
        "candidate_id": cycle.candidate_id,
        "verification_run_id": cycle.verification_run_id,
        "artifact_id": cycle.artifact_id,
        "candidate_deployment_id": cycle.candidate_deployment_id,
        "experiment_id": cycle.experiment_id,
        "release_decision": cycle.release_decision.value if cycle.release_decision else None,
        "is_terminal": cycle.state in TERMINAL_STATES,
    }


def _receipt_values(receipt: TransitionReceipt) -> dict[str, object]:
    return {
        "operation_id": receipt.operation_id,
        "cycle_id": receipt.cycle_id,
        "from_version": receipt.from_version,
        "result_version": receipt.result_version,
        "to_state": receipt.to_state.value,
    }


def _cycle_from_row(row: RowMapping) -> DevelopmentCycle:
    values = dict(row)
    decision = values.get("release_decision")
    return DevelopmentCycle(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        objective_revision_id=str(values["objective_revision_id"]),
        baseline_release_id=str(values["baseline_release_id"]),
        state=CycleState(str(values["state"])),
        version=int(values["version"]),
        candidate_id=_optional_str(values.get("candidate_id")),
        verification_run_id=_optional_str(values.get("verification_run_id")),
        artifact_id=_optional_str(values.get("artifact_id")),
        candidate_deployment_id=_optional_str(values.get("candidate_deployment_id")),
        experiment_id=_optional_str(values.get("experiment_id")),
        release_decision=ReleaseDecisionKind(str(decision)) if decision else None,
    )


def _receipt_from_row(row: RowMapping) -> TransitionReceipt:
    values = dict(row)
    return TransitionReceipt(
        operation_id=str(values["operation_id"]),
        cycle_id=str(values["cycle_id"]),
        from_version=int(values["from_version"]),
        result_version=int(values["result_version"]),
        to_state=CycleState(str(values["to_state"])),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
