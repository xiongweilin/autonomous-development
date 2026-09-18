from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.domain.transitions import transition_cycle
from autonomous_development.ports.repositories import CycleRepository

from .schema import CycleEventRecord, CycleRecord


class CycleNotFound(LookupError):
    pass


class CycleConflict(ValueError):
    pass


class SqlCycleRepository(CycleRepository):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, cycle: DevelopmentCycle) -> DevelopmentCycle:
        with self._session_factory.begin() as session:
            existing = session.get(CycleRecord, cycle.id)
            if existing is not None:
                current = _to_domain(existing)
                _validate_same_cycle_identity(current, cycle)
                return current
            session.add(_to_record(cycle))
        return cycle

    def get(self, cycle_id: str) -> DevelopmentCycle:
        with self._session_factory() as session:
            record = session.get(CycleRecord, cycle_id)
            if record is None:
                raise CycleNotFound(cycle_id)
            return _to_domain(record)

    def transition(
        self,
        cycle_id: str,
        to_state: CycleState,
        *,
        expected_version: int,
        operation_id: str,
        candidate_id: str | None = None,
        verification_run_id: str | None = None,
        artifact_id: str | None = None,
        candidate_deployment_id: str | None = None,
        experiment_id: str | None = None,
        release_decision: ReleaseDecisionKind | None = None,
    ) -> DevelopmentCycle:
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")

        with self._session_factory.begin() as session:
            replay = _event_for_operation(session, operation_id)
            if replay is not None:
                if replay.cycle_id != cycle_id:
                    raise CycleConflict("operation_id belongs to another cycle")
                return _cycle_from_snapshot(replay.snapshot_json)

            record = session.scalar(
                select(CycleRecord).where(CycleRecord.id == cycle_id).with_for_update()
            )
            if record is None:
                raise CycleNotFound(cycle_id)

            # Re-check after acquiring the row lock. Another process may have committed
            # the same operation while this transaction was waiting.
            replay = _event_for_operation(session, operation_id)
            if replay is not None:
                if replay.cycle_id != cycle_id:
                    raise CycleConflict("operation_id belongs to another cycle")
                return _cycle_from_snapshot(replay.snapshot_json)

            current = _to_domain(record)
            updated = transition_cycle(
                current,
                to_state,
                expected_version=expected_version,
                candidate_id=candidate_id,
                verification_run_id=verification_run_id,
                artifact_id=artifact_id,
                candidate_deployment_id=candidate_deployment_id,
                experiment_id=experiment_id,
                release_decision=release_decision,
            )
            _apply_domain(record, updated)
            session.add(
                CycleEventRecord(
                    operation_id=operation_id,
                    cycle_id=cycle_id,
                    from_version=current.version,
                    resulting_version=updated.version,
                    from_state=current.state.value,
                    to_state=updated.state.value,
                    snapshot_json=_snapshot(updated),
                    created_at=datetime.now(UTC),
                )
            )
            session.flush()
            return updated


def _event_for_operation(session: Session, operation_id: str) -> CycleEventRecord | None:
    return session.scalar(
        select(CycleEventRecord).where(CycleEventRecord.operation_id == operation_id)
    )


def _validate_same_cycle_identity(
    existing: DevelopmentCycle,
    requested: DevelopmentCycle,
) -> None:
    if (
        existing.target_id != requested.target_id
        or existing.objective_revision_id != requested.objective_revision_id
        or existing.baseline_release_id != requested.baseline_release_id
    ):
        raise CycleConflict("cycle id already exists with different immutable identity")


def _to_record(cycle: DevelopmentCycle) -> CycleRecord:
    return CycleRecord(
        id=cycle.id,
        target_id=cycle.target_id,
        objective_revision_id=cycle.objective_revision_id,
        baseline_release_id=cycle.baseline_release_id,
        state=cycle.state.value,
        version=cycle.version,
        candidate_id=cycle.candidate_id,
        verification_run_id=cycle.verification_run_id,
        artifact_id=cycle.artifact_id,
        candidate_deployment_id=cycle.candidate_deployment_id,
        experiment_id=cycle.experiment_id,
        release_decision=cycle.release_decision.value if cycle.release_decision else None,
        updated_at=datetime.now(UTC),
    )


def _apply_domain(record: CycleRecord, cycle: DevelopmentCycle) -> None:
    record.state = cycle.state.value
    record.version = cycle.version
    record.candidate_id = cycle.candidate_id
    record.verification_run_id = cycle.verification_run_id
    record.artifact_id = cycle.artifact_id
    record.candidate_deployment_id = cycle.candidate_deployment_id
    record.experiment_id = cycle.experiment_id
    record.release_decision = cycle.release_decision.value if cycle.release_decision else None
    record.updated_at = datetime.now(UTC)


def _to_domain(record: CycleRecord) -> DevelopmentCycle:
    return DevelopmentCycle(
        id=record.id,
        target_id=record.target_id,
        objective_revision_id=record.objective_revision_id,
        baseline_release_id=record.baseline_release_id,
        state=CycleState(record.state),
        version=record.version,
        candidate_id=record.candidate_id,
        verification_run_id=record.verification_run_id,
        artifact_id=record.artifact_id,
        candidate_deployment_id=record.candidate_deployment_id,
        experiment_id=record.experiment_id,
        release_decision=(
            ReleaseDecisionKind(record.release_decision) if record.release_decision else None
        ),
    )


def _snapshot(cycle: DevelopmentCycle) -> dict[str, Any]:
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
    }


def _cycle_from_snapshot(snapshot: dict[str, Any]) -> DevelopmentCycle:
    return DevelopmentCycle(
        id=str(snapshot["id"]),
        target_id=str(snapshot["target_id"]),
        objective_revision_id=str(snapshot["objective_revision_id"]),
        baseline_release_id=str(snapshot["baseline_release_id"]),
        state=CycleState(str(snapshot["state"])),
        version=int(snapshot["version"]),
        candidate_id=_optional_str(snapshot.get("candidate_id")),
        verification_run_id=_optional_str(snapshot.get("verification_run_id")),
        artifact_id=_optional_str(snapshot.get("artifact_id")),
        candidate_deployment_id=_optional_str(snapshot.get("candidate_deployment_id")),
        experiment_id=_optional_str(snapshot.get("experiment_id")),
        release_decision=(
            ReleaseDecisionKind(str(snapshot["release_decision"]))
            if snapshot.get("release_decision") is not None
            else None
        ),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)
