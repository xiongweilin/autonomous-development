from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import UserFeedback
from autonomous_development.ports.persistence import FeedbackRepository


class FeedbackAttributionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeedbackSubmission:
    id: str
    target_id: str
    received_at: datetime
    kind: FeedbackKind
    category: str
    severity: int
    provenance: str
    request_ref: str | None = None
    free_text: str | None = None
    reported_release_id: str | None = None
    reported_deployment_id: str | None = None


class FeedbackService:
    def __init__(
        self,
        releases: ReleaseCatalogService,
        repository: FeedbackRepository,
    ) -> None:
        self._releases = releases
        self._repository = repository

    def ingest(self, submission: FeedbackSubmission) -> UserFeedback:
        serving = self._releases.serving(submission.target_id)
        if serving is None:
            raise FeedbackAttributionError(
                f"target {submission.target_id} has no serving release"
            )
        if (
            submission.reported_release_id is not None
            and submission.reported_release_id != serving.id
        ):
            raise FeedbackAttributionError(
                "reported release does not match the server-side serving release"
            )
        if (
            submission.reported_deployment_id is not None
            and submission.reported_deployment_id != serving.deployment_id
        ):
            raise FeedbackAttributionError(
                "reported deployment does not match the server-side serving deployment"
            )

        feedback = UserFeedback(
            id=submission.id,
            target_id=submission.target_id,
            received_at=submission.received_at,
            kind=submission.kind,
            category=submission.category,
            severity=submission.severity,
            provenance=submission.provenance,
            release_id=serving.id,
            deployment_id=serving.deployment_id,
            request_ref=submission.request_ref,
            free_text=submission.free_text,
        )
        return self._repository.add(feedback)

    def get(self, feedback_id: str) -> UserFeedback | None:
        return self._repository.get(feedback_id)
