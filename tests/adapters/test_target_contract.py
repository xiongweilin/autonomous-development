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

[verification]

[[verification.gates]]
id = "static"
command = ["uv", "run", "ruff", "check", "."]
timeout_seconds = 120

[[verification.gates]]
id = "tests"
command = ["uv", "run", "pytest", "-q"]
timeout_seconds = 600

[deployment]
container_port = 8000
health_path = "/health"
readiness_path = "/ready"
startup_timeout_seconds = 30

[performance]
script_path = "tests/performance/smoke.js"
required_threshold_metrics = ["http_req_failed", "http_req_duration"]
timeout_seconds = 120

[canary]
max_candidate_error_rate = 0.02
max_error_rate_delta = 0.01
max_candidate_p95_latency_ms = 250.0
max_p95_latency_ratio = 1.25

[[canary.stages]]
weight_percent = 10
min_duration_seconds = 60
min_requests = 100

[[canary.stages]]
weight_percent = 50
min_duration_seconds = 120
min_requests = 200

[[canary.stages]]
weight_percent = 100
min_duration_seconds = 180
min_requests = 300
"""
        + extra,
        encoding="utf-8",
    )


def test_loads_strict_target_contract(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    contract = TomlTargetContractLoader().load(str(tmp_path))
    assert contract.target_id == "sample-agent"
    assert contract.build.dependency_locks == ("uv.lock",)
    assert tuple(gate.id for gate in contract.verification.gates) == ("static", "tests")
    assert contract.verification.gates[0].command == (
        "uv",
        "run",
        "ruff",
        "check",
        ".",
    )
    assert contract.mandatory_gates == ("static", "tests", "performance")
    assert contract.deployment.container_port == 8000
    assert contract.performance.required_threshold_metrics == (
        "http_req_failed",
        "http_req_duration",
    )
    assert tuple(stage.weight_percent for stage in contract.canary.stages) == (10, 50, 100)
    assert contract.canary.max_p95_latency_ratio == 1.25


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


def test_canary_contract_requires_final_100_percent_stage(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    path = tmp_path / "autonomous-development.toml"
    content = path.read_text(encoding="utf-8").replace(
        "weight_percent = 100",
        "weight_percent = 90",
    )
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="end at 100"):
        TomlTargetContractLoader().load(str(tmp_path))


def test_verification_gate_ids_must_be_unique(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    path = tmp_path / "autonomous-development.toml"
    content = path.read_text(encoding="utf-8").replace(
        'id = "tests"',
        'id = "static"',
    )
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        TomlTargetContractLoader().load(str(tmp_path))


def test_performance_gate_id_is_reserved(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    path = tmp_path / "autonomous-development.toml"
    content = path.read_text(encoding="utf-8").replace(
        'id = "static"',
        'id = "performance"',
        1,
    )
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        TomlTargetContractLoader().load(str(tmp_path))
