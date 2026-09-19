from __future__ import annotations

from autonomous_development.domain.models import BuildArtifact, Deployment
from autonomous_development.ports.deployment import (
    DeploymentObserver,
    DeploymentProvider,
    DeploymentRuntime,
    DeploymentSpec,
)
from autonomous_development.ports.target_contract import TargetContract


class DeploymentService:
    def __init__(
        self,
        provider: DeploymentProvider,
        observer: DeploymentObserver,
    ) -> None:
        self._provider = provider
        self._observer = observer

    def deploy_candidate(
        self,
        artifact: BuildArtifact,
        contract: TargetContract,
        *,
        deployment_id: str,
    ) -> tuple[DeploymentRuntime, Deployment]:
        spec = DeploymentSpec(
            deployment_id=deployment_id,
            target_id=contract.target_id,
            artifact_id=artifact.id,
            image_digest=artifact.image_digest,
            container_port=contract.deployment.container_port,
        )
        runtime = self._provider.ensure(spec)
        deployment = self._observer.wait_ready(
            spec,
            runtime,
            health_path=contract.deployment.health_path,
            readiness_path=contract.deployment.readiness_path,
            timeout_seconds=contract.deployment.startup_timeout_seconds,
        )
        return runtime, deployment

    def stop(self, deployment_id: str) -> None:
        self._provider.stop(deployment_id)
