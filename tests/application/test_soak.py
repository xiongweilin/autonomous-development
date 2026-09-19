from datetime import UTC, datetime

from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.soak_decisions import SqlSoakDecisionRepository
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.soak import PostPromotionSoakService
from autonomous_development.domain.canary import CanaryGuardrails, CanaryStageEvidence
from autonomous_development.domain.enums import (
    CycleState,
    ReleaseDecisionKind,
    SoakDecisionKind,
)
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentCycle,
    ReleasedVersion,
)
from autonomous_development.ports.traffic import (
    TrafficRouteSnapshot,
    TrafficRouteState,
    TrafficSplit,
)


class FakeReleaseRuntime:
    def __init__(self, catalog: ReleaseCatalogService) -> None:
        self.catalog = catalog
        self.calls: list[str] = []
        self.stopped_deployments: list[str] = []
        self.stopped_releases: list[str] = []

    def resolve(self, release_id: str):
        from autonomous_development.ports.deployment import DeploymentRuntime

        self.calls.append(release_id)
        port = 4100 if release_id == "release-0" else 4200
        deployment_id = "deployment-0" if release_id == "release-0" else "deployment-1"
        return DeploymentRuntime(
            deployment_id=deployment_id,
            container_id=f"container-{release_id}",
            base_url=f"http://127.0.0.1:{port}",
            evidence_ref=f"runtime:{release_id}",
        )

    def resolve_serving(self, target_id: str):
        release = self.catalog.serving(target_id)
        if release is None:
            raise ValueError("missing serving release")
        return release, self.resolve(release.id)

    def stop_deployment(self, deployment_id: str) -> None:
        self.stopped_deployments.append(deployment_id)

    def stop_release(self, release_id: str) -> None:
        self.stopped_releases.append(release_id)


class FakeSourcePromotion:
    def __init__(self) -> None:
        self.restored: list[str] = []
        self.cleaned: list[str] = []

    def restore_baseline(self, **kwargs: object) -> None:
        self.restored.append(str(kwargs["baseline_commit"]))

    def cleanup_cycle(self, **kwargs: object) -> None:
        self.cleaned.append(str(kwargs["cycle_id"]))


class FakeTraffic:
    def __init__(self) -> None:
        self.applied: list[TrafficSplit] = []
        self.restored: list[str] = []
        self._states: dict[str, TrafficRouteState] = {}
        self._current_split: TrafficSplit | None = None
        self._current_state: TrafficRouteState | None = None

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        existing = self._states.get(split.operation_id)
        if existing is not None:
            return existing
        self.applied.append(split)
        state = TrafficRouteState(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            candidate_weight_percent=split.candidate_weight_percent,
            generation=len(self._states) + 1,
            evidence_ref=f"traffic:{len(self._states) + 1}",
        )
        self._states[split.operation_id] = state
        self._current_split = split
        self._current_state = state
        return state

    def read_current(self) -> TrafficRouteSnapshot | None:
        if self._current_split is None or self._current_state is None:
            return None
        split = self._current_split
        state = self._current_state
        return TrafficRouteSnapshot(
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

    def restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> TrafficRouteState:
        self.restored.append(operation_id)
        return self.apply(
            TrafficSplit(
                experiment_id=experiment_id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                candidate_weight_percent=0,
                operation_id=operation_id,
                target_id="target-1",
                control_release_id="release-0",
                candidate_deployment_id="deployment-1",
            )
        )


class RegressionObserver:
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
            candidate_error_rate=0.2,
            control_error_rate=None,
            candidate_p95_latency_ms=100.0,
            control_p95_latency_ms=None,
            evidence_refs=("soak:regression",),
        )


def release(release_id: str, deployment_id: str) -> ReleasedVersion:
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


def promoted_cycle() -> DevelopmentCycle:
    return DevelopmentCycle(
        id="cycle-1",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-0",
        state=CycleState.PROMOTED,
        version=10,
        candidate_id="candidate-1",
        verification_run_id="verify-1",
        artifact_id="artifact-1",
        candidate_deployment_id="deployment-1",
        experiment_id="experiment-1",
        release_decision=ReleaseDecisionKind.PROMOTE,
    )


def test_soak_regression_restores_baseline_traffic_and_serving_release(
    tmp_path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    cycles = CycleService(SqlCycleRepository(engine))
    cycles.create(promoted_cycle())

    release_repository = SqlReleasedVersionRepository(engine)
    catalog = ReleaseCatalogService(release_repository)
    catalog.register(release("release-0", "deployment-0"))
    catalog.register(release("release-1", "deployment-1"))
    catalog.set_serving("target-1", "release-1", operation_id="serve-candidate")

    traffic = FakeTraffic()
    observer = RegressionObserver()
    runtime = FakeReleaseRuntime(catalog)
    source = FakeSourcePromotion()
    service = PostPromotionSoakService(
        cycles,
        traffic,
        observer,
        catalog,
        SqlSoakDecisionRepository(engine),
        runtime,  # type: ignore[arg-type]
        source,  # type: ignore[arg-type]
        repository_root=tmp_path.resolve(),
        worktree_root=(tmp_path / "worktrees").resolve(),
        default_branch="main",
    )
    soaking = service.start("cycle-1", operation_id="soak:start")
    assert soaking.state is CycleState.SOAKING

    decision = service.observe(
        "cycle-1",
        CanaryStage(100, 60, 100),
        CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
        route_operation_id="soak:route",
        decision_operation_id="soak:decision:0",
    )
    assert decision.kind is SoakDecisionKind.ROLLBACK

    rolled_back = service.apply(
        "cycle-1",
        decision_operation_id="soak:decision:0",
        effect_operation_id="soak:effect:0",
    )
    assert rolled_back.state is CycleState.ROLLED_BACK
    serving = catalog.serving("target-1")
    assert serving is not None
    assert serving.id == "release-0"
    assert traffic.restored == ["soak:effect:0:traffic"]
    assert traffic.applied[0].control_base_url == "http://127.0.0.1:4100"
    assert traffic.applied[0].candidate_base_url == "http://127.0.0.1:4200"
    assert source.restored == ["a" * 40]
    assert source.cleaned == ["cycle-1"]
    assert runtime.stopped_deployments == ["deployment-1"]

    replay = service.apply(
        "cycle-1",
        decision_operation_id="soak:decision:0",
        effect_operation_id="soak:effect:0",
    )
    assert replay == rolled_back
