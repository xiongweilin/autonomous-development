from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.diagnoses import SqlDiagnosisRepository
from autonomous_development.adapters.postgres.evidence_windows import SqlEvidenceWindowRepository
from autonomous_development.adapters.postgres.feedback import SqlFeedbackRepository
from autonomous_development.adapters.postgres.feedback_triggers import (
    SqlFeedbackTriggerRepository,
)
from autonomous_development.adapters.postgres.proposals import SqlChangeProposalRepository
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.diagnosis import DiagnosisService
from autonomous_development.application.evidence_windows import EvidenceWindowService
from autonomous_development.application.iteration import IterationService
from autonomous_development.application.iteration_scheduler import (
    FeedbackIterationPolicy,
    FeedbackIterationSchedulerService,
)
from autonomous_development.application.proposals import ProposalService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.enums import CycleState, FeedbackKind
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
    UserFeedback,
)
from autonomous_development.ports.codex import CodexTurnRequest, CodexTurnResult
from autonomous_development.ports.persistence import (
    FeedbackTriggerReceipt,
    FeedbackTriggerRepository,
)
from autonomous_development.ports.repository import RepositoryBaseline
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetDeploymentContract,
    TargetPerformanceContract,
    TargetVerificationContract,
    TargetVerificationGateContract,
)
from autonomous_development.ports.telemetry import TelemetryEvidence


class FakeCodex:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence
        self.calls = 0

    def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult:
        self.calls += 1
        payload = {
            "observed_problem": "known request returns the wrong answer",
            "affected_journey": "answer generation",
            "confidence": self.confidence,
            "competing_hypotheses": ["parser boundary"],
            "likely_root_cause": "boundary handling in src/app.py",
            "proposed_change_class": "bugfix",
            "expected_outcome": "known request returns the correct answer",
            "risks": ["parser regression"],
            "requested_paths": ["src/app.py"],
            "required_validation": ["regression test"],
        }
        return CodexTurnResult(
            thread_id="diagnosis-thread",
            turn_id=f"diagnosis-turn-{self.calls}",
            status="completed",
            events=(),
            agent_messages=(json.dumps(payload),),
        )


class FakeRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def verify_baseline(self, repository_root: Path, default_branch: str) -> RepositoryBaseline:
        assert repository_root == self.root
        assert default_branch == "main"
        return RepositoryBaseline(
            repository_root=repository_root,
            commit="a" * 40,
            tree="b" * 40,
            branch=default_branch,
        )


class FakeTelemetry:
    def collect(self, **kwargs: object) -> TelemetryEvidence:
        del kwargs
        return TelemetryEvidence(
            evidence_refs=("telemetry:correctness",),
            missing_metrics=(),
        )


class FakeContractLoader:
    def load(self, repository_root: str) -> TargetContract:
        assert Path(repository_root).is_absolute()
        return TargetContract(
            schema_version=1,
            target_id="target-1",
            build=TargetBuildContract("Dockerfile", ("uv.lock",)),
            verification=TargetVerificationContract(
                gates=(
                    TargetVerificationGateContract(
                        id="tests",
                        command=("uv", "run", "pytest"),
                    ),
                )
            ),
            deployment=TargetDeploymentContract(8000, "/health", "/ready", 30),
            performance=TargetPerformanceContract(
                "tests/performance/smoke.js",
                ("http_req_failed",),
                60,
            ),
            canary=TargetCanaryContract(
                stages=(CanaryStage(100, 1, 1),),
                max_candidate_error_rate=0.02,
                max_error_rate_delta=0.01,
                max_candidate_p95_latency_ms=250.0,
                max_p95_latency_ratio=1.25,
            ),
        )


class FailOnceTriggers(FeedbackTriggerRepository):
    def __init__(self, inner: FeedbackTriggerRepository) -> None:
        self.inner = inner
        self.failed = False

    def get(self, feedback_id: str) -> FeedbackTriggerReceipt | None:
        return self.inner.get(feedback_id)

    def record(self, receipt: FeedbackTriggerReceipt) -> FeedbackTriggerReceipt:
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated crash before trigger receipt commit")
        return self.inner.record(receipt)


def policy() -> MutationPolicy:
    return MutationPolicy(
        allowed_paths=("src", "tests"),
        forbidden_paths=("deploy",),
        max_changed_files=5,
        max_implementation_attempts=2,
    )


def objective(now: datetime) -> ProductObjectiveRevision:
    return ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Fix user-visible correctness defects without regressions.",
        acceptance_criteria=("known defect is fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=("error rate must not regress",),
        performance_constraints=("p95 must remain within target",),
        security_constraints=("no secret exposure",),
        mutation_policy=policy(),
        created_at=now - timedelta(days=1),
    )


def target(root: Path) -> DevelopmentTarget:
    return DevelopmentTarget(
        id="target-1",
        repository=str(root),
        default_branch="main",
        target_contract_revision="contract-1",
        active_objective_revision_id="objective-1",
        mutation_policy=policy(),
    )


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


def feedback(now: datetime, *, severity: int = 4) -> UserFeedback:
    return UserFeedback(
        id="feedback-1",
        target_id="target-1",
        received_at=now - timedelta(minutes=10),
        kind=FeedbackKind.EXPLICIT,
        category="incorrect-result",
        severity=severity,
        provenance="feedback-api",
        release_id="release-1",
        deployment_id="deployment-1",
        request_ref="request-1",
        free_text="The answer is wrong on a known request.",
    )


