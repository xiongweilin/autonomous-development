from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from autonomous_development.domain.build import DockerBuildSpec
from autonomous_development.domain.commands import CommandSpec
from autonomous_development.domain.models import BuildArtifact, CandidateRevision
from autonomous_development.ports.build import BuildProvider, BuildProviderError
from autonomous_development.ports.command import CommandRunner
from autonomous_development.ports.evidence import EvidenceStore


class DockerBuildProvider(BuildProvider):
    def __init__(
        self,
        runner: CommandRunner,
        evidence: EvidenceStore,
        *,
        docker: str = "docker",
        syft: str = "syft",
        grype: str = "grype",
    ) -> None:
        self._runner = runner
        self._evidence = evidence
        self._docker = docker
        self._syft = syft
        self._grype = grype

    def build(
        self,
        candidate: CandidateRevision,
        *,
        workspace: Path,
        spec: DockerBuildSpec,
    ) -> BuildArtifact:
        root = workspace.resolve()
        dockerfile = _inside(root, spec.dockerfile)
        context = _inside(root, spec.context)
        if not dockerfile.is_file():
            raise BuildProviderError(f"Dockerfile does not exist: {dockerfile}")
        if not context.is_dir():
            raise BuildProviderError(f"Docker build context does not exist: {context}")

        build_definition_digest = _sha256_file(dockerfile)
        dependency_lock_digest = _combined_digest(
            tuple(_inside(root, item) for item in spec.dependency_lock_files)
        )
        image_tag = f"autodev-candidate:{_safe_tag(candidate.id)}"

        with tempfile.TemporaryDirectory(prefix="autodev-build-") as temp:
            temp_root = Path(temp)
            iidfile = temp_root / "image-id.txt"
            sbom_file = temp_root / "sbom.spdx.json"

            build_result = self._runner.run(
                CommandSpec(
                    argv=(
                        self._docker,
                        "build",
                        "--iidfile",
                        str(iidfile),
                        "-f",
                        str(dockerfile),
                        "-t",
                        image_tag,
                        str(context),
                    ),
                    timeout_seconds=1800,
                ),
                default_cwd=root,
            )
            build_ref = self._record_result(candidate.id, "docker-build", build_result)
            if not build_result.succeeded or not iidfile.is_file():
                raise BuildProviderError(
                    f"Docker build failed; evidence={build_ref}"
                )
            image_digest = iidfile.read_text(encoding="utf-8").strip()
            if not image_digest.startswith("sha256:"):
                raise BuildProviderError("Docker build did not return a content-addressed image id")

            syft_result = self._runner.run(
                CommandSpec(
                    argv=(
                        self._syft,
                        image_digest,
                        "-o",
                        f"spdx-json={sbom_file}",
                    ),
                    timeout_seconds=600,
                ),
                default_cwd=root,
            )
            syft_ref = self._record_result(candidate.id, "syft", syft_result)
            if not syft_result.succeeded or not sbom_file.is_file():
                raise BuildProviderError(f"SBOM generation failed; evidence={syft_ref}")
            sbom_digest = _sha256_file(sbom_file)
            self._evidence.put_text(
                namespace=f"build-{candidate.id}",
                content=sbom_file.read_text(encoding="utf-8"),
                media_type="application/spdx+json",
            )

            grype_result = self._runner.run(
                CommandSpec(
                    argv=(self._grype, image_digest, "--fail-on", "high", "-o", "json"),
                    timeout_seconds=600,
                ),
                default_cwd=root,
            )
            vulnerability_scan_ref = self._record_result(
                candidate.id,
                "grype",
                grype_result,
            )
            if not grype_result.succeeded:
                raise BuildProviderError(
                    f"image vulnerability gate failed; evidence={vulnerability_scan_ref}"
                )

        return BuildArtifact(
            id=f"{candidate.id}-build",
            candidate_id=candidate.id,
            image_digest=image_digest,
            source_tree_hash=candidate.tree_hash,
            build_definition_digest=build_definition_digest,
            dependency_lock_digest=dependency_lock_digest,
            sbom_digest=sbom_digest,
            vulnerability_scan_ref=vulnerability_scan_ref,
        )

    def _record_result(self, candidate_id: str, kind: str, result: object) -> str:
        record = self._evidence.put_text(
            namespace=f"build-{candidate_id}",
            content=repr(result),
            media_type="text/plain; charset=utf-8",
        )
        return record.ref


def _inside(root: Path, relative: str) -> Path:
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise BuildProviderError(f"path escapes build workspace: {relative}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if not path.is_file():
            raise BuildProviderError(f"dependency lock file does not exist: {path}")
        digest.update(str(path.name).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_tag(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() or character in "_.-" else "-"
        for character in value
    ).strip(".-")
    return normalized[:100] or "candidate"
