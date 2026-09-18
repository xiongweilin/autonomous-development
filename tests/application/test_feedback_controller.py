from datetime import UTC, datetime, timedelta
from pathlib import Path

from autonomous_development.application.feedback_controller import (
    FeedbackIterationController,
)
from autonomous_development.application.iteration import (
    DiagnosisConfidenceInsufficient,
    IterationPreparation,
)
from autonomous_development.domain.enums import CycleState, FeedbackKind
from autonomous_development.domain.models import (
    ChangeProposal,
    DevelopmentCycle,
    DevelopmentTarget,
    Diagnosis,
    EvidenceWindow,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
    UserFeedback,
)
from autonomous_development.ports.persistence import FeedbackTriggerReceipt


def policy() -> MutationPolicy:
    return MutationPolicy(allowed_paths=("src",), max_changed_files=3)


def release(now: datetime) -> ReleasedVersion:
    return ReleasedVersion(
        id="release-1",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-1",
        promoted_at=now - timedelta(hours=1),
    )


def target() -> DevelopmentTarget:
    return DevelopmentTarget(
        id="target-1",
        repository="/repo",
        default_branch="main",
        target_contract_revision="contract-1",
        active_objective_revision_id="objective-1",
        mutation_policy=policy(),
        current_release_id="release-1",
    )


def objective(now: datetime) -> ProductObjectiveRevision:
    return ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Fix severe user-visible defects.",
        acceptance_criteria=("reported defect is fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=(),
        performance_constraints=(),
        security_constraints=(),
        mutation_policy=policy(),
        created_at=now - timedelta(days=1),
    )


def feedback(now: datetime, *, severity: int = 4) -> UserFeedback:
    return UserFeedback(
        id="feedback-1",
        target_id="target-1",
        received_at=now - timedelta(minutes=5),
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=severity,
        provenance="feedback-api",
        release_id="release-1",
        deployment_id="deployment-1",
        free_text="wrong answer",
    )


class Registry:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def runtime_target(
        self,
        target_id: str,
        *,
        serving_release_id: str | None,
    ) -> DevelopmentTarget:
        assert target_id == "target-1"
        assert serving_release_id == "release-1"
        return target()

    def get_active_objective(
        self,
        registered: DevelopmentTarget,
    ) -> ProductObjectiveRevision:
        assert registered.id == "target-1"
        return objective(self.now)


class Releases:
    def __init__(self, now: datetime) -> None:
        self.value = release(now)

    def serving(self, target_id: str) -> ReleasedVersion | None:
        return self.value if target_id == "target-1" else None


class Feedback:
    def __init__(self, item: UserFeedback) -> None:
        self.item = item

    def list_attributable(self, *args, **kwargs) -> tuple[UserFeedback, ...]:
        return (self.item,)


class Windows:
    def __init__(self) -> None:
        self.calls = 0

    def close(self, **kwargs) -> EvidenceWindow:
        self.calls += 1
        return EvidenceWindow(
            id=str(kwargs["window_id"]),
            target_id=str(kwargs["target_id"]),
            release_ids=("release-1",),
            opened_at=kwargs["opened_at"],
            closed_at=kwargs["closed_at"],
            telemetry_refs=("telemetry:1",),
            feedback_refs=("feedback:feedback-1",),
        )


class Cycles:
    def __init__(self) -> None:
        self.active: DevelopmentCycle | None = None

    def active_for_target(self, target_id: str) -> DevelopmentCycle | None:
        assert target_id == "target-1"
        return self.active


class Triggers:
    def __init__(self) -> None:
        self.items: dict[str, FeedbackTriggerReceipt] = {}

    def get(self, feedback_id: str) -> FeedbackTriggerReceipt | None:
        return self.items.get(feedback_id)

    def record(self, receipt: FeedbackTriggerReceipt) -> FeedbackTriggerReceipt:
        existing = self.items.get(receipt.feedback_id)
        if existing is not None:
            assert existing == receipt
            return existing
        self.items[receipt.feedback_id] = receipt
        return receipt


