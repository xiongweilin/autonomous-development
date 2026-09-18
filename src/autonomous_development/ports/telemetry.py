from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TelemetryEvidence:
    evidence_refs: tuple[str, ...]
    missing_metrics: tuple[str, ...]


class TelemetryProvider(Protocol):
    def collect(
        self,
        *,
        target_id: str,
        release_id: str,
        opened_at: datetime,
        closed_at: datetime,
    ) -> TelemetryEvidence: ...
