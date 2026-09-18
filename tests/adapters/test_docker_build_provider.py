from pathlib import Path

import pytest

from autonomous_development.adapters.docker_cli import DockerBuildProvider
from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.ports.build import BuildProviderError, BuildRequest
from autonomous_development.ports.process import CommandRequest, CommandResult


class FakeDockerRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        if "--iidfile" in request.command and self.returncode == 0:
            index = request.command.index("--iidfile")
            Path(request.command[index + 1]).write_text(
                "sha256:" + "d" * 64,
                encoding="utf-8",
            )
        return CommandResult(
            returncode=self.returncode,
            stdout="build output",
            stderr="" if self.returncode == 0 else "build failed",
        )


def build_request(tmp_path: Path) -> BuildRequest:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    return BuildRequest(
        candidate_id="candidate-1",
        context_dir=tmp_path.resolve(),
        dockerfile=dockerfile.resolve(),
    )


def test_docker_build_uses_content_addressed_iid(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    provider = DockerBuildProvider(
        runner,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    built = provider.build(build_request(tmp_path))
    assert built.image_digest == "sha256:" + "d" * 64
    assert runner.requests[0].command[:2] == ("docker", "build")
    assert built.evidence_ref.startswith("file:")


def test_docker_build_failure_is_not_artifact_success(tmp_path: Path) -> None:
    provider = DockerBuildProvider(
        FakeDockerRunner(returncode=1),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    with pytest.raises(BuildProviderError, match="failed"):
        provider.build(build_request(tmp_path))
