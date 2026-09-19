from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.operator import SqlOperatorRepository
from autonomous_development.adapters.postgres.proposals import SqlChangeProposalRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.operator import OperatorService
from autonomous_development.application.proposals import ProposalService
from autonomous_development.domain.enums import DevelopmentRequestStatus, HumanInterventionStatus
from autonomous_development.domain.models import (
    DevelopmentRequest,
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
    RequirementAnalysis,
)
from autonomous_development.ports.repository import RepositoryBaseline

NOW = datetime(2026, 9, 19, tzinfo=UTC)


def policy() -> MutationPolicy:
    return MutationPolicy(
        allowed_paths=("src", "tests"),
        forbidden_paths=("deploy", ".github"),
        max_changed_files=5,
        max_implementation_attempts=2,
    )


def target(root: Path) -> DevelopmentTarget:
    return DevelopmentTarget(
        id="target-1",
        repository=str(root),
        default_branch="main",
        target_contract_revision="contract-1",
        active_objective_revision_id="objective-1",
        mutation_policy=policy(),
        current_release_id="release-1",
    )


def objective() -> ProductObjectiveRevision:
    return ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Keep responses deterministic.",
        acceptance_criteria=("existing behavior remains compatible",),
        primary_metrics=("correctness",),
        reliability_constraints=(),
        performance_constraints=(),
        security_constraints=(),
        mutation_policy=policy(),
        created_at=NOW,
    )


def baseline() -> ReleasedVersion:
    return ReleasedVersion(
        id="release-1",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-1",
        promoted_at=NOW,
    )


def request() -> DevelopmentRequest:
    return DevelopmentRequest(
        id="request-1",
        target_id="target-1",
        source="test",
        external_reference_digest="external-1",
        title="Deterministic response",
        normalized_requirement_text="Make the response deterministic.",
        content_sha256="d" * 64,
        created_at=NOW,
    )


class Targets:
    def __init__(self, root: Path) -> None:
        self.registered = target(root)

    def get_target(self, target_id: str) -> DevelopmentTarget:
        assert target_id == self.registered.id
        return self.registered

    def get_active_objective(self, registered: DevelopmentTarget) -> ProductObjectiveRevision:
        assert registered.id == self.registered.id
        return objective()


class Releases:
    def serving(self, target_id: str) -> ReleasedVersion | None:
        return baseline() if target_id == "target-1" else None


class ContractLoader:
    def load(self, repository_root: str) -> object:
        return SimpleNamespace(target_id="target-1")


class SourceRepository:
    def verify_baseline(self, root: Path, branch: str) -> RepositoryBaseline:
        return RepositoryBaseline(root, "a" * 40, "b" * 40, branch)


class Requirements:
    def __init__(self, analysis: RequirementAnalysis) -> None:
        self.analysis = analysis

    def analyze(self, *args: object, **kwargs: object) -> RequirementAnalysis:
        return self.analysis


class FailingRequirements:
    def analyze(self, *args: object, **kwargs: object) -> RequirementAnalysis:
        raise RuntimeError("Codex output was not usable")


def analysis(*, actionable: bool = True) -> RequirementAnalysis:
    return RequirementAnalysis(
        id="analysis:request-1",
        request_id="request-1",
        summary="Make the response deterministic.",
        acceptance_criteria=("the response is stable",),
        requested_paths=("src/app.py",) if actionable else (),
        expected_behavior=("same input returns the same output",),
        risks=(),
        missing_information=() if actionable else ("which response format",),
        ambiguity=(),
        validation_expectations=("run tests",),
        created_at=NOW,
    )


def service(
    tmp_path: Path,
    requirement_analysis: RequirementAnalysis,
    source_repository: object | None = None,
) -> tuple[OperatorService, SqlOperatorRepository, CycleService]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    operator_repository = SqlOperatorRepository(engine)
    operator_repository.add_request(request())
    cycles = CycleService(SqlCycleRepository(engine))
    service = OperatorService(
        operator_repository,
        cycles=cycles,
        proposals=ProposalService(SqlChangeProposalRepository(engine)),
        targets=Targets(tmp_path),  # type: ignore[arg-type]
        releases=Releases(),  # type: ignore[arg-type]
        requirements=Requirements(requirement_analysis),  # type: ignore[arg-type]
        target_contracts=ContractLoader(),  # type: ignore[arg-type]
        source_repository=(source_repository or SourceRepository()),  # type: ignore[arg-type]
        repository_root=tmp_path,
        mandatory_gate_ids=("tests",),
    )
    return service, operator_repository, cycles


def test_requirement_enters_existing_cycle_path_and_start_is_idempotent(tmp_path: Path) -> None:
    operator, repository, _ = service(tmp_path, analysis())
    prepared = operator.prepare("request-1", operation_id="prepare-1")
    assert prepared.status == "ready"
    assert prepared.proposal_id == "proposal:request:request-1"
    assert prepared.cycle_id is not None
    assert prepared.cycle_id.startswith("cycle-request-")
    stored = operator.get_request("request-1")
    assert stored.status is DevelopmentRequestStatus.RUNNING
    assert repository.pending_event_count() == 1

    first, should_start = operator.start_record("request-1")
    second, should_start_again = operator.start_record("request-1")
    assert should_start
    assert not should_start_again
    assert first == second
    assert first.workflow_attempt == 1
    assert first.active_workflow_id == "requirement:request-1:1"


