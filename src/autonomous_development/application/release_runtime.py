from __future__ import annotations

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.models import ReleasedVersion
from autonomous_development.ports.deployment import (
    DeploymentProvider,
    DeploymentRuntime,
    DeploymentSpec,
)
from autonomous_development.ports.target_contract import TargetContract


class ReleaseRuntimeService:
    """Resolve durable release identity into current local deployment reality."""

    def __init__(
        self,
        releases: ReleaseCatalogService,
        deployment: DeploymentProvider,
        contract: TargetContract,
    ) -> None:
        self._releases = releases
        self._deployment = deployment
        self._contract = contract

    def resolve(self, release_id: str) -> DeploymentRuntime:
        release = self._releases.get(release_id)
        return self._ensure(release)

    def resolve_serving(self, target_id: str) -> tuple[ReleasedVersion, DeploymentRuntime]:
        release = self._releases.serving(target_id)
        if release is None:
            raise ValueError(f"target {target_id} has no serving release")
        return release, self._ensure(release)

    def _ensure(self, release: ReleasedVersion) -> DeploymentRuntime:
        if release.target_id != self._contract.target_id:
            raise ValueError("release target does not match configured target contract")
        runtime = self._deployment.ensure(
            DeploymentSpec(
                deployment_id=release.deployment_id,
                target_id=release.target_id,
                artifact_id=f"release:{release.id}",
                image_digest=release.artifact_digest,
                container_port=self._contract.deployment.container_port,
            )
        )
        if runtime.deployment_id != release.deployment_id:
            raise RuntimeError("deployment provider resolved a different release deployment")
        return runtime
