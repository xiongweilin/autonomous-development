from __future__ import annotations

from dbos import DBOS

from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.ports.repositories import CycleRepository

_cycle_repository: CycleRepository | None = None


def configure_cycle_workflow_repository(repository: CycleRepository) -> None:
    global _cycle_repository
    _cycle_repository = repository


def _repository() -> CycleRepository:
    if _cycle_repository is None:
        raise RuntimeError("cycle workflow repository is not configured")
    return _cycle_repository


@DBOS.step(name="autodev-baseline-verified", retries_allowed=False)
def _baseline_verified_step(cycle_id: str, expected_version: int) -> dict[str, object]:
    updated = _repository().transition(
        cycle_id,
        CycleState.BASELINE_VERIFIED,
        expected_version=expected_version,
        operation_id=f"baseline-verified:{cycle_id}:{expected_version}",
    )
    return _cycle_payload(updated)


@DBOS.workflow(name="autodev-bootstrap-cycle", max_recovery_attempts=20)
def bootstrap_cycle_workflow(cycle_id: str, expected_version: int = 0) -> dict[str, object]:
    return _baseline_verified_step(cycle_id, expected_version)


def _cycle_payload(cycle: DevelopmentCycle) -> dict[str, object]:
    return {
        "cycle_id": cycle.id,
        "state": cycle.state.value,
        "version": cycle.version,
    }
