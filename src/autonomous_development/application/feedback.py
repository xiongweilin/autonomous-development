from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import UserFeedback
from autonomous_development.ports.persistence import (
    FeedbackRepository,
    RequestAttributionRepository,
)


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
        attributions: RequestAttributionRepository | None = None,
    ) -> None:
        self._releases = releases
        self._repository = repository
        self._attributions = attributions

    def ingest(self, submission: FeedbackSubmission) -> UserFeedback:
        release_id: str | None
        deployment_id: str | None
        experiment_id: str | None

        attribution = (
            self._attributions.get(submission.request_ref)
            if self._attributions is not None and submission.request_ref is not None
            else None
        )
        if self._attributions is not None and submission.request_ref is not None:
            if attribution is None:
                raise FeedbackAttributionError(
                    "request reference has no server-side traffic attribution"
                )
            if attribution.target_id != submission.target_id:
                raise FeedbackAttributionError(
                    "request reference belongs to another target"
                )
            release_id = attribution.release_id
            deployment_id = attribution.deployment_id
            experiment_id = attribution.experiment_id
            if release_id is not None:
                release = self._releases.get(release_id)
                if release.target_id != submission.target_id:
                    raise FeedbackAttributionError(
                        "attributed release belongs to another target"
                    )
                deployment_id = deployment_id or release.deployment_id
        else:
            serving = self._releases.serving(submission.target_id)
            if serving is None:
                raise FeedbackAttributionError(
                    f"target {submission.target_id} has no serving release"
                )
            release_id = serving.id
            deployment_id = serving.deployment_id
            experiment_id = None

        if (
            submission.reported_release_id is not None
            and submission.reported_release_id != release_id
        ):
            raise FeedbackAttributionError(
                "reported release does not match server-side attribution"
            )
        if (
            submission.reported_deployment_id is not None
            and submission.reported_deployment_id != deployment_id
        ):
            raise FeedbackAttributionError(
                "reported deployment does not match server-side attribution"
            )

        feedback = UserFeedback(
            id=submission.id,
            target_id=submission.target_id,
            received_at=submission.received_at,
            kind=submission.kind,
            category=submission.category,
            severity=submission.severity,
            provenance=submission.provenance,
            release_id=release_id,
            deployment_id=deployment_id,
            experiment_id=experiment_id,
            request_ref=submission.request_ref,
            free_text=submission.free_text,
        )
        return self._repository.add(feedback)

    def get(self, feedback_id: str) -> UserFeedback | None:
        return self._repository.get(feedback_id)
