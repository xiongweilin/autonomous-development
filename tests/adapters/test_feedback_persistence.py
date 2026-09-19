from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.feedback import SqlFeedbackRepository
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import ReleasedVersion, UserFeedback
from autonomous_development.ports.persistence import OperationConflictError


def release(release_id: str, deployment_id: str = "deployment-1") -> ReleasedVersion:
    return ReleasedVersion(
        id=release_id,
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id=deployment_id,
        promoted_at=datetime.now(UTC),
    )


def repositories() -> tuple[SqlReleasedVersionRepository, SqlFeedbackRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return SqlReleasedVersionRepository(engine), SqlFeedbackRepository(engine)


def test_serving_pointer_is_idempotent_and_tracks_previous_release() -> None:
    releases, _ = repositories()
    releases.add(release("release-1"))
    releases.add(release("release-2", "deployment-2"))

    first = releases.set_serving("target-1", "release-1", operation_id="serve-1")
    replay = releases.set_serving("target-1", "release-1", operation_id="serve-1")
    second = releases.set_serving("target-1", "release-2", operation_id="serve-2")

    assert replay == first
    assert second.previous_release_id == "release-1"
    serving = releases.get_serving("target-1")
    assert serving is not None
    assert serving.id == "release-2"
    assert serving.deployment_id == "deployment-2"


def test_serving_operation_id_cannot_be_rebound() -> None:
    releases, _ = repositories()
    releases.add(release("release-1"))
    releases.add(release("release-2", "deployment-2"))
    releases.set_serving("target-1", "release-1", operation_id="serve-1")

    with pytest.raises(OperationConflictError):
        releases.set_serving("target-1", "release-2", operation_id="serve-1")


def test_feedback_query_is_release_and_time_bounded() -> None:
    releases, feedback = repositories()
    releases.add(release("release-1"))
    now = datetime.now(UTC)
    in_window = UserFeedback(
        id="feedback-in",
        target_id="target-1",
        received_at=now,
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=3,
        provenance="feedback-api",
        release_id="release-1",
        deployment_id="deployment-1",
    )
    outside = UserFeedback(
        id="feedback-out",
        target_id="target-1",
        received_at=now - timedelta(hours=2),
        kind=FeedbackKind.EXPLICIT,
        category="slow",
        severity=2,
        provenance="feedback-api",
        release_id="release-1",
        deployment_id="deployment-1",
    )
    feedback.add(in_window)
    feedback.add(outside)

    result = feedback.list_attributable(
        "target-1",
        "release-1",
        deployment_id="deployment-1",
        opened_at=now - timedelta(minutes=30),
        closed_at=now + timedelta(minutes=1),
    )
    assert result == (in_window,)


def test_feedback_query_includes_candidate_deployment_after_promotion() -> None:
    _, feedback = repositories()
    now = datetime.now(UTC)
    candidate = UserFeedback(
        id="feedback-candidate",
        target_id="target-1",
        received_at=now,
        kind=FeedbackKind.EXPLICIT,
        category="candidate-defect",
        severity=4,
        provenance="feedback-api",
        release_id=None,
        deployment_id="deployment-2",
        experiment_id="experiment-1",
        request_ref="request-candidate",
    )
    feedback.add(candidate)

    result = feedback.list_attributable(
        "target-1",
        "release-2",
        deployment_id="deployment-2",
        opened_at=now - timedelta(minutes=1),
        closed_at=now + timedelta(minutes=1),
    )
    assert result == (candidate,)