def service(
    tmp_path: Path,
    *,
    confidence: float,
    triggers: FeedbackTriggerRepository | None = None,
) -> tuple[
    FeedbackIterationSchedulerService,
    FakeCodex,
    CycleService,
    SqlFeedbackRepository,
    SqlFeedbackTriggerRepository,
]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)

    release_repository = SqlReleasedVersionRepository(engine)
    releases = ReleaseCatalogService(release_repository)
    now = datetime.now(UTC)
    releases.register(release(now))
    releases.set_serving("target-1", "release-1", operation_id="serve-release-1")

    target_repository = SqlTargetRepository(engine)
    objective_repository = SqlObjectiveRepository(engine)
    registry = TargetRegistryService(target_repository, objective_repository)
    registry.register(target(tmp_path.resolve()), objective(now))

    feedback_repository = SqlFeedbackRepository(engine)
    trigger_repository = SqlFeedbackTriggerRepository(engine)
    codex = FakeCodex(confidence)
    cycles = CycleService(SqlCycleRepository(engine))
    diagnosis = DiagnosisService(
        codex,
        feedback_repository,
        SqlDiagnosisRepository(engine),
    )
    proposals = ProposalService(SqlChangeProposalRepository(engine))
    evidence = EvidenceWindowService(
        releases,
        feedback_repository,
        FakeTelemetry(),
        SqlEvidenceWindowRepository(engine),
    )
    scheduler = FeedbackIterationSchedulerService(
        cycles=cycles,
        releases=releases,
        targets=registry,
        feedback=feedback_repository,
        triggers=triggers or trigger_repository,
        evidence=evidence,
        iterations=IterationService(
            cycles,
            FakeRepository(tmp_path.resolve()),
            diagnosis,
            proposals,
        ),
        contracts=FakeContractLoader(),
        policy=FeedbackIterationPolicy(
            minimum_severity=3,
            diagnosis_delay_seconds=60,
            evidence_lookback_seconds=300,
            minimum_diagnosis_confidence=0.65,
        ),
    )
    return scheduler, codex, cycles, feedback_repository, trigger_repository


def test_high_severity_feedback_prepares_exactly_one_change_proposal(tmp_path: Path) -> None:
    scheduler, codex, cycles, feedback_repository, triggers = service(
        tmp_path,
        confidence=0.9,
    )
    now = datetime.now(UTC)
    feedback_repository.add(feedback(now))

    result = scheduler.prepare_next("target-1", scheduled_time=now)

    assert result.status == "prepared"
    assert result.feedback_id == "feedback-1"
    assert result.proposal_id is not None
    assert result.cycle_id is not None
    assert cycles.get(result.cycle_id).state is CycleState.CHANGE_PROPOSED
    assert triggers.get("feedback-1") is not None
    assert codex.calls == 1


def test_low_confidence_diagnosis_is_consumed_and_blocked(tmp_path: Path) -> None:
    scheduler, codex, cycles, feedback_repository, triggers = service(
        tmp_path,
        confidence=0.4,
    )
    now = datetime.now(UTC)
    feedback_repository.add(feedback(now))

    first = scheduler.prepare_next("target-1", scheduled_time=now)
    second = scheduler.prepare_next("target-1", scheduled_time=now + timedelta(minutes=1))

    assert first.status == "blocked-low-confidence"
    assert first.cycle_id is not None
    assert cycles.get(first.cycle_id).state is CycleState.BLOCKED
    assert triggers.get("feedback-1") is not None
    assert second.status == "idle"
    assert codex.calls == 1


def test_trigger_receipt_crash_replays_same_prepared_iteration(tmp_path: Path) -> None:
    inner_engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(inner_engine)
    inner = SqlFeedbackTriggerRepository(inner_engine)
    failing = FailOnceTriggers(inner)

    scheduler, codex, cycles, feedback_repository, _ = service(
        tmp_path,
        confidence=0.9,
        triggers=failing,
    )
    now = datetime.now(UTC)
    feedback_repository.add(feedback(now))

    with pytest.raises(RuntimeError, match="simulated crash"):
        scheduler.prepare_next("target-1", scheduled_time=now)

    active = cycles.active_for_target("target-1")
    assert active is not None
    assert active.state is CycleState.CHANGE_PROPOSED
    first_cycle_id = active.id

    replay = scheduler.prepare_next(
        "target-1",
        scheduled_time=now + timedelta(minutes=1),
    )

    assert replay.status == "prepared"
    assert replay.cycle_id == first_cycle_id
    assert inner.get("feedback-1") is not None
    assert codex.calls == 1


def test_feedback_below_trigger_severity_is_not_consumed(tmp_path: Path) -> None:
    scheduler, codex, _, feedback_repository, triggers = service(
        tmp_path,
        confidence=0.9,
    )
    now = datetime.now(UTC)
    feedback_repository.add(feedback(now, severity=2))

    result = scheduler.prepare_next("target-1", scheduled_time=now)

    assert result.status == "idle"
    assert triggers.get("feedback-1") is None
    assert codex.calls == 0
