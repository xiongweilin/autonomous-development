from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from autonomous_development.ports.build import (
    BuildProvider,
    BuildProviderError,
    BuildRequest,
    BuiltImage,
)
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.process import CommandRequest, ProcessRunner

_CANDIDATE_LABEL = "autodev.candidate_id"
_SOURCE_TREE_LABEL = "autodev.source_tree"


class DockerBuildProvider(BuildProvider):
    def __init__(
        self,
        runner: ProcessRunner,
        evidence: EvidenceStore,
        *,
        timeout_seconds: int = 1800,
    ) -> None:
        self._runner = runner
        self._evidence = evidence
        self._timeout_seconds = timeout_seconds

    def build(self, request: BuildRequest) -> BuiltImage:
        tag = _build_tag(request)
        reconciled = self._inspect(tag, request)
        if reconciled is not None:
            return BuiltImage(
                image_digest=reconciled,
                evidence_ref=self._write_evidence(
                    request,
                    tag=tag,
                    image_digest=reconciled,
                    reconciled=True,
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            )

        descriptor, iid_name = tempfile.mkstemp(prefix="autodev-iid-", suffix=".txt")
        os.close(descriptor)
        iidfile = Path(iid_name)
        try:
            result = self._runner.run(
                CommandRequest(
                    command=(
                        "docker",
                        "build",
                        "--pull",
                        "--iidfile",
                        str(iidfile),
                        "--tag",
                        tag,
                        "--label",
                        f"{_CANDIDATE_LABEL}={request.candidate_id}",
                        "--label",
                        f"{_SOURCE_TREE_LABEL}={request.source_tree_hash}",
                        "-f",
                        str(request.dockerfile),
                        str(request.context_dir),
                    ),
                    cwd=request.context_dir,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            if result.returncode != 0:
                evidence_ref = self._write_evidence(
                    request,
                    tag=tag,
                    image_digest=None,
                    reconciled=False,
                    returncode=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                raise BuildProviderError(
                    f"docker build failed with exit {result.returncode}: {evidence_ref}"
                )
            if not iidfile.exists():
                raise BuildProviderError("docker build did not produce an iidfile")
            iid_digest = iidfile.read_text(encoding="utf-8").strip()
            if not iid_digest.startswith("sha256:"):
                raise BuildProviderError("docker iidfile did not contain a sha256 image id")

            observed = self._inspect(tag, request)
            if observed is None:
                raise BuildProviderError(
                    "docker build succeeded but tagged image cannot be reconciled"
                )
            if observed != iid_digest:
                raise BuildProviderError(
                    "docker tagged image digest does not match build iidfile"
                )
            evidence_ref = self._write_evidence(
                request,
                tag=tag,
                image_digest=observed,
                reconciled=False,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            return BuiltImage(image_digest=observed, evidence_ref=evidence_ref)
        finally:
            iidfile.unlink(missing_ok=True)

    def _inspect(self, tag: str, request: BuildRequest) -> str | None:
        result = self._runner.run(
            CommandRequest(
                command=(
                    "docker",
                    "image",
                    "inspect",
                    tag,
                    "--format",
                    (
                        '{{.Id}}|{{index .Config.Labels "'
                        + _CANDIDATE_LABEL
                        + '"}}|{{index .Config.Labels "'
                        + _SOURCE_TREE_LABEL
                        + '"}}'
                    ),
                ),
                cwd=request.context_dir,
                timeout_seconds=min(self._timeout_seconds, 60),
            )
        )
        if result.returncode != 0:
            if "No such image" in result.stderr or "No such object" in result.stderr:
                return None
            raise BuildProviderError(
                "docker image reconciliation failed before build: "
                f"{_digest_text(result.stderr)}"
            )
        parts = result.stdout.strip().split("|")
        if len(parts) != 3:
            raise BuildProviderError("docker image reconciliation returned malformed identity")
        digest, candidate_id, source_tree = parts
        if not digest.startswith("sha256:"):
            raise BuildProviderError("docker image reconciliation returned invalid digest")
        if candidate_id != request.candidate_id or source_tree != request.source_tree_hash:
            raise BuildProviderError(
                "deterministic build tag is bound to a different candidate identity"
            )
        return digest

    def _write_evidence(
        self,
        request: BuildRequest,
        *,
        tag: str,
        image_digest: str | None,
        reconciled: bool,
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> str:
        return self._evidence.write_json(
            "build",
            request.candidate_id,
            {
                "candidate_id": request.candidate_id,
                "source_tree_hash": request.source_tree_hash,
                "image_digest": image_digest,
                "reconciled": reconciled,
                "tag_sha256": _digest_text(tag),
                "returncode": returncode,
                "stdout_bytes": len(stdout.encode()),
                "stdout_sha256": _digest_text(stdout),
                "stderr_bytes": len(stderr.encode("utf-8")),
                "stderr_sha256": _digest_text(stderr),
                "dockerfile": str(request.dockerfile),
            },
        )


def _build_tag(request: BuildRequest) -> str:
    digest = hashlib.sha256(
        f"{request.candidate_id}\0{request.source_tree_hash}".encode()
    ).hexdigest()
    return f"autodev-build:{digest[:32]}"


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
