from pathlib import Path

import httpx
import pytest

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.http_observer import HttpDeploymentObserver
from autonomous_development.domain.enums import DeploymentState
from autonomous_development.ports.deployment import DeploymentRuntime, DeploymentSpec


def _spec() -> DeploymentSpec:
    return DeploymentSpec(
        deployment_id="deploy-1",
        target_id="target-1",
        artifact_id="artifact-1",
        image_digest="sha256:" + "a" * 64,
        container_port=8000,
    )


def test_observer_requires_independent_health_and_readiness(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request)

    observer = HttpDeploymentObserver(
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
        interval_seconds=0.001,
        transport=httpx.MockTransport(handler),
    )
    deployment = observer.wait_ready(
        _spec(),
        DeploymentRuntime(
            deployment_id="deploy-1",
            container_id="container-1",
            base_url="http://127.0.0.1:49155",
            evidence_ref="effect:1",
        ),
        health_path="/health",
        readiness_path="/ready",
        timeout_seconds=1,
    )
    assert deployment.state is DeploymentState.READY
    assert deployment.observation_refs
    assert calls == 4


def test_observer_rejects_non_loopback_runtime(tmp_path: Path) -> None:
    observer = HttpDeploymentObserver(
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
    )
    with pytest.raises(ValueError, match="loopback"):
        observer.wait_ready(
            _spec(),
            DeploymentRuntime(
                deployment_id="deploy-1",
                container_id="container-1",
                base_url="http://example.com",
                evidence_ref="effect:1",
            ),
            health_path="/health",
            readiness_path="/ready",
            timeout_seconds=1,
        )