def test_ambiguous_requirement_creates_intervention_and_response_is_replay_safe(
    tmp_path: Path,
) -> None:
    operator, repository, _ = service(tmp_path, analysis(actionable=False))
    prepared = operator.prepare("request-1", operation_id="prepare-1")
    assert prepared.status == "needs-human"
    assert prepared.intervention_id == "intervention:request-1"
    intervention = repository.get_intervention("intervention:request-1")
    assert intervention is not None
    assert intervention.status is HumanInterventionStatus.OPEN

    updated = operator.respond_intervention(intervention.id, "Use JSON")
    assert updated.status is DevelopmentRequestStatus.READY
    assert operator.respond_intervention(intervention.id, "Use JSON") == updated
    assert operator.get_request("request-1").pending_intervention_id is None


def test_request_id_content_conflict_is_rejected(tmp_path: Path) -> None:
    operator, _, _ = service(tmp_path, analysis())
    with pytest.raises(ValueError, match="immutable content"):
        operator.submit_request(
            request_id="request-1",
            target_id="target-1",
            source="test",
            external_reference_digest="external-1",
            title="Different",
            normalized_requirement_text="Different text",
            content_sha256="e" * 64,
        )


def test_submit_finish_cancel_status_and_terminal_start_are_replay_safe(tmp_path: Path) -> None:
    operator, repository, _ = service(tmp_path, analysis())
    stored = operator.submit_request(
        request_id="request-2",
        target_id="target-1",
        source="test",
        external_reference_digest="external-2",
        title="Second requirement",
        normalized_requirement_text="Do another bounded thing.",
        content_sha256="f" * 64,
    )
    assert operator.submit_request(
        request_id="request-2",
        target_id="target-1",
        source="test",
        external_reference_digest="external-2",
        title="Second requirement",
        normalized_requirement_text="Do another bounded thing.",
        content_sha256="f" * 64,
    ) == stored
    status = operator.status("request-2")
    assert status.cycle_state is None
    assert status.serving_release_id == "release-1"

    completed = operator.finish(
        "request-2",
        {
            "status": "promoted",
            "reason": "validated",
            "execution": {
                "status": "promoted",
                "release": {"id": "release-2", "source_commit": "b" * 40},
                "candidate": {"candidate_commit": "c" * 40, "changed_paths": ["src/app.py"]},
            },
            "soak": {"status": "completed"},
        },
    )
    assert completed["status"] == "completed"
    assert operator.start_record("request-2")[1] is False
    assert repository.pending_event_count() >= 2

    operator, repository, _ = service(tmp_path, analysis())
    operator.finish("request-1", {"status": "rolled-back", "reason": "canary regression"})
    assert operator.cancel_request("request-1").status is DevelopmentRequestStatus.ROLLED_BACK
    assert operator.cancel_request("request-1").status is DevelopmentRequestStatus.ROLLED_BACK
    assert repository.latest_event_sequence() == 1


def test_running_request_is_not_safely_cancellable(tmp_path: Path) -> None:
    operator, _, _ = service(tmp_path, analysis())
    operator.prepare("request-1", operation_id="prepare-1")
    with pytest.raises(ValueError, match="not safely cancellable"):
        operator.cancel_request("request-1")


def test_safe_cancel_and_operator_cursors_cover_non_running_paths(tmp_path: Path) -> None:
    operator, _repository, _ = service(tmp_path, analysis())
    cancelled = operator.cancel_request("request-1")
    assert cancelled.status is DevelopmentRequestStatus.CANCELLED
    assert operator.cancel_request("request-1") == cancelled
    assert operator.start_record("request-1")[1] is False
    events = operator.list_events(after=0, limit=10)
    assert events
    acknowledged = operator.acknowledge_event(events[-1].id)
    assert acknowledged.acknowledged_at is not None
    assert operator.pending_event_count() == 0
    assert operator.pending_intervention_count() == 0
    assert operator.latest_event_sequence() == events[-1].sequence


def test_human_waiting_request_cannot_start_until_response(tmp_path: Path) -> None:
    operator, _, _ = service(tmp_path, analysis(actionable=False))
    operator.prepare("request-1", operation_id="prepare-1")
    with pytest.raises(ValueError, match="waiting for human"):
        operator.start_record("request-1")


class DirtySourceRepository(SourceRepository):
    def verify_baseline(self, root: Path, branch: str) -> RepositoryBaseline:
        raise RuntimeError("repository is dirty")


def test_dirty_baseline_becomes_human_intervention(tmp_path: Path) -> None:
    operator, repository, _ = service(
        tmp_path,
        analysis(),
        source_repository=DirtySourceRepository(),
    )
    prepared = operator.prepare("request-1", operation_id="prepare-dirty")
    assert prepared.status == "needs-human"
    intervention = repository.get_intervention("intervention:request-1")
    assert intervention is not None
    assert intervention.kind == "baseline-recovery"


def test_failed_requirement_analysis_becomes_human_intervention(tmp_path: Path) -> None:
    operator, repository, _ = service(tmp_path, analysis())
    operator._requirements = FailingRequirements()  # type: ignore[assignment]

    prepared = operator.prepare("request-1", operation_id="prepare-analysis-failure")

    assert prepared.status == "needs-human"
    intervention = repository.get_intervention("intervention:request-1")
    assert intervention is not None
    assert intervention.kind == "requirement-analysis-failed"


def test_failed_and_cancelled_terminal_events_are_classified(tmp_path: Path) -> None:
    operator, _, _ = service(tmp_path, analysis())
    assert operator.finish("request-1", {"status": "failed"})["status"] == "failed"
    operator, _, _ = service(tmp_path, analysis())
    assert operator.finish("request-1", {"status": "cancelled"})["status"] == "cancelled"
