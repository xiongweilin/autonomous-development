from datetime import UTC, datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.release_finalization import ReleaseFinalizationService
from autonomous_development.domain.enums import (
    CycleState,
    DeploymentState,
    ReleaseDecisionKind,
)
from autonomous_development.domain.models import (
    BuildArtifact,
    CandidateRevision,
    Deployment,
    DevelopmentCycle,
    ReleasedVersion,
)
from autonomous_development.ports.persistence import ServingReleaseReceipt


class MemoryReleaseRepository:
    def __init__(self) -> None:
        self.releases: dict[str, ReleasedVersion] = {}
        self.serving: dict[str, str] = {}
        self.operations: dict[str, ServingReleaseReceipt] = {}

    def add(self, release: ReleasedVersion) -> ReleasedVersion:
        existing = self.releases.get(release.id)
        if existing is not None:
            if existing != release:
                raise ValueError("release conflict")
            return existing
        self.releases[release.id] = release
        return release

    def get(self, release_id: str) -> ReleasedVersion | None:
        return self.releases.get(release_id)

    def get_serving(self, target_id: str) -> ReleasedVersion | None:
        release_id = self.serving.get(target_id)
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
        previous = self.serving.get(target_id)
        receipt = ServingReleaseReceipt(
            operation_id=operation_id,
            target_id=target_id,
            release_id=release_id,
            previous_release_id=previous,
        )
        self.operations[operation_id] = receipt
        self.serving[target_id] = release_id
        return receipt


def candidate() -> CandidateRevision:
    return CandidateRevision(
        id="candidate-1",
        cycle_id="cycle-1",
        worktree_path="/tmp/worktree",
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("src/app.py",),
        codex_thread_id="thread-1",
        implementation_attempt=1,
    )


def artifact() -> BuildArtifact:
    return BuildArtifact(
        id="artifact-1",
        candidate_id="candidate-1",
        image_digest="sha256:" + "d" * 64,
        source_tree_hash="c" * 40,
        build_definition_digest="sha256:" + "e" * 64,
        dependency_lock_digest="sha256:" + "f" * 64,
        build_evidence_ref="build:1",
        sbom_digest="sha256:" + "1" * 64,
        sbom_ref="sbom:1",
        vulnerability_scan_ref="scan:1",
    )


def deployment() -> Deployment:
    now = datetime.now(UTC)
    return Deployment(
        id="deployment-1",
        target_id="target-1",
        artifact_id="artifact-1",
        environment="local-candidate",
        state=DeploymentState.SERVING,
        observed_at=now,
        observation_refs=("ready:1",),
    )


def cycle() -> DevelopmentCycle:
    return DevelopmentCycle(
        id="cycle-1",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-0",
        state=CycleState.PROMOTED,
        version=12,
        candidate_id="candidate-1",
        verification_run_id="verify-full-1",
        artifact_id="artifact-1",
        candidate_deployment_id="deployment-1",
        experiment_id="experiment-1",
        release_decision=ReleaseDecisionKind.PROMOTE,
    )


def test_promoted_candidate_becomes_server_owned_serving_release() -> None:
    repository = MemoryReleaseRepository()
    service = ReleaseFinalizationService(ReleaseCatalogService(repository))
    promoted_at = datetime.now(UTC)

    first = service.finalize(
        cycle(),
        candidate(),
        artifact(),
        deployment(),
        release_id="release-1",
        promoted_at=promoted_at,
        operation_id="finalize-1",
    )
    second = service.finalize(
        cycle(),
        candidate(),
        artifact(),
        deployment(),
        release_id="release-1",
        promoted_at=promoted_at,
        operation_id="finalize-1",
    )

    assert first == second
    assert repository.get_serving("target-1") == first
    assert first.source_commit == candidate().candidate_commit
    assert first.artifact_digest == artifact().image_digest


def test_restore_serving_reconciles_baseline_pointer() -> None:
    repository = MemoryReleaseRepository()
    catalog = ReleaseCatalogService(repository)
    service = ReleaseFinalizationService(catalog)
    baseline = ReleasedVersion(
        id="release-0",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="a" * 40,
        artifact_digest="sha256:" + "0" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-0",
        promoted_at=datetime.now(UTC),
    )
    catalog.register(baseline)
    promoted = service.finalize(
        cycle(),
        candidate(),
        artifact(),
        deployment(),
        release_id="release-1",
        promoted_at=datetime.now(UTC),
        operation_id="finalize-1",
    )
    assert catalog.serving("target-1") == promoted

    restored = service.restore_serving(
        "target-1",
        "release-0",
        operation_id="rollback-serving-1",
    )
    replay = service.restore_serving(
        "target-1",
        "release-0",
        operation_id="rollback-serving-1",
    )

    assert restored == baseline
    assert replay == baseline
    assert catalog.serving("target-1") == baseline
