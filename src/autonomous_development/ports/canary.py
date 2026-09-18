from __future__ import annotations

from typing import Protocol

from autonomous_development.domain.canary import CanaryStageEvidence
from autonomous_development.domain.models import CanaryStage
from autonomous_development.ports.traffic import TrafficRouteState


class CanaryObserver(Protocol):
    def observe(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        stage: CanaryStage,
        route_state: TrafficRouteState,
    ) -> CanaryStageEvidence: ...
