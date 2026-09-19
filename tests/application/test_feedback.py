from datetime import UTC, datetime

import pytest

from autonomous_development.application.feedback import (
    FeedbackAttributionError,
    FeedbackService,
    FeedbackSubmission,
)
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import (
    ReleasedVersion,
    RequestAttribution,
    UserFeedback,
)
from autonomous_development.ports.persistence import ServingReleaseReceipt


class MemoryReleaseRepository:
    def __init__(self) -> None:
        self.releases: dict[str, ReleasedVersion] = {}
        self.serving_by_target: dict[str, str] = {}
        self.operations: dict[str, ServingReleaseReceipt] = {}

    def add(self, release: ReleasedVersion) -> ReleasedVersion:
        existing = self.releases.get(release.id)
        if existing is not None and existing != release:
            raise ValueError("conflicting release")
        self.releases[release.id] = release
        return release

    def get(self, release_id: str) -> ReleasedVersion | None:
        return self.releases.get(release_id)

    def get_serving(self, target_id: str) -> ReleasedVersion | None:
        release_id = self.serving_by_target.get(target_id)
        return self.releases.get(release_id) if release_id is not None else None

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ServingReleaseReceipt:
        existing = self.operations.get(operation_id)
        if existing is not None:
            if existing.target_id != target_id or existing.release_id != release_id:
                raise ValueError("operation conflict")
            return existing
        previous = self.serving_by_target.get(target_id)
        receipt = ServingReleaseReceipt(
            operation_id=operation_id,
            target_id=target_id,
            release_id=release_id,
            previous_release_id=previous,
        )
        self.operations[operation_id] = receipt
        self.serving_by_target[target_id] = release_id
        return receipt


class MemoryAttributionRepository:
    def __init__(self) -> None:
        self.items: dict[str, RequestAttribution] = {}

    def add(self, attribution: RequestAttribution) -> RequestAttribution:
        self.items[attribution.request_ref] = attribution
        return attribution

    def get(self, request_ref: str) -> RequestAttribution | None:
        return self.items.get(request_ref)


class MemoryFeedbackRepository:
    def __init__(self) -> None:
        self.items: dict[str, UserFeedback] = {}

    def add(self, feedback: UserFeedback) -> UserFeedback:
        existing = self.items.get(feedback.id)
        if existing is not None and existing != feedback:
            raise ValueError("feedback conflict")
        self.items[feedback.id] = feedback
        return feedback

    def get(self, feedback_id: str) -> UserFeedback | None:
        return self.items.get(feedback_id)

    def list_attributable(self, *args, **kwargs) -> tuple[UserFeedback, ...]:
        return tuple(self.items.values())


def release(release_id: str = "release-1") -> ReleasedVersion:
    return ReleasedVersion(
        id=release_id,
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-1",
        promoted_at=datetime.now(UTC),
    )


def submission(**updates: object) -> FeedbackSubmission:
    values: dict[str, object] = {
        "id": "feedback-1",
        "target_id": "target-1",
        "received_at": datetime.now(UTC),
        "kind": FeedbackKind.EXPLICIT,
        "category": "incorrect-result",
        "severity": 3,
        "provenance": "feedback-api",
        "request_ref": "request-1",
        "free_text": "the answer was incorrect",
    }
    values.update(updates)
    return FeedbackSubmission(**values)  # type: ignore[arg-type]


def configured_service() -> tuple[FeedbackService, ReleaseCatalogService]:
    releases = ReleaseCatalogService(MemoryReleaseRepository())
    releases.register(release())
    releases.set_serving("target-1", "release-1", operation_id="serve-1")
    return FeedbackService(releases, MemoryFeedbackRepository()), releases


def test_feedback_is_bound_to_server_side_serving_release() -> None:
    service, _ = configured_service()
    feedback = service.ingest(submission())
    assert feedback.release_id == "release-1"
    assert feedback.deployment_id == "deployment-1"
    assert feedback.attributable


def test_mismatched_reported_release_is_rejected() -> None:
    service, _ = configured_service()
    with pytest.raises(FeedbackAttributionError, match="reported release"):
        service.ingest(submission(reported_release_id="attacker-chosen-release"))


def test_mismatched_reported_deployment_is_rejected() -> None:
    service, _ = configured_service()
    with pytest.raises(FeedbackAttributionError, match="reported deployment"):
        service.ingest(submission(reported_deployment_id="attacker-chosen-deployment"))


def test_feedback_without_serving_release_is_rejected() -> None:
    releases = ReleaseCatalogService(MemoryReleaseRepository())
    service = FeedbackService(releases, MemoryFeedbackRepository())
    with pytest.raises(FeedbackAttributionError, match="no serving release"):
        service.ingest(submission())


def test_feedback_free_text_is_bounded() -> None:
    service, _ = configured_service()
    with pytest.raises(ValueError, match="free text"):
        service.ingest(submission(free_text="x" * 4001))


def test_candidate_feedback_uses_server_observed_request_attribution() -> None:
    releases = ReleaseCatalogService(MemoryReleaseRepository())
    releases.register(release())
    releases.set_serving("target-1", "release-1", operation_id="serve-1")
    attributions = MemoryAttributionRepository()
    attributions.add(
        RequestAttribution(
            request_ref="request-candidate",
            target_id="target-1",
            observed_at=datetime.now(UTC),
            arm="candidate",
            experiment_id="experiment-1",
            deployment_id="deployment-2",
        )
    )
    service = FeedbackService(
        releases,
        MemoryFeedbackRepository(),
        attributions,
    )

    feedback = service.ingest(
        submission(
            request_ref="request-candidate",
            reported_deployment_id="deployment-2",
        )
    )

    assert feedback.release_id is None
    assert feedback.deployment_id == "deployment-2"
    assert feedback.experiment_id == "experiment-1"


def test_unknown_request_reference_fails_closed_when_attribution_is_enabled() -> None:
    releases = ReleaseCatalogService(MemoryReleaseRepository())
    releases.register(release())
    releases.set_serving("target-1", "release-1", operation_id="serve-1")
    service = FeedbackService(
        releases,
        MemoryFeedbackRepository(),
        MemoryAttributionRepository(),
    )

    with pytest.raises(FeedbackAttributionError, match="no server-side traffic attribution"):
        service.ingest(submission(request_ref="unknown-request"))
