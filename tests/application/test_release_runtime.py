from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.release_runtime import ReleaseRuntimeService
from autonomous_development.domain.models import CanaryStage, ReleasedVersion
from autonomous_development.ports.deployment import DeploymentRuntime, DeploymentSpec
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetDeploymentContract,
    TargetPerformanceContract,
    TargetVerificationContract,
    TargetVerificationGateContract,
)


class FakeDeploymentProvider:
    def __init__(self, *, wrong_deployment_id: bool = False) -> None:
        self.specs: list[DeploymentSpec] = []
        self.wrong_deployment_id = wrong_deployment_id

    def ensure(self, spec: DeploymentSpec) -> DeploymentRuntime:
        self.specs.append(spec)
        deployment_id = "wrong-deployment" if self.wrong_deployment_id else spec.deployment_id
        return DeploymentRuntime(
            deployment_id=deployment_id,
            container_id=f"container-{spec.deployment_id}",
            base_url="http://127.0.0.1:4100",
            evidence_ref="runtime:1",
        )

    def stop(self, deployment_id: str) -> None:
        del deployment_id


def contract() -> TargetContract:
    return TargetContract(
        schema_version=1,
        target_id="target-1",
        build=TargetBuildContract("Dockerfile", ("uv.lock",)),
        verification=TargetVerificationContract(
            gates=(
                TargetVerificationGateContract(
                    id="tests",
                    command=("uv", "run", "pytest", "-q"),
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


def release() -> ReleasedVersion:
    return ReleasedVersion(
        id="release-1",
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id="deployment-1",
        promoted_at=datetime.now(UTC),
    )


def test_release_runtime_is_resolved_from_durable_release_identity() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    catalog.register(release())
    catalog.set_serving("target-1", "release-1", operation_id="serve-1")
    deployment = FakeDeploymentProvider()
    service = ReleaseRuntimeService(catalog, deployment, contract())

    resolved = service.resolve("release-1")
    serving, serving_runtime = service.resolve_serving("target-1")

    assert resolved == serving_runtime
    assert serving.id == "release-1"
    assert len(deployment.specs) == 2
    spec = deployment.specs[0]
    assert spec.deployment_id == "deployment-1"
    assert spec.target_id == "target-1"
    assert spec.image_digest == release().artifact_digest
    assert spec.container_port == 8000



def test_release_runtime_fails_closed_without_serving_release() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    service = ReleaseRuntimeService(
        ReleaseCatalogService(SqlReleasedVersionRepository(engine)),
        FakeDeploymentProvider(),
        contract(),
    )

    with pytest.raises(ValueError, match="no serving release"):
        service.resolve_serving("target-1")


def test_release_runtime_rejects_release_for_another_target() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    foreign = ReleasedVersion(
        id="release-foreign",
        target_id="target-2",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-2",
        deployment_id="deployment-2",
        promoted_at=datetime.now(UTC),
    )
    catalog.register(foreign)
    service = ReleaseRuntimeService(catalog, FakeDeploymentProvider(), contract())

    with pytest.raises(ValueError, match="target does not match"):
        service.resolve("release-foreign")


def test_release_runtime_rejects_mismatched_deployment_reconciliation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    catalog = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
    catalog.register(release())
    service = ReleaseRuntimeService(
        catalog,
        FakeDeploymentProvider(wrong_deployment_id=True),
        contract(),
    )

    with pytest.raises(RuntimeError, match="different release deployment"):
        service.resolve("release-1")
