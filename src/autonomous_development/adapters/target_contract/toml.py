from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

from autonomous_development.domain.models import CanaryStage
from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetCanaryContract,
    TargetContract,
    TargetContractLoader,
    TargetDeploymentContract,
    TargetPerformanceContract,
    TargetVerificationContract,
    TargetVerificationGateContract,
)


class TomlTargetContractLoader(TargetContractLoader):
    def __init__(self, filename: str = "autonomous-development.toml") -> None:
        if not filename.strip() or "/" in filename or "\\" in filename:
            raise ValueError("contract filename must be a simple file name")
        self._filename = filename

    def load(self, repository_root: str) -> TargetContract:
        root = Path(repository_root).resolve(strict=True)
        payload = (root / self._filename).read_bytes()
        document = tomllib.loads(payload.decode("utf-8"))
        _exact_keys(
            document,
            {
                "schema_version",
                "target_id",
                "build",
                "verification",
                "deployment",
                "performance",
                "canary",
            },
            "target contract",
        )
        build = _table(document, "build")
        verification = _table(document, "verification")
        deployment = _table(document, "deployment")
        performance = _table(document, "performance")
        canary = _table(document, "canary")
        _exact_keys(build, {"dockerfile", "dependency_locks"}, "build")
        _exact_keys(verification, {"gates"}, "verification")
        _exact_keys(
            deployment,
            {"container_port", "health_path", "readiness_path", "startup_timeout_seconds"},
            "deployment",
        )
        _exact_keys(
            performance,
            {"script_path", "required_threshold_metrics", "timeout_seconds"},
            "performance",
        )
        _exact_keys(
            canary,
            {
                "stages",
                "max_candidate_error_rate",
                "max_error_rate_delta",
                "max_candidate_p95_latency_ms",
                "max_p95_latency_ratio",
            },
            "canary",
        )
        return TargetContract(
            schema_version=_int(document, "schema_version"),
            target_id=_string(document, "target_id"),
            build=TargetBuildContract(
                dockerfile=_string(build, "dockerfile"),
                dependency_locks=_string_tuple(build, "dependency_locks"),
            ),
            verification=TargetVerificationContract(
                gates=_verification_gates(verification),
            ),
            deployment=TargetDeploymentContract(
                container_port=_int(deployment, "container_port"),
                health_path=_string(deployment, "health_path"),
                readiness_path=_string(deployment, "readiness_path"),
                startup_timeout_seconds=_int(deployment, "startup_timeout_seconds"),
            ),
            performance=TargetPerformanceContract(
                script_path=_string(performance, "script_path"),
                required_threshold_metrics=_string_tuple(
                    performance,
                    "required_threshold_metrics",
                ),
                timeout_seconds=_int(performance, "timeout_seconds"),
            ),
            canary=TargetCanaryContract(
                stages=_stages(canary),
                max_candidate_error_rate=_float(canary, "max_candidate_error_rate"),
                max_error_rate_delta=_float(canary, "max_error_rate_delta"),
                max_candidate_p95_latency_ms=_float(
                    canary,
                    "max_candidate_p95_latency_ms",
                ),
                max_p95_latency_ratio=_float(canary, "max_p95_latency_ratio"),
            ),
            revision="sha256:" + hashlib.sha256(payload).hexdigest(),
        )


def _table(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a TOML table")
    return {str(item_key): item for item_key, item in value.items()}


def _exact_keys(document: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(document)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(f"{label} keys mismatch; missing={missing}, unknown={unknown}")


def _string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _int(document: dict[str, Any], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _float(document: dict[str, Any], key: str) -> float:
    value = document.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _string_tuple(document: dict[str, Any], key: str) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a non-empty string array")
    return tuple(value)


def _verification_gates(
    document: dict[str, Any],
) -> tuple[TargetVerificationGateContract, ...]:
    raw = document.get("gates")
    if not isinstance(raw, list) or not raw:
        raise ValueError("verification gates must be a non-empty table array")
    gates: list[TargetVerificationGateContract] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"verification gate {index} must be a table")
        normalized = {str(key): value for key, value in item.items()}
        _exact_keys(
            normalized,
            {"id", "command", "timeout_seconds"},
            f"verification gate {index}",
        )
        gates.append(
            TargetVerificationGateContract(
                id=_string(normalized, "id"),
                command=_string_tuple(normalized, "command"),
                timeout_seconds=_int(normalized, "timeout_seconds"),
            )
        )
    return tuple(gates)


def _stages(document: dict[str, Any]) -> tuple[CanaryStage, ...]:
    raw = document.get("stages")
    if not isinstance(raw, list) or not raw:
        raise ValueError("canary stages must be a non-empty table array")
    stages: list[CanaryStage] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"canary stage {index} must be a table")
        normalized = {str(key): value for key, value in item.items()}
        _exact_keys(
            normalized,
            {"weight_percent", "min_duration_seconds", "min_requests"},
            f"canary stage {index}",
        )
        stages.append(
            CanaryStage(
                weight_percent=_int(normalized, "weight_percent"),
                min_duration_seconds=_int(normalized, "min_duration_seconds"),
                min_requests=_int(normalized, "min_requests"),
            )
        )
    return tuple(stages)
