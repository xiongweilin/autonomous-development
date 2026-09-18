from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from autonomous_development.domain.models import Deployment


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    deployment_id: str
    target_id: str
    artifact_id: str
    image_digest: str
    container_port: int

    def __post_init__(self) -> None:
        for label, value in (
            ("deployment_id", self.deployment_id),
            ("target_id", self.target_id),
            ("artifact_id", self.artifact_id),
            ("image_digest", self.image_digest),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if not self.image_digest.startswith("sha256:"):
            raise ValueError("deployment requires a content-addressed image")
        if not 1 <= self.container_port <= 65535:
            raise ValueError("container_port must be between 1 and 65535")


@dataclass(frozen=True, slots=True)
class DeploymentRuntime:
    deployment_id: str
    container_id: str
    base_url: str
    evidence_ref: str


class DeploymentProvider(Protocol):
    def ensure(self, spec: DeploymentSpec) -> DeploymentRuntime: ...

    def stop(self, deployment_id: str) -> None: ...


class DeploymentObserver(Protocol):
    def wait_ready(
        self,
        spec: DeploymentSpec,
        runtime: DeploymentRuntime,
        *,
        health_path: str,
        readiness_path: str,
        timeout_seconds: int,
    ) -> Deployment: ...


class DeploymentProviderError(RuntimeError):
    pass


class DeploymentObservationError(RuntimeError):
    pass
