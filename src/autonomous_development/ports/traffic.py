from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TrafficSplit:
    experiment_id: str
    stage_index: int
    control_base_url: str
    candidate_base_url: str
    candidate_weight_percent: int
    operation_id: str

    def __post_init__(self) -> None:
        for label, value in (
            ("experiment_id", self.experiment_id),
            ("control_base_url", self.control_base_url),
            ("candidate_base_url", self.candidate_base_url),
            ("operation_id", self.operation_id),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if self.stage_index < 0:
            raise ValueError("stage_index cannot be negative")
        if not 0 <= self.candidate_weight_percent <= 100:
            raise ValueError("candidate traffic weight must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class TrafficRouteState:
    experiment_id: str
    stage_index: int
    candidate_weight_percent: int
    generation: int
    evidence_ref: str


class TrafficDirector(Protocol):
    def apply(self, split: TrafficSplit) -> TrafficRouteState: ...

    def restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> TrafficRouteState: ...
