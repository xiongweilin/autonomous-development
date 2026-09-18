from pathlib import Path

import pytest

from autonomous_development.adapters.target_contract import TomlTargetContractLoader


def _write_contract(root: Path, *, extra: str = "") -> None:
    (root / "autonomous-development.toml").write_text(
        """
schema_version = 1
target_id = "sample-agent"

[build]
dockerfile = "Dockerfile"
dependency_locks = ["uv.lock"]

[deployment]
container_port = 8000
health_path = "/health"
readiness_path = "/ready"
startup_timeout_seconds = 30

[performance]
script_path = "tests/performance/smoke.js"
required_threshold_metrics = ["http_req_failed", "http_req_duration"]
timeout_seconds = 120
"""
        + extra,
        encoding="utf-8",
    )


def test_loads_strict_target_contract(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = TomlTargetContractLoader().load(str(tmp_path))
    assert contract.target_id == "sample-agent"
    assert contract.build.dependency_locks == ("uv.lock",)
    assert contract.deployment.container_port == 8000
    assert contract.performance.required_threshold_metrics == (
        "http_req_failed",
        "http_req_duration",
    )


def test_unknown_contract_key_is_rejected(tmp_path: Path) -> None:
    _write_contract(tmp_path, extra='\nunknown = "value"\n')
    with pytest.raises(ValueError, match="keys mismatch"):
        TomlTargetContractLoader().load(str(tmp_path))


def test_contract_rejects_path_traversal(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    path = tmp_path / "autonomous-development.toml"
    content = path.read_text(encoding="utf-8").replace(
        'dockerfile = "Dockerfile"',
        'dockerfile = "../Dockerfile"',
    )
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="relative path"):
        TomlTargetContractLoader().load(str(tmp_path))
