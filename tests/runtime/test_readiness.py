from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentTarget,
    MutationPolicy,
    ProductObjectiveRevision,
    ReleasedVersion,
)
from autonomous_development.ports.process import CommandRequest, CommandResult
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetDeploymentContract,
    TargetPerformanceContract,
    TargetVerificationContract,
    TargetVerificationGateContract,
)
from autonomous_development.runtime.config import RuntimeSettings
from autonomous_development.runtime.readiness import RuntimeReadinessService

CONTRACT_REVISION = "sha256:" + "d" * 64


class Runner:
    def run(self, request: CommandRequest) -> CommandResult:
        return CommandResult(
            returncode=0,
            stdout="ok",
            stderr="",
        )




class EmptyTraffic:
    def read_current(self):
        return None

class Contracts:
    def load(self, repository_root: str) -> TargetContract:
        del repository_root
        return TargetContract(
            schema_version=1,
            target_id="target-1",
            build=TargetBuildContract(
                dockerfile="Dockerfile",
                dependency_locks=("uv.lock",),
            ),
            verification=TargetVerificationContract(
                gates=(
                    TargetVerificationGateContract(
                        id="tests",
                        command=("uv", "run", "pytest"),
                        timeout_seconds=60,
                    ),
                )
            ),
            deployment=TargetDeploymentContract(
                container_port=8000,
                health_path="/health",
                readiness_path="/ready",
            ),
            performance=TargetPerformanceContract(
                script_path="tests/load.js",
                required_threshold_metrics=("http_req_duration",),
            ),
            canary=TargetCanaryContract(
                stages=(CanaryStage(100, 1, 1),),
                max_candidate_error_rate=0.02,
                max_error_rate_delta=0.01,
                max_candidate_p95_latency_ms=250.0,
                max_p95_latency_ratio=1.25,
            ),
            revision=CONTRACT_REVISION,
        )


def configured(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        database_url=SecretStr("sqlite+pysqlite:///:memory:"),
        dbos_system_database_url=SecretStr("sqlite:///:memory:"),
        state_root=tmp_path.resolve(),
        telemetry_queries={"requests": "up"},
    )


def runtime_state(tmp_path: Path) -> tuple[
    object,
    TargetRegistryService,
    ReleaseCatalogService,
]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    policy = MutationPolicy(
        allowed_paths=("src",),
        max_changed_files=3,
        max_implementation_attempts=2,
    )
    target = DevelopmentTarget(
        id="target-1",
        repository=str(tmp_path.resolve()),
        default_branch="main",
        target_contract_revision=CONTRACT_REVISION,
        active_objective_revision_id="objective-1",
        mutation_policy=policy,
    )
    objective = ProductObjectiveRevision(
        id="objective-1",
        target_id="target-1",
        statement="Improve the product.",
        acceptance_criteria=("defect fixed",),
        primary_metrics=("correctness",),
        reliability_constraints=(),
        performance_constraints=(),
        security_constraints=(),
        mutation_policy=policy,
        created_at=datetime.now(UTC),
    )
    targets = TargetRegistryService(
        SqlTargetRepository(engine),
        SqlObjectiveRepository(engine),
    )
    targets.register(target, objective)

    releases = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    releases.register(
        ReleasedVersion(
            id="release-1",
            target_id="target-1",
            source_commit="a" * 40,
            source_tree="b" * 40,
            artifact_digest="sha256:" + "c" * 64,
            objective_revision_id="objective-1",
            deployment_id="deployment-1",
            promoted_at=datetime.now(UTC),
        )
    )
    releases.set_serving(
        "target-1",
        "release-1",
        operation_id="bootstrap-serving",
    )
    return engine, targets, releases


def test_readiness_reports_all_required_runtime_boundaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, targets, releases = runtime_state(tmp_path)
    monkeypatch.setattr(
        "autonomous_development.runtime.readiness.shutil.which",
        lambda command: f"/bin/{command}",
    )

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/-/ready":
            return httpx.Response(200)
        if request.url.path == "/product/__autodev/metrics/0":
            return httpx.Response(404)
        return httpx.Response(500)

    service = RuntimeReadinessService(
        settings=configured(tmp_path),
        engine=engine,  # type: ignore[arg-type]
        targets=targets,
        releases=releases,
        contracts=Contracts(),  # type: ignore[arg-type]
        runner=Runner(),  # type: ignore[arg-type]
        http_transport=httpx.MockTransport(transport),
    )
    report = service.check()

    assert report.ready
    assert {check.name for check in report.checks} == {
        "database",
        "python",
        "git",
        "uv",
        "codex",
        "docker-daemon",
        "k6",
        "syft",
        "grype",
        "registered-target",
        "prometheus",
        "canary-proxy",
    }


def test_readiness_fails_closed_without_telemetry_queries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, targets, releases = runtime_state(tmp_path)
    monkeypatch.setattr(
        "autonomous_development.runtime.readiness.shutil.which",
        lambda command: f"/bin/{command}",
    )
    settings = configured(tmp_path)
    settings.telemetry_queries.clear()

    service = RuntimeReadinessService(
        settings=settings,
        engine=engine,  # type: ignore[arg-type]
        targets=targets,
        releases=releases,
        contracts=Contracts(),  # type: ignore[arg-type]
        runner=Runner(),  # type: ignore[arg-type]
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    report = service.check()

    assert not report.ready
    prometheus = next(check for check in report.checks if check.name == "prometheus")
    assert prometheus.detail == "no telemetry queries configured"


def test_readiness_fails_closed_without_active_product_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, targets, releases = runtime_state(tmp_path)
    monkeypatch.setattr(
        "autonomous_development.runtime.readiness.shutil.which",
        lambda command: f"/bin/{command}",
    )

    service = RuntimeReadinessService(
        settings=configured(tmp_path),
        engine=engine,  # type: ignore[arg-type]
        targets=targets,
        releases=releases,
        contracts=Contracts(),  # type: ignore[arg-type]
        runner=Runner(),  # type: ignore[arg-type]
        traffic=EmptyTraffic(),  # type: ignore[arg-type]
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    report = service.check()

    assert not report.ready
    proxy = next(check for check in report.checks if check.name == "canary-proxy")
    assert proxy.detail == "no active product route"
