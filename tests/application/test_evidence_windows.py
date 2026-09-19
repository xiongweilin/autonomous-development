from datetime import UTC, datetime, timedelta

from autonomous_development.application.evidence_windows import EvidenceWindowService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.enums import FeedbackKind
from autonomous_development.domain.models import EvidenceWindow, ReleasedVersion, UserFeedback
from autonomous_development.ports.persistence import ServingReleaseReceipt
from autonomous_development.ports.telemetry import TelemetryEvidence


class Releases:
    def __init__(self, release: ReleasedVersion) -> None:
        self.release = release

    def add(self, release: ReleasedVersion) -> ReleasedVersion:
        self.release = release
        return release

    def get(self, release_id: str) -> ReleasedVersion | None:
        return self.release if self.release.id == release_id else None

    def get_serving(self, target_id: str) -> ReleasedVersion | None:
        return self.release if self.release.target_id == target_id else None

    def set_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ServingReleaseReceipt:
        return ServingReleaseReceipt(operation_id, target_id, release_id, None)


class Feedback:
    def __init__(self, items: tuple[UserFeedback, ...]) -> None:
        self.items = items

    def add(self, feedback: UserFeedback) -> UserFeedback:
        raise AssertionError("not used")

    def get(self, feedback_id: str) -> UserFeedback | None:
        return next((item for item in self.items if item.id == feedback_id), None)

    def list_attributable(self, *args, **kwargs) -> tuple[UserFeedback, ...]:
        return self.items


class Telemetry:
    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        self.missing = missing

    def collect(self, **kwargs) -> TelemetryEvidence:
        return TelemetryEvidence(
            evidence_refs=("telemetry:error-rate", "telemetry:latency"),
            missing_metrics=self.missing,
        )


class Windows:
    def __init__(self) -> None:
        self.items: dict[str, EvidenceWindow] = {}

    def add(self, window: EvidenceWindow) -> EvidenceWindow:
        existing = self.items.get(window.id)
        if existing is not None and existing != window:
            raise ValueError("window conflict")
        self.items[window.id] = window
        return window

    def get(self, window_id: str) -> EvidenceWindow | None:
        return self.items.get(window_id)


def release(promoted_at: datetime) -> ReleasedVersion:
    return ReleasedVersion(
        id="release-1",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-1",
        promoted_at=promoted_at,
    )


def feedback(received_at: datetime) -> UserFeedback:
    return UserFeedback(
        id="feedback-1",
        target_id="target-1",
        received_at=received_at,
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=4,
        provenance="feedback-api",
        release_id="release-1",
        deployment_id="deployment-1",
        free_text="wrong answer",
    )


def test_evidence_window_freezes_feedback_and_telemetry_refs() -> None:
    now = datetime.now(UTC)
    releases = ReleaseCatalogService(Releases(release(now - timedelta(hours=2))))
    service = EvidenceWindowService(
        releases,
        Feedback((feedback(now - timedelta(minutes=10)),)),
        Telemetry(),
        Windows(),
    )
    window = service.close(
        window_id="window-1",
        target_id="target-1",
        opened_at=now - timedelta(hours=1),
        closed_at=now,
    )
    assert window.release_ids == ("release-1",)
    assert window.feedback_refs == ("feedback:feedback-1",)
    assert window.telemetry_refs == ("telemetry:error-rate", "telemetry:latency")
    assert not window.missing_evidence


def test_missing_metric_is_explicit_not_success() -> None:
    now = datetime.now(UTC)
    releases = ReleaseCatalogService(Releases(release(now - timedelta(hours=2))))
    service = EvidenceWindowService(
        releases,
        Feedback(()),
        Telemetry(("latency",)),
        Windows(),
    )
    window = service.close(
        window_id="window-1",
        target_id="target-1",
        opened_at=now - timedelta(hours=1),
        closed_at=now,
    )
    assert window.missing_evidence == ("telemetry:latency",)


def test_pre_promotion_candidate_feedback_window_is_allowed_without_future_telemetry() -> None:
    now = datetime.now(UTC)
    promoted_at = now - timedelta(minutes=20)
    candidate_feedback = UserFeedback(
        id="feedback-candidate",
        target_id="target-1",
        received_at=now - timedelta(minutes=30),
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=4,
        provenance="feedback-api",
        release_id=None,
        deployment_id="deployment-1",
        experiment_id="experiment-1",
    )

    class UnexpectedTelemetry(Telemetry):
        def collect(self, **kwargs) -> TelemetryEvidence:
            raise AssertionError(f"future serving telemetry must not be queried: {kwargs}")

    releases = ReleaseCatalogService(Releases(release(promoted_at)))
    service = EvidenceWindowService(
        releases,
        Feedback((candidate_feedback,)),
        UnexpectedTelemetry(),
        Windows(),
    )
    window = service.close(
        window_id="window-candidate",
        target_id="target-1",
        opened_at=now - timedelta(minutes=40),
        closed_at=now - timedelta(minutes=25),
        feedback_ids=("feedback-candidate",),
    )

    assert window.feedback_refs == ("feedback:feedback-candidate",)
    assert window.missing_evidence == (
        "telemetry:serving-release-not-yet-promoted",
    )



def test_explicit_feedback_window_replays_without_recollecting_telemetry() -> None:
    now = datetime.now(UTC)
    releases = ReleaseCatalogService(Releases(release(now - timedelta(hours=2))))
    feedback_item = feedback(now - timedelta(minutes=10))

    class CountingTelemetry(Telemetry):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def collect(self, **kwargs) -> TelemetryEvidence:
            self.calls += 1
            return super().collect(**kwargs)

    telemetry = CountingTelemetry()
    windows = Windows()
    service = EvidenceWindowService(
        releases,
        Feedback((feedback_item,)),
        telemetry,
        windows,
    )
    first = service.close(
        window_id="window-explicit",
        target_id="target-1",
        opened_at=now - timedelta(minutes=30),
        closed_at=now,
        feedback_ids=("feedback-1",),
    )
    second = service.close(
        window_id="window-explicit",
        target_id="target-1",
        opened_at=now - timedelta(minutes=30),
        closed_at=now,
        feedback_ids=("feedback-1",),
    )

    assert second == first
    assert first.feedback_refs == ("feedback:feedback-1",)
    assert telemetry.calls == 1
