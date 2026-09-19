from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.experiments import SqlExperimentRepository
from autonomous_development.adapters.postgres.proposals import SqlChangeProposalRepository
from autonomous_development.adapters.postgres.release_decisions import (
    SqlReleaseDecisionRepository,
)
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.canary import CanaryService
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.engineering import EngineeringAttempt
from autonomous_development.application.experiments import ExperimentService
from autonomous_development.application.proposals import ProposalService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.release_finalization import ReleaseFinalizationService
from autonomous_development.application.releases import ReleaseService
from autonomous_development.domain.canary import CanaryStageEvidence
from autonomous_development.domain.enums import (
    CycleState,
    DeploymentState,
    VerificationStatus,
)
from autonomous_development.domain.models import (
    BuildArtifact,
    CanaryStage,
    CandidateRevision,
    ChangeProposal,
    Deployment,
    DevelopmentCycle,
    ReleasedVersion,
    VerificationCheck,
    VerificationRun,
)
from autonomous_development.ports.deployment import DeploymentRuntime
from autonomous_development.ports.repository import Worktree
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetDeploymentContract,
    TargetPerformanceContract,
    TargetVerificationContract,
    TargetVerificationGateContract,
)
from autonomous_development.ports.traffic import (
    TrafficRouteSnapshot,
    TrafficRouteState,
    TrafficSplit,
)
from autonomous_development.workflows.autonomous_iteration import (
    AutonomousIterationWorkflow,
)


class FakeEngineering:
    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.calls = 0

    def implement(self, proposal: ChangeProposal, **kwargs: object) -> EngineeringAttempt:
        self.calls += 1
        return EngineeringAttempt(
            candidate=CandidateRevision(
                id="cycle-1-candidate-1",
                cycle_id="cycle-1",
                worktree_path=str(self.worktree),
                branch_name="autodev/cycle-1",
                base_commit=proposal.baseline_commit,
                candidate_commit="b" * 40,
                tree_hash="c" * 40,
                changed_paths=("src/app.py",),
                codex_thread_id="thread-1",
                implementation_attempt=1,
            ),
            worktree=Worktree(
                path=self.worktree,
                branch="autodev/cycle-1",
                base_commit=proposal.baseline_commit,
            ),
            codex_status="completed",
        )


class FakeVerification:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        candidate: CandidateRevision,
        *,
        run_id: str,
        required_gates: tuple[str, ...],
    ) -> VerificationRun:
        self.calls += 1
        now = datetime.now(UTC)
        return VerificationRun(
            id=run_id,
            candidate_id=candidate.id,
            checks=tuple(
                VerificationCheck(
                    id=f"{candidate.id}:{gate}",
                    gate=gate,
                    status=VerificationStatus.PASSED,
                    started_at=now,
                    ended_at=now,
                    evidence_refs=(f"evidence:{gate}",),
                )
                for gate in required_gates
            ),
        )


class FakeBuild:
    def __init__(self) -> None:
        self.calls = 0

    def build(
        self,
        candidate: CandidateRevision,
        *,
        artifact_id: str,
        dockerfile: Path,
        dependency_locks: tuple[Path, ...],
    ) -> BuildArtifact:
        del dockerfile, dependency_locks
        self.calls += 1
        return BuildArtifact(
            id=artifact_id,
            candidate_id=candidate.id,
            image_digest="sha256:" + "d" * 64,
            source_tree_hash=candidate.tree_hash,
            build_definition_digest="sha256:" + "e" * 64,
            dependency_lock_digest="sha256:" + "f" * 64,
            build_evidence_ref="build:1",
            sbom_digest="sha256:" + "1" * 64,
            sbom_ref="sbom:1",
            vulnerability_scan_ref="scan:1",
        )


class FakeReleaseRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, release_id: str) -> DeploymentRuntime:
        self.calls.append(release_id)
        return DeploymentRuntime(
            deployment_id="deployment-0",
            container_id="control-container",
            base_url="http://127.0.0.1:4100",
            evidence_ref="release-runtime:control",
        )


