from pathlib import Path

import pytest

from autonomous_development.adapters.docker_cli import DockerDeploymentProvider
from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.ports.deployment import DeploymentProviderError, DeploymentSpec
from autonomous_development.ports.process import CommandRequest, CommandResult


class FakeDockerRunner:
    def __init__(self, *, existing: bool = False, wrong_image: bool = False) -> None:
        self.existing = existing
        self.wrong_image = wrong_image
        self.created = existing
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        command = request.command
        if command[:2] == ("docker", "inspect"):
            if not self.created:
                return CommandResult(1, "", "error: no such object")
            image = "sha256:" + ("f" if self.wrong_image else "a") * 64
            return CommandResult(
                0,
                f"container-id|{image}|deploy-1|target-1\n",
                "",
            )
        if command[:2] == ("docker", "run"):
            self.created = True
            return CommandResult(0, "container-id\n", "")
        if command[:2] == ("docker", "port"):
            return CommandResult(0, "127.0.0.1:49155\n", "")
        if command[:3] == ("docker", "rm", "--force"):
            self.created = False
            return CommandResult(0, "container-id\n", "")
        raise AssertionError(f"unexpected command: {command}")


def _spec() -> DeploymentSpec:
    return DeploymentSpec(
        deployment_id="deploy-1",
        target_id="target-1",
        artifact_id="artifact-1",
        image_digest="sha256:" + "a" * 64,
        container_port=8000,
    )


def test_ensure_creates_then_reconciles_loopback_runtime(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    provider = DockerDeploymentProvider(
        runner,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    runtime = provider.ensure(_spec())
    assert runtime.container_id == "container-id"
    assert runtime.base_url == "http://127.0.0.1:49155"
    assert sum(1 for request in runner.requests if request.command[:2] == ("docker", "run")) == 1


def test_repeated_ensure_reuses_matching_deployment(tmp_path: Path) -> None:
    runner = FakeDockerRunner(existing=True)
    provider = DockerDeploymentProvider(
        runner,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    provider.ensure(_spec())
    assert not any(request.command[:2] == ("docker", "run") for request in runner.requests)


def test_identity_conflict_fails_closed(tmp_path: Path) -> None:
    provider = DockerDeploymentProvider(
        FakeDockerRunner(existing=True, wrong_image=True),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    with pytest.raises(DeploymentProviderError, match="conflicts"):
        provider.ensure(_spec())
