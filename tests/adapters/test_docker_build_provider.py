from pathlib import Path

import pytest

from autonomous_development.adapters.docker_cli import DockerBuildProvider
from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.ports.build import BuildProviderError, BuildRequest
from autonomous_development.ports.process import (
    CommandRequest,
    CommandResult,
    CommandTimedOut,
)


class FakeDockerRunner:
    def __init__(
        self,
        *,
        build_returncode: int = 0,
        timeout_after_build: bool = False,
        wrong_identity: bool = False,
    ) -> None:
        self.build_returncode = build_returncode
        self.timeout_after_build = timeout_after_build
        self.wrong_identity = wrong_identity
        self.created = False
        self.build_calls = 0
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        command = request.command
        if command[:3] == ("docker", "image", "inspect"):
            if not self.created:
                return CommandResult(1, "", "Error: No such image")
            candidate = "other-candidate" if self.wrong_identity else "candidate-1"
            source_tree = "f" * 40 if self.wrong_identity else "c" * 40
            return CommandResult(
                0,
                f"sha256:{'d' * 64}|{candidate}|{source_tree}\n",
                "",
            )

        if command[:2] == ("docker", "build"):
            self.build_calls += 1
            if "--iidfile" in command and self.build_returncode == 0:
                index = command.index("--iidfile")
                Path(command[index + 1]).write_text(
                    "sha256:" + "d" * 64,
                    encoding="utf-8",
                )
                self.created = True
            if self.timeout_after_build and self.build_calls == 1:
                raise CommandTimedOut("lost acknowledgement after docker build")
            return CommandResult(
                returncode=self.build_returncode,
                stdout="build output",
                stderr="" if self.build_returncode == 0 else "build failed",
            )

        raise AssertionError(f"unexpected command: {command}")


def build_request(tmp_path: Path) -> BuildRequest:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    return BuildRequest(
        candidate_id="candidate-1",
        source_tree_hash="c" * 40,
        context_dir=tmp_path.resolve(),
        dockerfile=dockerfile.resolve(),
    )


def provider(tmp_path: Path, runner: FakeDockerRunner) -> DockerBuildProvider:
    return DockerBuildProvider(
        runner,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )


def test_docker_build_uses_content_addressed_iid_and_identity_labels(tmp_path: Path) -> None:
    runner = FakeDockerRunner()
    built = provider(tmp_path, runner).build(build_request(tmp_path))

    assert built.image_digest == "sha256:" + "d" * 64
    assert runner.build_calls == 1
    build_command = next(
        request.command
        for request in runner.requests
        if request.command[:2] == ("docker", "build")
    )
    assert "--tag" in build_command
    assert "autodev.candidate_id=candidate-1" in build_command
    assert f"autodev.source_tree={'c' * 40}" in build_command
    assert built.evidence_ref.startswith("file:")


def test_lost_build_acknowledgement_reconciles_without_second_build(tmp_path: Path) -> None:
    runner = FakeDockerRunner(timeout_after_build=True)
    build = provider(tmp_path, runner)
    request = build_request(tmp_path)

    with pytest.raises(CommandTimedOut, match="lost acknowledgement"):
        build.build(request)

    recovered = build.build(request)
    assert recovered.image_digest == "sha256:" + "d" * 64
    assert runner.build_calls == 1


def test_existing_tag_with_wrong_candidate_identity_fails_closed(tmp_path: Path) -> None:
    runner = FakeDockerRunner(wrong_identity=True)
    runner.created = True

    with pytest.raises(BuildProviderError, match="different candidate identity"):
        provider(tmp_path, runner).build(build_request(tmp_path))

    assert runner.build_calls == 0


def test_docker_build_failure_is_not_artifact_success(tmp_path: Path) -> None:
    runner = FakeDockerRunner(build_returncode=1)
    with pytest.raises(BuildProviderError, match="failed"):
        provider(tmp_path, runner).build(build_request(tmp_path))
