from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autonomous_development.api.feedback import create_feedback_router
from autonomous_development.application.feedback import FeedbackService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.models import ReleasedVersion, UserFeedback
from autonomous_development.ports.persistence import ServingReleaseReceipt


class MemoryReleaseRepository:
    def __init__(self) -> None:
        self.releases: dict[str, ReleasedVersion] = {}
        self.serving: dict[str, str] = {}

    def add(self, release: ReleasedVersion) -> ReleasedVersion:
        self.releases[release.id] = release
        return release

    def get(self, release_id: str) -> ReleasedVersion | None:
        return self.releases.get(release_id)

    def get_serving(self, target_id: str) -> ReleasedVersion | None:
        release_id = self.serving.get(target_id)
        return self.releases.get(release_id) if release_id else None

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ServingReleaseReceipt:
        previous = self.serving.get(target_id)
        self.serving[target_id] = release_id
        return ServingReleaseReceipt(
            operation_id=operation_id,
            target_id=target_id,
            release_id=release_id,
            previous_release_id=previous,
        )


class MemoryFeedbackRepository:
    def __init__(self) -> None:
        self.items: dict[str, UserFeedback] = {}

    def add(self, feedback: UserFeedback) -> UserFeedback:
        existing = self.items.get(feedback.id)
        if existing is not None:
            return existing
        self.items[feedback.id] = feedback
        return feedback

    def get(self, feedback_id: str) -> UserFeedback | None:
        return self.items.get(feedback_id)

    def list_attributable(self, *args, **kwargs) -> tuple[UserFeedback, ...]:
        return tuple(self.items.values())


def client() -> TestClient:
    releases = ReleaseCatalogService(MemoryReleaseRepository())
    releases.register(
        ReleasedVersion(
            id="release-1",
            target_id="target-1",
            source_commit="a" * 40,
            source_tree="b" * 40,
            artifact_digest="sha256:" + "c" * 64,
            objective_revision_id="objective-1",
            deployment_id="deployment-1",
            promoted_at=datetime.now(UTC),
        )
    )
    releases.set_serving("target-1", "release-1", operation_id="serve-1")
    application = FastAPI()
    application.include_router(
        create_feedback_router(FeedbackService(releases, MemoryFeedbackRepository()))
    )
    return TestClient(application)


def test_feedback_api_uses_server_owned_attribution_and_provenance() -> None:
    response = client().post(
        "/v1/targets/target-1/feedback",
        json={
            "feedback_id": "feedback-1",
            "kind": "explicit",
            "category": "incorrect-result",
            "severity": 3,
            "free_text": "wrong answer",
            "reported_release_id": "release-1",
            "reported_deployment_id": "deployment-1",
        },
    )
    assert response.status_code == 201
    assert response.json()["release_id"] == "release-1"
    assert response.json()["deployment_id"] == "deployment-1"


def test_feedback_api_rejects_false_release_claim() -> None:
    response = client().post(
        "/v1/targets/target-1/feedback",
        json={
            "feedback_id": "feedback-1",
            "kind": "explicit",
            "category": "incorrect-result",
            "severity": 3,
            "reported_release_id": "fake-release",
        },
    )
    assert response.status_code == 409
