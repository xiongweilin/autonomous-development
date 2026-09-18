from __future__ import annotations

from dataclasses import dataclass

from .build import DockerBuildSpec
from .models import CanaryStage, MutationPolicy
from .verification import VerificationPlan


@dataclass(frozen=True, slots=True)
class DeploymentContract:
    container_port: int
    health_path: str
    readiness_path: str
    metrics_path: str

    def __post_init__(self) -> None:
        if not 1 <= self.container_port <= 65535:
            raise ValueError("container port must be valid")
        for field_name, value in (
            ("health path", self.health_path),
            ("readiness path", self.readiness_path),
            ("metrics path", self.metrics_path),
        ):
            if not value.startswith("/"):
                raise ValueError(f"{field_name} must start with /")


@dataclass(frozen=True, slots=True)
class PerformanceContract:
    k6_script: str
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        normalized = self.k6_script.replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("k6 script must be repository-relative")
        if self.timeout_seconds < 1:
            raise ValueError("performance timeout must be positive")


@dataclass(frozen=True, slots=True)
class FeedbackContract:
    attribution_header: str = "X-Autodev-Request-ID"

    def __post_init__(self) -> None:
        if not self.attribution_header.strip():
            raise ValueError("feedback attribution header must be non-empty")


@dataclass(frozen=True, slots=True)
class TargetContract:
    schema_version: int
    target_id: str
    default_branch: str
    mutation: MutationPolicy
    verification: VerificationPlan
    build: DockerBuildSpec
    deployment: DeploymentContract
    performance: PerformanceContract
    canary_stages: tuple[CanaryStage, ...]
    feedback: FeedbackContract

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported target contract schema version")
        if not self.target_id.strip() or not self.default_branch.strip():
            raise ValueError("target id and default branch must be non-empty")
        self.verification.require_platform_baseline()
        if not self.canary_stages:
            raise ValueError("target contract requires canary stages")
        weights = tuple(stage.weight_percent for stage in self.canary_stages)
        if weights != tuple(sorted(weights)) or weights[-1] != 100:
            raise ValueError("canary stages must increase monotonically to 100 percent")
