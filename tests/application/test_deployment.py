from datetime import UTC, datetime

from autonomous_development.application.deployment import DeploymentService
from autonomous_development.domain.enums import DeploymentState
from autonomous_development.domain.models import BuildArtifact, CanaryStage, Deployment
from autonomous_development.ports.deployment import DeploymentRuntime, DeploymentSpec
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetDeploymentContract,
    TargetPerformanceContract,
)


class FakeProvider:
    def __init__(self) -> None:
        self.spec: DeploymentSpec | None = None

    def ensure(self, spec: DeploymentSpec) -> DeploymentRuntime:
        self.spec = spec
        return DeploymentRuntime(
            deployment_id=spec.deployment_id,
            container_id="container-1",
            base_url="http://127.0.0.1:49155",
            evidence_ref="effect:1",
        )

    def stop(self, deployment_id: str) -> None:
        pass


class FakeObserver:
    def wait_ready(
        self,
        spec: DeploymentSpec,
        runtime: DeploymentRuntime,
        *,
        health_path: str,
        readiness_path: str,
        timeout_seconds: int,
    ) -> Deployment:
        assert health_path == "/health"
        assert readiness_path == "/ready"
        assert timeout_seconds == 30
        return Deployment(
            id=spec.deployment_id,
            target_id=spec.target_id,
            artifact_id=spec.artifact_id,
            environment="local-candidate",
            state=DeploymentState.READY,
            observed_at=datetime.now(UTC),
            observation_refs=("observation:1",),
        )


def test_deployment_service_uses_artifact_digest_and_contract() -> None:
    artifact = BuildArtifact(
        id="artifact-1",
        candidate_id="candidate-1",
        image_digest="sha256:" + "a" * 64,
        source_tree_hash="b" * 40,
        build_definition_digest="sha256:" + "c" * 64,
        dependency_lock_digest="sha256:" + "d" * 64,
        build_evidence_ref="build:1",
        sbom_digest="sha256:" + "e" * 64,
        sbom_ref="sbom:1",
        vulnerability_scan_ref="scan:1",
    )
    contract = TargetContract(
        schema_version=1,
        target_id="target-1",
        build=TargetBuildContract("Dockerfile", ("uv.lock",)),
        deployment=TargetDeploymentContract(8000, "/health", "/ready", 30),
        performance=TargetPerformanceContract(
            "tests/performance/smoke.js",
            ("http_req_failed",),
            60,
        ),
        canary=TargetCanaryContract(
            stages=(CanaryStage(100, 60, 100),),
            max_candidate_error_rate=0.02,
            max_error_rate_delta=0.01,
            max_candidate_p95_latency_ms=250.0,
            max_p95_latency_ratio=1.25,
        ),
    )
    provider = FakeProvider()
    runtime, deployment = DeploymentService(provider, FakeObserver()).deploy_candidate(
        artifact,
        contract,
        deployment_id="deploy-1",
    )
    assert provider.spec is not None
    assert provider.spec.image_digest == artifact.image_digest
    assert provider.spec.container_port == 8000
    assert runtime.base_url.startswith("http://127.0.0.1:")
    assert deployment.state is DeploymentState.READY
