from datetime import UTC, datetime, timedelta

import pytest

from autonomous_development.domain.enums import DeploymentState, FeedbackKind, VerificationStatus
from autonomous_development.domain.models import (
    CanaryStage,
    Deployment,
    EvidenceWindow,
    Experiment,
    UserFeedback,
    VerificationCheck,
    VerificationRun,
)


def test_ready_deployment_requires_independent_observation() -> None:
    with pytest.raises(ValueError, match="independent observation"):
        Deployment(
            id="d1",
            target_id="t1",
            artifact_id="a1",
            environment="candidate",
            state=DeploymentState.READY,
        )


def test_evidence_window_is_time_bounded() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="closes before"):
        EvidenceWindow(
            id="e1",
            target_id="t1",
            release_ids=("r1",),
            opened_at=now,
            closed_at=now - timedelta(seconds=1),
        )


def test_experiment_must_end_at_full_traffic() -> None:
    with pytest.raises(ValueError, match="100 percent"):
        Experiment(
            id="x1",
            target_id="t1",
            control_release_id="r1",
            candidate_deployment_id="d1",
            stages=(CanaryStage(10, 60, 10), CanaryStage(50, 60, 10)),
        )


def test_feedback_without_version_binding_is_not_attributable() -> None:
    feedback = UserFeedback(
        id="f1",
        target_id="t1",
        received_at=datetime.now(UTC),
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=3,
        provenance="api",
        free_text="bad result",
    )
    assert not feedback.attributable


def test_verification_run_only_passes_when_every_check_passes() -> None:
    now = datetime.now(UTC)
    run = VerificationRun(
        id="vr1",
        candidate_id="c1",
        checks=(
            VerificationCheck(
                id="a",
                gate="tests",
                status=VerificationStatus.PASSED,
                started_at=now,
                ended_at=now,
                evidence_refs=("log:a",),
            ),
            VerificationCheck(
                id="b",
                gate="security",
                status=VerificationStatus.FAILED,
                started_at=now,
                ended_at=now,
                evidence_refs=("log:b",),
            ),
        ),
    )
    assert not run.passed
