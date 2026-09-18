from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from autonomous_development.ports.build import (
    SupplyChainEvidence,
    SupplyChainScanError,
    SupplyChainScanner,
)
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.process import CommandRequest, ProcessRunner


class SyftGrypeScanner(SupplyChainScanner):
    def __init__(
        self,
        runner: ProcessRunner,
        evidence: EvidenceStore,
        *,
        fail_on: str = "high",
        timeout_seconds: int = 900,
    ) -> None:
        if fail_on not in {"negligible", "low", "medium", "high", "critical"}:
            raise ValueError("unsupported Grype severity threshold")
        self._runner = runner
        self._evidence = evidence
        self._fail_on = fail_on
        self._timeout_seconds = timeout_seconds

    def scan(self, image_digest: str, *, candidate_id: str) -> SupplyChainEvidence:
        if not image_digest.startswith("sha256:"):
            raise SupplyChainScanError("scanner requires a sha256 image identity")
        with tempfile.TemporaryDirectory(prefix="autodev-scan-") as directory:
            root = Path(directory)
            sbom_path = root / "sbom.cdx.json"
            grype_path = root / "grype.json"

            syft = self._runner.run(
                CommandRequest(
                    command=(
                        "syft",
                        "scan",
                        image_digest,
                        "-o",
                        f"cyclonedx-json={sbom_path}",
                    ),
                    cwd=root,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            if syft.returncode != 0 or not sbom_path.exists():
                raise SupplyChainScanError(
                    f"Syft failed with exit {syft.returncode}: {syft.stderr}"
                )
            sbom_bytes = sbom_path.read_bytes()
            sbom = _json_object(sbom_bytes, "Syft SBOM")
            sbom_digest = "sha256:" + hashlib.sha256(sbom_bytes).hexdigest()
            sbom_ref = self._evidence.write_json(
                "sbom",
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "image_digest": image_digest,
                    "sbom_digest": sbom_digest,
                    "document": sbom,
                },
            )

            grype = self._runner.run(
                CommandRequest(
                    command=(
                        "grype",
                        image_digest,
                        "-o",
                        "json",
                        "--file",
                        str(grype_path),
                        "--fail-on",
                        self._fail_on,
                    ),
                    cwd=root,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            if grype.returncode not in {0, 2} or not grype_path.exists():
                raise SupplyChainScanError(
                    f"Grype failed with exit {grype.returncode}: {grype.stderr}"
                )
            report = _json_object(grype_path.read_bytes(), "Grype report")
            passed = grype.returncode == 0
            scan_ref = self._evidence.write_json(
                "vulnerability-scan",
                candidate_id,
                {
                    "candidate_id": candidate_id,
                    "image_digest": image_digest,
                    "fail_on": self._fail_on,
                    "passed": passed,
                    "sbom_ref": sbom_ref,
                    "report": report,
                },
            )
            return SupplyChainEvidence(
                sbom_digest=sbom_digest,
                sbom_ref=sbom_ref,
                vulnerability_scan_ref=scan_ref,
                passed=passed,
            )


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise SupplyChainScanError(f"{label} must be a JSON object")
    return parsed
