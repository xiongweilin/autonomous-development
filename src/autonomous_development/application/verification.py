from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision, VerificationCheck, VerificationRun
from autonomous_development.domain.verification import VerificationPlan
from autonomous_development.ports.command import CommandRunner
from autonomous_development.ports.evidence import EvidenceStore


class VerificationService:
    def __init__(
        self,
        runner: CommandRunner,
        evidence: EvidenceStore,
    ) -> None:
        self._runner = runner
        self._evidence = evidence

    def verify(
        self,
        candidate: CandidateRevision,
        plan: VerificationPlan,
        *,
        workspace: Path,
        run_id: str,
    ) -> VerificationRun:
        plan.require_platform_baseline()
        checks: list[VerificationCheck] = []
        for gate in plan.gates:
            started = datetime.now(UTC)
            command_evidence: list[str] = []
            measurements: dict[str, float | int | str | bool] = {}
            passed = True
            blocked = False
            for index, command in enumerate(gate.commands):
                result = self._runner.run(command, default_cwd=workspace)
                payload = json.dumps(
                    {
                        "argv": result.argv,
                        "exit_code": result.exit_code,
                        "timed_out": result.timed_out,
                        "duration_seconds": result.duration_seconds,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                record = self._evidence.put_text(
                    namespace=f"verification-{run_id}",
                    content=payload,
                    media_type="application/json",
                )
                command_evidence.append(record.ref)
                measurements[f"command_{index}_duration_seconds"] = result.duration_seconds
                if result.timed_out:
                    passed = False
                    blocked = True
                elif not result.succeeded:
                    passed = False
            status = (
                VerificationStatus.PASSED
                if passed
                else VerificationStatus.BLOCKED
                if blocked
                else VerificationStatus.FAILED
            )
            checks.append(
                VerificationCheck(
                    id=f"{run_id}:{gate.id}",
                    gate=gate.id,
                    status=status,
                    started_at=started,
                    ended_at=datetime.now(UTC),
                    evidence_refs=tuple(command_evidence),
                    measurements=measurements,
                )
            )
        return VerificationRun(
            id=run_id,
            candidate_id=candidate.id,
            checks=tuple(checks),
        )
