from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.soak_decisions import SqlSoakDecisionRepository
from autonomous_development.application.cycles import CycleService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.soak import PostPromotionSoakService
from autonomous_development.domain.canary import CanaryGuardrails, CanaryStageEvidence
from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentCycle,
    ReleasedVersion,
)
from autonomous_development.ports.traffic import TrafficRouteState, TrafficSplit
from autonomous_development.workflows.post_promotion_soak import (
    PostPromotionSoakWorkflow,
)


class IdempotentTraffic:
    def __init__(self) -> None:
        self.states: dict[str, TrafficRouteState] = {}
        self.creations = 0

    def apply(self, split: TrafficSplit) -> TrafficRouteState:
        existing = self.states.get(split.operation_id)
        if existing is not None:
            return existing
        self.creations += 1
        state = TrafficRouteState(
            experiment_id=split.experiment_id,
            stage_index=split.stage_index,
            candidate_weight_percent=split.candidate_weight_percent,
            generation=self.creations,
            evidence_ref=f"traffic:{self.creations}",
        )
        self.states[split.operation_id] = state
        return state

    def restore_control(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        control_base_url: str,
        candidate_base_url: str,
        operation_id: str,
    ) -> TrafficRouteState:
        return self.apply(
            TrafficSplit(
                experiment_id=experiment_id,
                stage_index=stage_index,
                control_base_url=control_base_url,
                candidate_base_url=candidate_base_url,
                candidate_weight_percent=0,
                operation_id=operation_id,
            )
        )


class AccumulatingObserver:
    def __init__(self) -> None:
        self.calls = 0
        self.generations: list[int] = []

    def observe(
        self,
        *,
        experiment_id: str,
        stage_index: int,
        stage: CanaryStage,
        route_state: TrafficRouteState,
    ) -> CanaryStageEvidence:
        self.calls += 1
        self.generations.append(route_state.generation)
        sufficient = self.calls >= 2
        requests = stage.min_requests if sufficient else max(1, stage.min_requests // 10)
        duration = (
            stage.min_duration_seconds
            if sufficient
            else max(0, stage.min_duration_seconds // 10)
        )
        return CanaryStageEvidence(
            experiment_id=experiment_id,
            stage_index=stage_index,
            weight_percent=stage.weight_percent,
            observed_duration_seconds=duration,
            total_requests=requests,
            candidate_requests=requests,
            control_requests=0,
            candidate_error_rate=0.0,
            control_error_rate=None,
            candidate_p95_latency_ms=80.0,
            control_p95_latency_ms=None,
            evidence_refs=(f"soak:{self.calls}",),
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


def test_dbos_soak_holds_then_completes_and_replay_is_stable(tmp_path: Path) -> None:
    app_database = tmp_path / "app.db"
    system_database = tmp_path / "dbos.db"
    engine = create_engine(f"sqlite+pysqlite:///{app_database}")
    metadata.create_all(engine)

    cycles = CycleService(SqlCycleRepository(engine))
    cycles.create(promoted_cycle())
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    catalog.register(release("release-0", "deployment-0"))
    catalog.register(release("release-1", "deployment-1"))
    catalog.set_serving("target-1", "release-1", operation_id="serve-candidate")

    traffic = IdempotentTraffic()
    observer = AccumulatingObserver()
    service = PostPromotionSoakService(
        cycles,
        traffic,
        observer,
        catalog,
        SqlSoakDecisionRepository(engine),
    )

    config: DBOSConfig = {
        "name": "post-promotion-soak-test",
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{system_database}",
    }
    DBOS(config=config)
    workflow = PostPromotionSoakWorkflow(
        service,
        stage=CanaryStage(100, 10, 20),
        guardrails=CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        hold_sleep_seconds=0.001,
        config_name="test-post-promotion-soak",
    )
    DBOS.launch()
    try:
        with SetWorkflowID("cycle-1:soak"):
            first = workflow.run("cycle-1", "soak-run-1")
        with SetWorkflowID("cycle-1:soak"):
            second = workflow.run("cycle-1", "soak-run-1")

        assert first == second
        assert first["status"] == CycleState.COMPLETED.value
        assert cycles.get("cycle-1").state is CycleState.COMPLETED
        assert observer.calls == 2
        assert observer.generations == [1, 1]
        assert traffic.creations == 1
        serving = catalog.serving("target-1")
        assert serving is not None
        assert serving.id == "release-1"
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
        engine.dispose()
