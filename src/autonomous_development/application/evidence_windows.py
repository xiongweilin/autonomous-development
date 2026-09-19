from __future__ import annotations

from datetime import datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.models import EvidenceWindow, UserFeedback
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
        feedback_ids: tuple[str, ...] | None = None,
    ) -> EvidenceWindow:
        if not window_id.strip():
            raise ValueError("evidence window id must be non-empty")
        if closed_at < opened_at:
            raise ValueError("evidence window closes before it opens")
        if feedback_ids is not None and (
            not feedback_ids or len(set(feedback_ids)) != len(feedback_ids)
        ):
            raise ValueError("explicit feedback ids must be non-empty and unique")

        existing = self._repository.get(window_id)
        if existing is not None:
            _validate_existing_window(
                existing,
                target_id=target_id,
                opened_at=opened_at,
                closed_at=closed_at,
                feedback_ids=feedback_ids,
            )
            return existing

        serving = self._releases.serving(target_id)
        if serving is None:
            raise ValueError(f"target {target_id} has no serving release")
        if opened_at < serving.promoted_at:
            raise ValueError(
                "evidence window predates the current serving release and may mix releases"
            )

        feedback = (
            self._explicit_feedback(
                feedback_ids,
                target_id=target_id,
                release_id=serving.id,
                deployment_id=serving.deployment_id,
                opened_at=opened_at,
                closed_at=closed_at,
            )
            if feedback_ids is not None
            else self._feedback.list_attributable(
                target_id,
                serving.id,
                deployment_id=serving.deployment_id,
                opened_at=opened_at,
                closed_at=closed_at,
            )
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

    def _explicit_feedback(
        self,
        feedback_ids: tuple[str, ...],
        *,
        target_id: str,
        release_id: str,
        deployment_id: str,
        opened_at: datetime,
        closed_at: datetime,
    ) -> tuple[UserFeedback, ...]:
        items: list[UserFeedback] = []
        for feedback_id in feedback_ids:
            item = self._feedback.get(feedback_id)
            if item is None:
                raise ValueError(f"feedback does not exist: {feedback_id}")
            if item.target_id != target_id or (
                item.release_id != release_id
                and item.deployment_id != deployment_id
            ):
                raise ValueError("feedback is not attributable to the serving release")
            if not opened_at <= item.received_at <= closed_at:
                raise ValueError("feedback is outside the requested evidence window")
            items.append(item)
        return tuple(items)


def _validate_existing_window(
    window: EvidenceWindow,
    *,
    target_id: str,
    opened_at: datetime,
    closed_at: datetime,
    feedback_ids: tuple[str, ...] | None,
) -> None:
    if (
        window.target_id != target_id
        or window.opened_at != opened_at
        or window.closed_at != closed_at
    ):
        raise ValueError("evidence window id is already bound to different window identity")
    if feedback_ids is not None:
        expected = tuple(f"feedback:{feedback_id}" for feedback_id in feedback_ids)
        if window.feedback_refs != expected:
            raise ValueError("evidence window id is already bound to different feedback")
