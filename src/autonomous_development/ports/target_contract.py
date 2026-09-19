from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from autonomous_development.domain.models import CanaryStage


def _relative_path(value: str, field_name: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must be a relative path without traversal")
    return path.as_posix()


def _http_path(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("/") or "://" in normalized:
        raise ValueError(f"{field_name} must be an absolute HTTP path")
    return normalized


@dataclass(frozen=True, slots=True)
class TargetBuildContract:
    dockerfile: str
    dependency_locks: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dockerfile", _relative_path(self.dockerfile, "dockerfile"))
        if not self.dependency_locks:
            raise ValueError("target requires at least one dependency lock")
        object.__setattr__(
            self,
            "dependency_locks",
            tuple(_relative_path(item, "dependency lock") for item in self.dependency_locks),
        )


@dataclass(frozen=True, slots=True)
class TargetVerificationGateContract:
    id: str
    command: tuple[str, ...]
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("verification gate id must be non-empty")
        if self.id == "performance":
            raise ValueError("performance is reserved for the dedicated performance gate")
        if not self.command or any(not item.strip() for item in self.command):
            raise ValueError("verification gate command must be a non-empty argv")
        if self.timeout_seconds < 1:
            raise ValueError("verification gate timeout must be positive")


@dataclass(frozen=True, slots=True)
class TargetVerificationContract:
    gates: tuple[TargetVerificationGateContract, ...]

    def __post_init__(self) -> None:
        if not self.gates:
            raise ValueError("target requires at least one pre-deployment verification gate")
        ids = tuple(gate.id for gate in self.gates)
        if len(set(ids)) != len(ids):
            raise ValueError("verification gate ids must be unique")


@dataclass(frozen=True, slots=True)
class TargetDeploymentContract:
    container_port: int
    health_path: str
    readiness_path: str
    startup_timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if not 1 <= self.container_port <= 65535:
            raise ValueError("container_port must be between 1 and 65535")
        if self.startup_timeout_seconds < 1:
            raise ValueError("startup timeout must be positive")
        object.__setattr__(self, "health_path", _http_path(self.health_path, "health_path"))
        object.__setattr__(
            self,
            "readiness_path",
            _http_path(self.readiness_path, "readiness_path"),
        )


@dataclass(frozen=True, slots=True)
class TargetPerformanceContract:
    script_path: str
    required_threshold_metrics: tuple[str, ...]
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "script_path",
            _relative_path(self.script_path, "performance script"),
        )
        if not self.required_threshold_metrics:
            raise ValueError("performance contract requires threshold metrics")
        if any(not item.strip() for item in self.required_threshold_metrics):
            raise ValueError("performance threshold metric names must be non-empty")
        if self.timeout_seconds < 1:
            raise ValueError("performance timeout must be positive")


@dataclass(frozen=True, slots=True)
class TargetCanaryContract:
    stages: tuple[CanaryStage, ...]
    max_candidate_error_rate: float
    max_error_rate_delta: float
    max_candidate_p95_latency_ms: float
    max_p95_latency_ratio: float

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("canary contract requires stages")
        weights = tuple(stage.weight_percent for stage in self.stages)
        if weights != tuple(sorted(weights)) or weights[-1] != 100:
            raise ValueError("canary stages must increase monotonically and end at 100")
        if not 0.0 <= self.max_candidate_error_rate <= 1.0:
            raise ValueError("max_candidate_error_rate must be between 0 and 1")
        if not 0.0 <= self.max_error_rate_delta <= 1.0:
            raise ValueError("max_error_rate_delta must be between 0 and 1")
        if self.max_candidate_p95_latency_ms <= 0:
            raise ValueError("max_candidate_p95_latency_ms must be positive")
        if self.max_p95_latency_ratio < 1.0:
            raise ValueError("max_p95_latency_ratio must be at least 1")


@dataclass(frozen=True, slots=True)
class TargetContract:
    schema_version: int
    target_id: str
    build: TargetBuildContract
    verification: TargetVerificationContract
    deployment: TargetDeploymentContract
    performance: TargetPerformanceContract
    canary: TargetCanaryContract
    revision: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported target contract schema")
        if not self.target_id.strip():
            raise ValueError("target_id must be non-empty")
        if self.revision is not None and not self.revision.startswith("sha256:"):
            raise ValueError("target contract revision must be a sha256 digest")

    @property
    def mandatory_gates(self) -> tuple[str, ...]:
        return (*(gate.id for gate in self.verification.gates), "performance")


class TargetContractLoader(Protocol):
    def load(self, repository_root: str) -> TargetContract: ...
