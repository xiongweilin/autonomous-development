from __future__ import annotations

from datetime import datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.models import EvidenceWindow
from autonomous_development.ports.persistence import (
    EvidenceWindowRepository,
    FeedbackRepository,
)
from autonomous_development.ports.telemetry import TelemetryProvider


class EvidenceWindowService:
    def __init__(
        self,
        releases: ReleaseCatalogService,
        feedback: FeedbackRepository,
        telemetry: TelemetryProvider,
        repository: EvidenceWindowRepository,
    ) -> None:
        self._releases = releases
        self._feedback = feedback
        self._telemetry = telemetry
        self._repository = repository

    def close(
        self,
        *,
        window_id: str,
        target_id: str,
        opened_at: datetime,
        closed_at: datetime,
    ) -> EvidenceWindow:
        if not window_id.strip():
            raise ValueError("evidence window id must be non-empty")
        if closed_at < opened_at:
            raise ValueError("evidence window closes before it opens")

        serving = self._releases.serving(target_id)
        if serving is None:
            raise ValueError(f"target {target_id} has no serving release")
        if opened_at < serving.promoted_at:
            raise ValueError(
                "evidence window predates the current serving release and may mix releases"
            )

        feedback = self._feedback.list_attributable(
            target_id,
            serving.id,
            opened_at=opened_at,
            closed_at=closed_at,
        )
        telemetry = self._telemetry.collect(
            target_id=target_id,
            release_id=serving.id,
            opened_at=opened_at,
            closed_at=closed_at,
        )
        window = EvidenceWindow(
            id=window_id,
            target_id=target_id,
            release_ids=(serving.id,),
            opened_at=opened_at,
            closed_at=closed_at,
            telemetry_refs=telemetry.evidence_refs,
            feedback_refs=tuple(f"feedback:{item.id}" for item in feedback),
            missing_evidence=tuple(
                f"telemetry:{metric}" for metric in telemetry.missing_metrics
            ),
        )
        return self._repository.add(window)

    def get(self, window_id: str) -> EvidenceWindow | None:
        return self._repository.get(window_id)
