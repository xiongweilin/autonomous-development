from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from autonomous_development.domain.build import DockerBuildSpec
from autonomous_development.domain.commands import CommandSpec
from autonomous_development.domain.models import CanaryStage, MutationPolicy
from autonomous_development.domain.target import (
    DeploymentContract,
    FeedbackContract,
    PerformanceContract,
    TargetContract,
)
from autonomous_development.domain.verification import (
    VerificationGateSpec,
    VerificationPlan,
)


@dataclass(frozen=True, slots=True)
class LoadedTargetContract:
    contract: TargetContract
    revision_sha256: str


def load_target_contract(path: Path) -> LoadedTargetContract:
    payload = path.read_bytes()
    raw = yaml.safe_load(payload)
    if not isinstance(raw, dict):
        raise ValueError("target contract root must be a mapping")

    target = _mapping(raw, "target")
    mutation = _mapping(raw, "mutation")
    verification = _mapping(raw, "verification")
    build = _mapping(raw, "build")
    deployment = _mapping(raw, "deployment")
    performance = _mapping(raw, "performance")
    canary = _mapping(raw, "canary")
    feedback = _mapping(raw, "feedback")

    gates: list[VerificationGateSpec] = []
    for gate_raw in _list(verification, "gates"):
        if not isinstance(gate_raw, dict):
            raise ValueError("verification gate must be a mapping")
        commands: list[CommandSpec] = []
        for command_raw in _list(gate_raw, "commands"):
            if not isinstance(command_raw, dict):
                raise ValueError("verification command must be a mapping")
            argv = command_raw.get("argv")
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and item for item in argv
            ):
                raise ValueError("verification command argv must be a non-empty string list")
            commands.append(
                CommandSpec(
                    argv=tuple(argv),
                    timeout_seconds=int(command_raw.get("timeout_seconds", 300)),
                )
            )
        gates.append(
            VerificationGateSpec(
                id=_string(gate_raw, "id"),
                commands=tuple(commands),
                required=bool(gate_raw.get("required", True)),
            )
        )

    stages: list[CanaryStage] = []
    for stage_raw in _list(canary, "stages"):
        if not isinstance(stage_raw, dict):
            raise ValueError("canary stage must be a mapping")
        stages.append(
            CanaryStage(
                weight_percent=int(stage_raw["weight"]),
                min_duration_seconds=int(stage_raw["min_duration_seconds"]),
                min_requests=int(stage_raw["min_requests"]),
            )
        )

    contract = TargetContract(
        schema_version=int(raw.get("schema_version", 0)),
        target_id=_string(target, "id"),
        default_branch=_string(target, "default_branch"),
        mutation=MutationPolicy(
            allowed_paths=tuple(_strings(mutation, "allowed_paths")),
            forbidden_paths=tuple(_strings(mutation, "forbidden_paths", default=[])),
            max_changed_files=int(mutation.get("max_changed_files", 50)),
            max_implementation_attempts=int(
                mutation.get("max_implementation_attempts", 3)
            ),
        ),
        verification=VerificationPlan(gates=tuple(gates)),
        build=DockerBuildSpec(
            dockerfile=str(build.get("dockerfile", "Dockerfile")),
            context=str(build.get("context", ".")),
            dependency_lock_files=tuple(
                _strings(build, "dependency_lock_files", default=[])
            ),
        ),
        deployment=DeploymentContract(
            container_port=int(deployment["container_port"]),
            health_path=_string(deployment, "health_path"),
            readiness_path=_string(deployment, "readiness_path"),
            metrics_path=_string(deployment, "metrics_path"),
        ),
        performance=PerformanceContract(
            k6_script=_string(performance, "k6_script"),
            timeout_seconds=int(performance.get("timeout_seconds", 600)),
        ),
        canary_stages=tuple(stages),
        feedback=FeedbackContract(
            attribution_header=str(
                feedback.get("attribution_header", "X-Autodev-Request-ID")
            )
        ),
    )
    return LoadedTargetContract(
        contract=contract,
        revision_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _mapping(root: dict[str, Any], key: str) -> dict[str, Any]:
    value = root.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _string(root: dict[str, Any], key: str) -> str:
    value = root.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _list(root: dict[str, Any], key: str) -> list[Any]:
    value = root.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _strings(
    root: dict[str, Any],
    key: str,
    *,
    default: list[str] | None = None,
) -> list[str]:
    value = root.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string list")
    return value
