from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision, VerificationCheck
from autonomous_development.ports.evidence import EvidenceStore
from autonomous_development.ports.process import (
    CommandRequest,
    CommandTimedOut,
    CommandUnavailable,
    ProcessRunner,
)


class CommandQualityGate:
    def __init__(
        self,
        *,
        gate_id: str,
        command: tuple[str, ...],
        runner: ProcessRunner,
        evidence: EvidenceStore,
        timeout_seconds: int = 900,
    ) -> None:
        if not gate_id.strip():
            raise ValueError("gate_id must be non-empty")
        if not command:
            raise ValueError("quality gate command must be non-empty")
        self._gate_id = gate_id
        self._command = command
        self._runner = runner
        self._evidence = evidence
        self._timeout_seconds = timeout_seconds

    @property
    def gate_id(self) -> str:
        return self._gate_id

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck:
        cwd = Path(candidate.worktree_path)
        if not cwd.is_absolute():
            raise ValueError("candidate worktree path must be absolute")
        started = datetime.now(UTC)
        status = VerificationStatus.BLOCKED
        payload: dict[str, object] = {
            "gate": self._gate_id,
            "candidate_id": candidate.id,
            "command": list(self._command),
        }
        returncode = -1
        try:
            result = self._runner.run(
                CommandRequest(
                    command=self._command,
                    cwd=cwd,
                    timeout_seconds=self._timeout_seconds,
                )
            )
            returncode = result.returncode
            status = (
                VerificationStatus.PASSED
                if result.returncode == 0
                else VerificationStatus.FAILED
            )
            payload.update(
                {
                    "returncode": result.returncode,
                    "stdout_bytes": len(result.stdout.encode("utf-8")),
                    "stdout_sha256": _digest_text(result.stdout),
                    "stderr_bytes": len(result.stderr.encode("utf-8")),
                    "stderr_sha256": _digest_text(result.stderr),
                }
            )
        except (CommandUnavailable, CommandTimedOut) as exc:
            payload.update({"error_type": type(exc).__name__, "error": str(exc)})
        ended = datetime.now(UTC)
        payload.update(
            {
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "status": status.value,
            }
        )
        evidence_ref = self._evidence.write_json(
            "quality",
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
            measurements={
                "returncode": returncode,
                "duration_ms": max(0, int((ended - started).total_seconds() * 1000)),
            },
        )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
