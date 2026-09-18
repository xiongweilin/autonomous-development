from __future__ import annotations

from typing import Protocol

from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import DevelopmentCycle


class CycleRepository(Protocol):
    def create(self, cycle: DevelopmentCycle) -> DevelopmentCycle: ...

    def get(self, cycle_id: str) -> DevelopmentCycle: ...

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
    ) -> DevelopmentCycle: ...
