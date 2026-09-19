from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import create_engine

from autonomous_development.adapters.evidence.local import LocalEvidenceStore
from autonomous_development.adapters.postgres.releases import SqlReleasedVersionRepository
from autonomous_development.adapters.postgres.target_registry import (
    SqlObjectiveRepository,
    SqlTargetRepository,
)
from autonomous_development.adapters.traffic.file import AtomicFileTrafficDirector
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.enums import DeploymentState
from autonomous_development.domain.models import Deployment
from autonomous_development.ports.deployment import DeploymentRuntime, DeploymentSpec
from autonomous_development.runtime import bootstrap
from autonomous_development.runtime.bootstrap import bootstrap_runtime
from autonomous_development.runtime.config import RuntimeSettings


class FakeDeploymentProvider:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.specs: list[DeploymentSpec] = []

    def ensure(self, spec: DeploymentSpec) -> DeploymentRuntime:
        self.specs.append(spec)
        return DeploymentRuntime(
            deployment_id=spec.deployment_id,
            container_id="baseline-container",
            base_url="http://127.0.0.1:49152",
            evidence_ref="bootstrap:deployment",
        )


class FakeObserver:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def wait_ready(
        self,
        spec: DeploymentSpec,
        runtime: DeploymentRuntime,
        *,
        health_path: str,
        readiness_path: str,
        timeout_seconds: int,
    ) -> Deployment:
        assert runtime.deployment_id == spec.deployment_id
        assert health_path == "/health"
        assert readiness_path == "/ready"
        assert timeout_seconds == 30
        return Deployment(
            id=spec.deployment_id,
            target_id=spec.target_id,
            artifact_id=spec.artifact_id,
            environment="local-baseline",
            state=DeploymentState.READY,
            observed_at=datetime.now(UTC),
            observation_refs=("bootstrap:ready",),
        )


def _run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _target_repo(tmp_path: Path) -> Path:
    root = (tmp_path / "product").resolve()
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "load.js").write_text("export default function() {}\n", encoding="utf-8")
    (root / "autonomous-development.toml").write_text(
        """
schema_version = 1
target_id = "target-1"

[build]
dockerfile = "Dockerfile"
dependency_locks = ["uv.lock"]

[verification]
[[verification.gates]]
id = "tests"
command = ["python", "-m", "pytest"]
timeout_seconds = 60

[deployment]
container_port = 8000
health_path = "/health"
readiness_path = "/ready"
startup_timeout_seconds = 30

[performance]
script_path = "tests/load.js"
required_threshold_metrics = ["http_req_failed"]
timeout_seconds = 60

[canary]
max_candidate_error_rate = 0.02
max_error_rate_delta = 0.01
max_candidate_p95_latency_ms = 250.0
max_p95_latency_ratio = 1.25

[[canary.stages]]
weight_percent = 100
min_duration_seconds = 1
min_requests = 1
""".strip()
        + "\n",
        encoding="utf-8",
    )
    _run(root, "init", "-b", "main")
    _run(root, "add", ".")
    _run(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "baseline",
    )
    return root


def _settings(tmp_path: Path) -> RuntimeSettings:
    return RuntimeSettings(
        database_url=SecretStr(
            f"sqlite+pysqlite:///{tmp_path / 'autodev.sqlite3'}"
        ),
        dbos_system_database_url=SecretStr(
            f"sqlite:///{tmp_path / 'dbos.sqlite3'}"
        ),
        state_root=(tmp_path / "state").resolve(),
        telemetry_queries={"requests": "up"},
    )


def test_bootstrap_migrates_registers_and_observes_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _target_repo(tmp_path)
    settings = _settings(tmp_path)
    manifest = tmp_path / "bootstrap.json"
    manifest.write_text(
        json.dumps(
            {
                "target_id": "target-1",
                "repository": str(root),
                "default_branch": "main",
                "objective": {
                    "id": "objective-1",
                    "statement": "Keep the seeded product correct.",
                    "acceptance_criteria": ["seeded behavior remains correct"],
                    "primary_metrics": ["correctness"],
                    "reliability_constraints": ["no error regression"],
                    "performance_constraints": ["p95 remains bounded"],
                    "security_constraints": ["no secret exposure"],
                    "mutation_policy": {
                        "allowed_paths": ["src", "tests"],
                        "forbidden_paths": ["autonomous-development.toml"],
                        "max_changed_files": 5,
                        "max_implementation_attempts": 2,
                    },
                    "created_at": datetime.now(UTC).isoformat(),
                },
                "baseline_release": {
                    "id": "release-1",
                    "artifact_digest": "sha256:" + "c" * 64,
                    "deployment_id": "deployment-1",
                    "promoted_at": datetime.now(UTC).isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(bootstrap, "DockerDeploymentProvider", FakeDeploymentProvider)
    monkeypatch.setattr(bootstrap, "HttpDeploymentObserver", FakeObserver)

    first = bootstrap_runtime(settings, manifest)
    second = bootstrap_runtime(settings, manifest)

    assert first == second
    assert first["status"] == "bootstrapped"
    assert first["source_commit"] == _run(root, "rev-parse", "HEAD")
    assert str(first["target_contract_revision"]).startswith("sha256:")

    route = AtomicFileTrafficDirector(
        settings.traffic_state_root,
        LocalEvidenceStore(settings.evidence_root),
    ).read_current()
    assert route is not None
    assert route.target_id == "target-1"
    assert route.control_release_id == "release-1"
    assert route.candidate_deployment_id == "deployment-1"
    assert route.candidate_weight_percent == 0
    assert route.control_base_url == route.candidate_base_url

    engine = create_engine(settings.database_url.get_secret_value())
    try:
        targets = TargetRegistryService(
            SqlTargetRepository(engine),
            SqlObjectiveRepository(engine),
        )
        target = targets.get_target("target-1")
        assert target.target_contract_revision == first["target_contract_revision"]

        releases = ReleaseCatalogService(SqlReleasedVersionRepository(engine))
        serving = releases.serving("target-1")
        assert serving is not None
        assert serving.id == "release-1"
        assert serving.source_commit == first["source_commit"]
    finally:
        engine.dispose()
