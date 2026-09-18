from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from autonomous_development.ports.target_contract import (
    TargetBuildContract,
    TargetContract,
    TargetContractLoader,
    TargetDeploymentContract,
    TargetPerformanceContract,
)


class TomlTargetContractLoader(TargetContractLoader):
    def __init__(self, filename: str = "autonomous-development.toml") -> None:
        if not filename.strip() or "/" in filename or "\\" in filename:
            raise ValueError("contract filename must be a simple file name")
        self._filename = filename

    def load(self, repository_root: str) -> TargetContract:
        root = Path(repository_root).resolve(strict=True)
        document = tomllib.loads((root / self._filename).read_text(encoding="utf-8"))
        _exact_keys(
            document,
            {"schema_version", "target_id", "build", "deployment", "performance"},
            "target contract",
        )
        build = _table(document, "build")
        deployment = _table(document, "deployment")
        performance = _table(document, "performance")
        _exact_keys(build, {"dockerfile", "dependency_locks"}, "build")
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
        return TargetContract(
            schema_version=_int(document, "schema_version"),
            target_id=_string(document, "target_id"),
            build=TargetBuildContract(
                dockerfile=_string(build, "dockerfile"),
                dependency_locks=_string_tuple(build, "dependency_locks"),
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


def _string_tuple(document: dict[str, Any], key: str) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{key} must be a non-empty string array")
    return tuple(value)
