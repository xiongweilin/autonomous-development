from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle


@dataclass(frozen=True, slots=True)
class TransitionReceipt:
    operation_id: str
    cycle_id: str
    from_version: int
    result_version: int
    to_state: CycleState


class ConcurrentCycleError(RuntimeError):
    """A target already has another active mutating cycle."""


class ConcurrentUpdateError(RuntimeError):
    """The persisted cycle no longer matches the expected version."""


class OperationConflictError(RuntimeError):
    """An operation id was reused with incompatible semantics."""


class CycleRepository(Protocol):
    def add(self, cycle: DevelopmentCycle) -> DevelopmentCycle: ...

    def get(self, cycle_id: str) -> DevelopmentCycle | None: ...

    def find_active_for_target(self, target_id: str) -> DevelopmentCycle | None: ...

    def get_transition(self, operation_id: str) -> TransitionReceipt | None: ...

    def commit_transition(
        self,
        cycle: DevelopmentCycle,
        *,
        expected_version: int,
        operation_id: str,
        expected_to_state: CycleState,
    ) -> TransitionReceipt: ...
