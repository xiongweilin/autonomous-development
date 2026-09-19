from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.adapters.target_contract.toml import TomlTargetContractLoader
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.models import (
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
)
from autonomous_development.runtime import composition
from autonomous_development.runtime.composition import (
    RuntimeComposition,
    RuntimeConfigurationError,
    compose_runtime,
)
from autonomous_development.runtime.config import RuntimeSettings


class FakeDBOS:
    configured: ClassVar[list[object]] = []
    launches = 0
    schedules: ClassVar[list[object]] = []
    destroys: ClassVar[list[int]] = []

    def __init__(self, *, config: object) -> None:
        self.configured.append(config)

    @classmethod
    def launch(cls) -> None:
        cls.launches += 1

    @classmethod
    def apply_schedules(cls, schedules: object) -> None:
        cls.schedules.append(schedules)

    @classmethod
    def destroy(cls, *, workflow_completion_timeout_sec: int) -> None:
        cls.destroys.append(workflow_completion_timeout_sec)


class DummyWorkflow:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


def policy() -> MutationPolicy:
    return MutationPolicy(
        allowed_paths=("src", "tests"),
        forbidden_paths=("deploy",),
        max_changed_files=5,
        max_implementation_attempts=2,
    )


def write_contract(root: Path) -> None:
    (root / "autonomous-development.toml").write_text(
        """
schema_version = 1
target_id = "target-1"

[build]
dockerfile = "Dockerfile"
dependency_locks = ["uv.lock"]

[verification]
[[verification.gates]]
id = "tests"
command = ["uv", "run", "pytest"]
timeout_seconds = 60

[deployment]
container_port = 8000
health_path = "/health"
readiness_path = "/ready"
startup_timeout_seconds = 30

[performance]
script_path = "tests/performance/smoke.js"
required_threshold_metrics = ["http_req_failed"]
timeout_seconds = 60

[canary]
max_candidate_error_rate = 0.02
max_error_rate_delta = 0.01
max_candidate_p95_latency_ms = 250.0
max_p95_latency_ratio = 1.25

[[canary.stages]]
weight_percent = 10
min_duration_seconds = 10
min_requests = 10

[[canary.stages]]
weight_percent = 100
min_duration_seconds = 20
min_requests = 20
""".strip()
        + "\n",
        encoding="utf-8",
    )


def seed_database(database_url: str, repository_root: Path) -> None:
    engine = create_engine(database_url)
    metadata.create_all(engine)
    targets = TargetRegistryService(
        SqlTargetRepository(engine),
        SqlObjectiveRepository(engine),
    )
    contract_revision = TomlTargetContractLoader().load(str(repository_root)).revision
    assert contract_revision is not None
    objective = ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Fix attributable defects without reliability regression.",
        acceptance_criteria=("reported defect is fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=("error rate does not regress",),
        performance_constraints=("p95 remains bounded",),
        security_constraints=("no secret exposure",),
        mutation_policy=policy(),
        created_at=datetime.now(UTC),
    )
    targets.register(
        DevelopmentTarget(
            id="target-1",
            repository=str(repository_root),
            default_branch="main",
            target_contract_revision=contract_revision,
            active_objective_revision_id=objective.id,
            mutation_policy=policy(),
        ),
        objective,
    )
    releases = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    releases.register(
        ReleasedVersion(
            id="release-1",
            target_id="target-1",
            source_commit="a" * 40,
            source_tree="b" * 40,
            artifact_digest="sha256:" + "c" * 64,
            objective_revision_id=objective.id,
            deployment_id="deployment-1",
            promoted_at=datetime.now(UTC),
        )
    )
    releases.set_serving(
        "target-1",
        "release-1",
        operation_id="bootstrap-serving",
    )
    engine.dispose()


def settings(tmp_path: Path, database_url: str, **overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "database_url": SecretStr(database_url),
        "dbos_system_database_url": SecretStr(
            f"sqlite:///{tmp_path / 'dbos.sqlite3'}"
        ),
        "state_root": (tmp_path / "state").resolve(),
        "telemetry_queries": {"requests": "up"},
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_compose_runtime_wires_single_registered_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_root = (tmp_path / "product").resolve()
    repository_root.mkdir()
    write_contract(repository_root)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'app.sqlite3'}"
    seed_database(database_url, repository_root)

    bound: list[object] = []
    FakeDBOS.configured.clear()
    FakeDBOS.launches = 0
    FakeDBOS.schedules.clear()
    FakeDBOS.destroys.clear()
    monkeypatch.setattr(composition, "DBOS", FakeDBOS)
    monkeypatch.setattr(composition, "AutonomousIterationWorkflow", DummyWorkflow)
    monkeypatch.setattr(composition, "PostPromotionSoakWorkflow", DummyWorkflow)
    monkeypatch.setattr(composition, "FeedbackAutonomyWorkflow", DummyWorkflow)
    monkeypatch.setattr(
        composition,
        "bind_scheduled_feedback_workflow",
        lambda workflow: bound.append(workflow),
    )

    runtime = compose_runtime(settings(tmp_path, database_url))

    assert isinstance(runtime, RuntimeComposition)
    assert runtime.target_id == "target-1"
    assert runtime.schedule_name.startswith("autodev-feedback-target-1-")
    assert len(FakeDBOS.configured) == 1
    assert len(bound) == 1
    assert runtime.settings.evidence_root.is_dir()
    assert runtime.settings.traffic_state_root.is_dir()
    assert runtime.settings.worktree_root.is_dir()
    assert runtime.settings.codex_thread_journal_root.is_dir()

    client = TestClient(runtime.app)
    assert client.get("/health").status_code == 200
    assert client.get("/product/__autodev/metrics/0").status_code == 404

    runtime.launch()
    runtime.launch()
    assert FakeDBOS.launches == 1
    assert len(FakeDBOS.schedules) == 1
    schedule = FakeDBOS.schedules[0]
    assert isinstance(schedule, list)
    assert schedule[0]["context"] == "target-1"

    runtime.close()
    assert FakeDBOS.destroys == [30]


def test_compose_runtime_rejects_proxy_binding_mismatch(tmp_path: Path) -> None:
    configured = RuntimeSettings(
        database_url=SecretStr("sqlite+pysqlite:///:memory:"),
        dbos_system_database_url=SecretStr("sqlite:///:memory:"),
        state_root=tmp_path.resolve(),
        api_port=8765,
        canary_proxy_base_url="http://127.0.0.1:9999/product",
        telemetry_queries={"requests": "up"},
    )
    try:
        compose_runtime(configured)
    except RuntimeConfigurationError as exc:
        assert "api_port" in str(exc)
    else:
        raise AssertionError("proxy binding mismatch must fail closed")