class FakeDeployment:
    def __init__(self) -> None:
        self.calls = 0
        self.stopped: list[str] = []

    def deploy_candidate(
        self,
        artifact: BuildArtifact,
        contract: TargetContract,
        *,
        deployment_id: str,
    ) -> tuple[DeploymentRuntime, Deployment]:
        self.calls += 1
        now = datetime.now(UTC)
        return (
            DeploymentRuntime(
                deployment_id=deployment_id,
                container_id="container-1",
                base_url="http://127.0.0.1:49155",
                evidence_ref="deployment-effect:1",
            ),
            Deployment(
                id=deployment_id,
                target_id=contract.target_id,
                artifact_id=artifact.id,
                environment="local-candidate",
                state=DeploymentState.READY,
                observed_at=now,
                observation_refs=("ready:1",),
            ),
        )

    def stop(self, deployment_id: str) -> None:
        self.stopped.append(deployment_id)


class PassingPerformanceGate:
    gate_id = "performance"

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck:
        self.calls += 1
        now = datetime.now(UTC)
        return VerificationCheck(
            id=f"{candidate.id}:performance",
            gate=self.gate_id,
            status=VerificationStatus.PASSED,
            started_at=now,
            ended_at=now,
            evidence_refs=("performance:1",),
        )


class FakePerformanceFactory:
    def __init__(self) -> None:
        self.gate = PassingPerformanceGate()
        self.calls = 0

    def create(self, **kwargs: object) -> PassingPerformanceGate:
        self.calls += 1
        assert kwargs["base_url"] == "http://127.0.0.1:49155"
        return self.gate


class FakeTraffic:
    def __init__(self) -> None:
        self.applied = 0
        self.current: TrafficRouteSnapshot | None = None

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        self.applied += 1
        state = TrafficRouteState(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            candidate_weight_percent=split.candidate_weight_percent,
            generation=self.applied,
            evidence_ref=f"traffic:{self.applied}",
        )
        self.current = TrafficRouteSnapshot(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            control_base_url=split.control_base_url,
            candidate_base_url=split.candidate_base_url,
            candidate_weight_percent=split.candidate_weight_percent,
            operation_id=split.operation_id,
            generation=state.generation,
            evidence_ref=state.evidence_ref,
            target_id=split.target_id,
            control_release_id=split.control_release_id,
            candidate_deployment_id=split.candidate_deployment_id,
        )
        return state

    def read_current(self) -> TrafficRouteSnapshot | None:
        return self.current

    def restore_control(self, **kwargs: object) -> TrafficRouteState:
        state = TrafficRouteState(
            experiment_id=str(kwargs["experiment_id"]),
            stage_index=int(kwargs["stage_index"]),
            candidate_weight_percent=0,
            generation=self.applied + 1,
            evidence_ref="traffic:restore",
        )
        assert self.current is not None
        self.current = TrafficRouteSnapshot(
            experiment_id=str(kwargs["experiment_id"]),
            stage_index=int(kwargs["stage_index"]),
            control_base_url=str(kwargs["control_base_url"]),
            candidate_base_url=str(kwargs["candidate_base_url"]),
            candidate_weight_percent=0,
            operation_id=str(kwargs["operation_id"]),
            generation=state.generation,
            evidence_ref=state.evidence_ref,
            target_id=self.current.target_id,
            control_release_id=self.current.control_release_id,
            candidate_deployment_id=self.current.candidate_deployment_id,
        )
        return state


class PassingCanaryObserver:
    def __init__(self) -> None:
        self.calls = 0

    def observe(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        stage: CanaryStage,
        route_state: TrafficRouteState,
    ) -> CanaryStageEvidence:
        del route_state
        self.calls += 1
        return CanaryStageEvidence(
            experiment_id=experiment_id,
            stage_index=stage_index,
            weight_percent=stage.weight_percent,
            observed_duration_seconds=stage.min_duration_seconds,
            total_requests=stage.min_requests,
            candidate_requests=stage.min_requests,
            control_requests=0,
            candidate_error_rate=0.0,
            control_error_rate=None,
            candidate_p95_latency_ms=50.0,
            control_p95_latency_ms=None,
            evidence_refs=(f"canary:{stage_index}",),
        )


class FakeSourcePromotion:
    def __init__(self, *, fail_promote: bool = False) -> None:
        self.calls = 0
        self.fail_promote = fail_promote
        self.restored: list[str] = []
        self.cleaned: list[str] = []

    def promote(
        self,
        candidate: CandidateRevision,
        *,
        repository_root: Path,
        default_branch: str,
    ) -> CandidateRevision:
        self.calls += 1
        assert repository_root.is_absolute()
        assert default_branch == "main"
        if self.fail_promote:
            raise RuntimeError("simulated source promotion failure")
        return candidate

    def restore_baseline(self, **kwargs: object) -> None:
        self.restored.append(str(kwargs["baseline_commit"]))

    def cleanup_cycle(self, **kwargs: object) -> None:
        self.cleaned.append(str(kwargs["cycle_id"]))