class Iterations:
    def __init__(self, *, low_confidence: bool = False) -> None:
        self.low_confidence = low_confidence
        self.calls = 0

    def prepare_from_evidence(self, *args, **kwargs) -> IterationPreparation:
        self.calls += 1
        cycle_id = str(kwargs["cycle_id"])
        diagnosis_id = str(kwargs["diagnosis_id"])
        proposal_id = str(kwargs["proposal_id"])
        diagnosis = Diagnosis(
            id=diagnosis_id,
            evidence_window_id=args[3].id,
            observed_problem="wrong answer",
            affected_journey="answer",
            evidence_refs=("feedback:feedback-1",),
            confidence=0.4 if self.low_confidence else 0.95,
            competing_hypotheses=(),
            likely_root_cause="parser bug",
            proposed_change_class="bugfix",
            expected_outcome="correct answer",
            risks=(),
            requested_paths=("src/app.py",),
            required_validation=("tests",),
        )
        if self.low_confidence:
            blocked = DevelopmentCycle(
                id=cycle_id,
                target_id="target-1",
                objective_revision_id="objective-1",
                baseline_release_id="release-1",
                state=CycleState.BLOCKED,
                version=4,
                evidence_window_id=args[3].id,
                diagnosis_id=diagnosis_id,
            )
            raise DiagnosisConfidenceInsufficient(blocked, diagnosis, 0.8)

        proposal = ChangeProposal(
            id=proposal_id,
            target_id="target-1",
            baseline_release_id="release-1",
            baseline_commit="a" * 40,
            objective_revision_id="objective-1",
            diagnosis_id=diagnosis_id,
            acceptance_criteria=("reported defect is fixed",),
            allowed_paths=("src/app.py",),
            forbidden_paths=(),
            max_implementation_attempts=2,
            mandatory_gates=("tests", "performance"),
            change_intent="fix parser bug",
        )
        cycle = DevelopmentCycle(
            id=cycle_id,
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
            state=CycleState.CHANGE_PROPOSED,
            version=4,
            evidence_window_id=args[3].id,
            diagnosis_id=diagnosis_id,
            change_proposal_id=proposal_id,
        )
        return IterationPreparation(cycle=cycle, diagnosis=diagnosis, proposal=proposal)


def controller(
    now: datetime,
    *,
    severity: int = 4,
    low_confidence: bool = False,
) -> tuple[FeedbackIterationController, Triggers, Windows, Iterations, Cycles]:
    triggers = Triggers()
    windows = Windows()
    iterations = Iterations(low_confidence=low_confidence)
    cycles = Cycles()
    service = FeedbackIterationController(
        Registry(now),  # type: ignore[arg-type]
        Releases(now),  # type: ignore[arg-type]
        Feedback(feedback(now, severity=severity)),  # type: ignore[arg-type]
        windows,  # type: ignore[arg-type]
        cycles,  # type: ignore[arg-type]
        iterations,  # type: ignore[arg-type]
        triggers,
        minimum_feedback_severity=3,
        minimum_diagnosis_confidence=0.8,
        evidence_lookback_seconds=900,
        mandatory_gates=("tests", "performance"),
    )
    return service, triggers, windows, iterations, cycles


def test_severe_feedback_is_prepared_once_and_receipted() -> None:
    now = datetime.now(UTC)
    service, triggers, windows, iterations, _ = controller(now)

    first = service.prepare_next("target-1", closed_at=now)
    second = service.prepare_next("target-1", closed_at=now + timedelta(minutes=1))

    assert first is not None and first.executable
    assert first.proposal_id is not None
    assert triggers.get("feedback-1") is not None
    assert second is None
    assert windows.calls == 1
    assert iterations.calls == 1


def test_low_severity_feedback_does_not_trigger_code_change() -> None:
    now = datetime.now(UTC)
    service, triggers, windows, iterations, _ = controller(now, severity=1)

    assert service.prepare_next("target-1", closed_at=now) is None
    assert not triggers.items
    assert windows.calls == 0
    assert iterations.calls == 0


def test_low_confidence_diagnosis_is_consumed_as_blocked() -> None:
    now = datetime.now(UTC)
    service, triggers, _, iterations, _ = controller(now, low_confidence=True)

    plan = service.prepare_next("target-1", closed_at=now)

    assert plan is not None
    assert not plan.executable
    assert plan.proposal_id is None
    assert plan.cycle_id.endswith("-cycle")
    receipt = triggers.get("feedback-1")
    assert receipt is not None
    assert receipt.outcome == "blocked-low-confidence"
    assert iterations.calls == 1


def test_other_active_cycle_prevents_new_feedback_iteration() -> None:
    now = datetime.now(UTC)
    service, triggers, windows, iterations, cycles = controller(now)
    cycles.active = DevelopmentCycle(
        id="unrelated-cycle",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-1",
        state=CycleState.DEVELOPING,
        version=2,
    )

    assert service.prepare_next("target-1", closed_at=now) is None
    assert not triggers.items
    assert windows.calls == 0
    assert iterations.calls == 0
