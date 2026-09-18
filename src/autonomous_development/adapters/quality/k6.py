from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision, VerificationCheck
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.quality import PerformanceGateFactory, QualityGate
from autonomous_development.ports.process import (
    CommandRequest,
    CommandTimedOut,
    CommandUnavailable,
    ProcessRunner,
)


class K6PerformanceGate:
    def __init__(
        self,
        *,
        runner: ProcessRunner,
        evidence: EvidenceStore,
        base_url: str,
        script_path: str,
        required_threshold_metrics: tuple[str, ...],
        timeout_seconds: int,
        gate_id: str = "performance",
    ) -> None:
        if not required_threshold_metrics:
            raise ValueError("k6 gate requires threshold metrics")
        _require_loopback(base_url)
        self._runner = runner
        self._evidence = evidence
        self._base_url = base_url
        self._script_path = script_path
        self._required_threshold_metrics = required_threshold_metrics
        self._timeout_seconds = timeout_seconds
        self._gate_id = gate_id

    @property
    def gate_id(self) -> str:
        return self._gate_id

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck:
        root = Path(candidate.worktree_path).resolve()
        script = (root / self._script_path).resolve(strict=True)
        if not script.is_relative_to(root):
            raise ValueError("k6 script must be inside the candidate worktree")

        started = datetime.now(UTC)
        status = VerificationStatus.BLOCKED
        payload: dict[str, object] = {
            "candidate_id": candidate.id,
            "gate": self._gate_id,
            "required_threshold_metrics": list(self._required_threshold_metrics),
        }
        returncode = -1
        with tempfile.TemporaryDirectory(prefix="autodev-k6-") as directory:
            summary_path = Path(directory) / "summary.json"
            try:
                result = self._runner.run(
                    CommandRequest(
                        command=(
                            "k6",
                            "run",
                            "--summary-export",
                            str(summary_path),
                            str(script),
                        ),
                        cwd=root,
                        timeout_seconds=self._timeout_seconds,
                        environment={"AUTODEV_BASE_URL": self._base_url},
                    )
                )
                returncode = result.returncode
                payload.update(
                    {
                        "returncode": result.returncode,
                        "stdout_sha256": _digest(result.stdout),
                        "stderr_sha256": _digest(result.stderr),
                    }
                )
                if summary_path.exists():
                    summary = _json_object(summary_path.read_bytes())
                    missing = _missing_thresholds(
                        summary,
                        self._required_threshold_metrics,
                    )
                    payload["summary"] = summary
                    payload["missing_threshold_metrics"] = list(missing)
                    if not missing:
                        status = (
                            VerificationStatus.PASSED
                            if result.returncode == 0
                            else VerificationStatus.FAILED
                        )
                else:
                    payload["summary_missing"] = True
            except (CommandUnavailable, CommandTimedOut, ValueError) as exc:
                payload.update({"error_type": type(exc).__name__, "error": str(exc)})

        ended = datetime.now(UTC)
        payload.update({"status": status.value, "ended_at": ended.isoformat()})
        evidence_ref = self._evidence.write_json(
            "performance",
            f"{candidate.id}-{self._gate_id}",
            payload,
        )
        return VerificationCheck(
            id=f"{candidate.id}:{self._gate_id}",
            gate=self._gate_id,
            status=status,
            started_at=started,
            ended_at=ended,
            evidence_refs=(evidence_ref,),
            measurements={"returncode": returncode},
        )


def _json_object(payload: bytes) -> dict[str, Any]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("k6 summary must be a JSON object")
    return parsed


def _missing_thresholds(
    summary: dict[str, Any],
    required: tuple[str, ...],
) -> tuple[str, ...]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        return required
    missing: list[str] = []
    for name in required:
        metric = metrics.get(name)
        if not isinstance(metric, dict):
            missing.append(name)
            continue
        thresholds = metric.get("thresholds")
        if not isinstance(thresholds, dict) or not thresholds:
            missing.append(name)
    return tuple(missing)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 performance gate is restricted to local HTTP loopback")



class K6PerformanceGateFactory(PerformanceGateFactory):
    def __init__(
        self,
        *,
        runner: ProcessRunner,
        evidence: EvidenceStore,
        gate_id: str = "performance",
    ) -> None:
        if not gate_id.strip():
            raise ValueError("performance gate id must be non-empty")
        self._runner = runner
        self._evidence = evidence
        self._gate_id = gate_id

    def create(
        self,
        *,
        base_url: str,
        script_path: str,
        required_threshold_metrics: tuple[str, ...],
        timeout_seconds: int,
    ) -> QualityGate:
        return K6PerformanceGate(
            runner=self._runner,
            evidence=self._evidence,
            base_url=base_url,
            script_path=script_path,
            required_threshold_metrics=required_threshold_metrics,
            timeout_seconds=timeout_seconds,
            gate_id=self._gate_id,
        )