def contract() -> TargetContract:
    return TargetContract(
        schema_version=1,
        target_id="target-1",
        build=TargetBuildContract("Dockerfile", ("uv.lock",)),
        verification=TargetVerificationContract(
            gates=(
                TargetVerificationGateContract(
                    id="static",
                    command=("uv", "run", "ruff", "check", "."),
                    timeout_seconds=60,
                ),
                TargetVerificationGateContract(
                    id="tests",
                    command=("uv", "run", "pytest", "-q"),
                    timeout_seconds=60,
                ),
                TargetVerificationGateContract(
                    id="security",
                    command=("uv", "run", "python", "-m", "pip", "--version"),
                    timeout_seconds=60,
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
            stages=(CanaryStage(100, 1, 10),),
            max_candidate_error_rate=0.02,
            max_error_rate_delta=0.01,
            max_candidate_p95_latency_ms=250.0,
            max_p95_latency_ratio=1.25,
        ),
    )


def baseline_release() -> ReleasedVersion:
    return ReleasedVersion(
        id="release-0",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="a" * 40,
        artifact_digest="sha256:" + "0" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-0",
        promoted_at=datetime.now(UTC),
    )


def proposal() -> ChangeProposal:
    return ChangeProposal(
        id="proposal-1",
        target_id="target-1",
        baseline_release_id="release-0",
        baseline_commit="a" * 40,
        objective_revision_id="objective-1",
        diagnosis_id="diagnosis-1",
        acceptance_criteria=("fix seeded defect",),
        allowed_paths=("src/app.py",),
        forbidden_paths=("deploy",),
        max_implementation_attempts=2,
        mandatory_gates=("static", "tests", "security", "performance"),
        change_intent="Fix the diagnosed seeded defect without widening scope.",
    )


def test_dbos_iteration_promotes_and_replay_does_not_repeat_effects(tmp_path: Path) -> None:
    app_database = tmp_path / "app.db"
    system_database = tmp_path / "dbos.db"
    engine = create_engine(f"sqlite+pysqlite:///{app_database}")
    metadata.create_all(engine)

    cycles = CycleService(SqlCycleRepository(engine))
    cycles.create(
        DevelopmentCycle(
            id="cycle-1",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-0",
            state=CycleState.CHANGE_PROPOSED,
            change_proposal_id="proposal-1",
        )
    )
    proposal_repository = SqlChangeProposalRepository(engine)
    proposals = ProposalService(proposal_repository)
    proposal_repository.add(proposal())

    experiments = ExperimentService(SqlExperimentRepository(engine))
    traffic = FakeTraffic()
    observer = PassingCanaryObserver()
    canary = CanaryService(traffic, observer, experiments)
    releases = ReleaseService(
        cycles,
        experiments,
        SqlReleaseDecisionRepository(engine),
    )
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    catalog.register(baseline_release())
    catalog.set_serving("target-1", "release-0", operation_id="serve-baseline")
    finalization = ReleaseFinalizationService(catalog)
    source_promotion = FakeSourcePromotion()

    worktree = (tmp_path / "worktree").resolve()
    worktree.mkdir()
    engineering = FakeEngineering(worktree)
    verification = FakeVerification()
    build = FakeBuild()
    deployment = FakeDeployment()
    release_runtime = FakeReleaseRuntime()
    performance = FakePerformanceFactory()

    config: DBOSConfig = {
        "name": "autonomous-iteration-test",
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{system_database}",
    }
    DBOS(config=config)
    workflow = AutonomousIterationWorkflow(
        cycles=cycles,
        proposals=proposals,
        engineering=engineering,  # type: ignore[arg-type]
        verification=verification,  # type: ignore[arg-type]
        build=build,  # type: ignore[arg-type]
        deployment=deployment,  # type: ignore[arg-type]
        performance_gates=performance,
        experiments=experiments,
        canary=canary,
        releases=releases,
        finalization=finalization,
        release_runtime=release_runtime,  # type: ignore[arg-type]
        source_promotion=source_promotion,  # type: ignore[arg-type]
        contract=contract(),
        repository_root=tmp_path.resolve(),
        worktree_root=(tmp_path / "worktrees").resolve(),
        default_branch="main",
        canary_hold_sleep_seconds=0.001,
        config_name="test-autonomous-iteration",
    )
    DBOS.launch()
    try:
        with SetWorkflowID("cycle-1:autonomous-run"):
            first = workflow.run(
                "cycle-1",
                "proposal-1",
                "iteration-run-1",
            )
        with SetWorkflowID("cycle-1:autonomous-run"):
            second = workflow.run(
                "cycle-1",
                "proposal-1",
                "iteration-run-1",
            )
        assert first == second
        assert first["status"] == "promoted"
        assert cycles.get("cycle-1").state is CycleState.PROMOTED
        serving = catalog.serving("target-1")
        assert serving is not None
        assert serving.id == "cycle-1-release"
        assert serving.source_commit == "b" * 40
        assert engineering.calls == 1
        assert verification.calls == 1
        assert build.calls == 1
        assert deployment.calls == 1
        assert performance.calls == 1
        assert performance.gate.calls == 1
        assert traffic.applied == 1
        assert observer.calls == 1
        assert release_runtime.calls == ["release-0"]
        assert source_promotion.calls == 1
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
        engine.dispose()


def test_source_promotion_failure_compensates_to_baseline(tmp_path: Path) -> None:
    app_database = tmp_path / "rollback-app.db"
    system_database = tmp_path / "rollback-dbos.db"
    engine = create_engine(f"sqlite+pysqlite:///{app_database}")
    metadata.create_all(engine)

    cycles = CycleService(SqlCycleRepository(engine))
    cycles.create(
        DevelopmentCycle(
            id="cycle-1",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-0",
            state=CycleState.CHANGE_PROPOSED,
            change_proposal_id="proposal-1",
        )
    )
    proposal_repository = SqlChangeProposalRepository(engine)
    proposals = ProposalService(proposal_repository)
    proposal_repository.add(proposal())

    experiments = ExperimentService(SqlExperimentRepository(engine))
    traffic = FakeTraffic()
    observer = PassingCanaryObserver()
    canary = CanaryService(traffic, observer, experiments)
    releases = ReleaseService(
        cycles,
        experiments,
        SqlReleaseDecisionRepository(engine),
    )
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    catalog.register(baseline_release())
    catalog.set_serving("target-1", "release-0", operation_id="serve-baseline")
    finalization = ReleaseFinalizationService(catalog)
    source_promotion = FakeSourcePromotion(fail_promote=True)

    worktree = (tmp_path / "rollback-worktree").resolve()
    worktree.mkdir()
    engineering = FakeEngineering(worktree)
    verification = FakeVerification()
    build = FakeBuild()
    deployment = FakeDeployment()
    release_runtime = FakeReleaseRuntime()
    performance = FakePerformanceFactory()

    config: DBOSConfig = {
        "name": "autodev-rollback-test",
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{system_database}",
    }
    DBOS(config=config)
    workflow = AutonomousIterationWorkflow(
        cycles=cycles,
        proposals=proposals,
        engineering=engineering,  # type: ignore[arg-type]
        verification=verification,  # type: ignore[arg-type]
        build=build,  # type: ignore[arg-type]
        deployment=deployment,  # type: ignore[arg-type]
        performance_gates=performance,
        experiments=experiments,
        canary=canary,
        releases=releases,
        finalization=finalization,
        release_runtime=release_runtime,  # type: ignore[arg-type]
        source_promotion=source_promotion,  # type: ignore[arg-type]
        contract=contract(),
        repository_root=tmp_path.resolve(),
        worktree_root=(tmp_path / "rollback-worktrees").resolve(),
        default_branch="main",
        canary_hold_sleep_seconds=0.001,
        config_name="test-autonomous-iteration-rollback",
    )
    DBOS.launch()
    try:
        with SetWorkflowID("cycle-1:source-promotion-failure"):
            result = workflow.run(
                "cycle-1",
                "proposal-1",
                "iteration-run-rollback",
            )

        assert result["status"] == CycleState.ROLLED_BACK.value
        assert cycles.get("cycle-1").state is CycleState.ROLLED_BACK
        serving = catalog.serving("target-1")
        assert serving is not None
        assert serving.id == "release-0"
        assert traffic.current is not None
        assert traffic.current.candidate_weight_percent == 0
        assert source_promotion.calls == 1
        assert source_promotion.restored == ["a" * 40]
        assert source_promotion.cleaned == ["cycle-1"]
        assert deployment.stopped == ["cycle-1-candidate"]
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
        engine.dispose()
