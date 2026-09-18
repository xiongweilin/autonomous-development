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
        descriptor, iid_name = tempfile.mkstemp(prefix="autodev-iid-", suffix=".txt")
        os.close(descriptor)
        iidfile = Path(iid_name)
        try:
            result = self._runner.run(
                CommandRequest(
                    command=(
                        "docker",
                        "build",
                        "--iidfile",
                        str(iidfile),
                        "-f",
                        str(request.dockerfile),
                        str(request.context_dir),
                    ),
                    cwd=request.context_dir,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            evidence_ref = self._evidence.write_json(
                "build",
                request.candidate_id,
                {
                    "candidate_id": request.candidate_id,
                    "returncode": result.returncode,
                    "stdout_bytes": len(result.stdout.encode("utf-8")),
                    "stdout_sha256": _digest_text(result.stdout),
                    "stderr_bytes": len(result.stderr.encode("utf-8")),
                    "stderr_sha256": _digest_text(result.stderr),
                    "dockerfile": str(request.dockerfile),
                },
            )
            if result.returncode != 0:
                raise BuildProviderError(
                    f"docker build failed with exit {result.returncode}: {evidence_ref}"
                )
            if not iidfile.exists():
                raise BuildProviderError("docker build did not produce an iidfile")
            image_digest = iidfile.read_text(encoding="utf-8").strip()
            if not image_digest.startswith("sha256:"):
                raise BuildProviderError("docker iidfile did not contain a sha256 image id")
            return BuiltImage(image_digest=image_digest, evidence_ref=evidence_ref)
        finally:
            iidfile.unlink(missing_ok=True)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
