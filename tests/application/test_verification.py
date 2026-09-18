from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonomous_development.application.verification import (
    VerificationPolicyError,
    VerificationService,
)
from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision, VerificationCheck


class FakeGate:
    def __init__(self, gate_id: str, status: VerificationStatus) -> None:
        self._gate_id = gate_id
        self._status = status

    @property
    def gate_id(self) -> str:
        return self._gate_id

    def evaluate(self, candidate: CandidateRevision) -> VerificationCheck:
        now = datetime.now(UTC)
        return VerificationCheck(
            id=f"{candidate.id}:{self._gate_id}",
            gate=self._gate_id,
            status=self._status,
            started_at=now,
            ended_at=now,
            evidence_refs=(f"evidence:{self._gate_id}",),
        )


def candidate(tmp_path: Path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-1",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path),
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("src/app.py",),
        codex_thread_id="thread-1",
        implementation_attempt=1,
    )


def test_required_gates_run_in_declared_order(tmp_path: Path) -> None:
    service = VerificationService(
        [
            FakeGate("static", VerificationStatus.PASSED),
            FakeGate("tests", VerificationStatus.FAILED),
        ]
    )
    run = service.run(
        candidate(tmp_path),
        run_id="verify-1",
        required_gates=("static", "tests"),
    )
    assert tuple(check.gate for check in run.checks) == ("static", "tests")
    assert not run.passed


def test_missing_required_gate_fails_before_claiming_verification(tmp_path: Path) -> None:
    service = VerificationService([FakeGate("static", VerificationStatus.PASSED)])
    with pytest.raises(VerificationPolicyError, match="not configured"):
        service.run(
            candidate(tmp_path),
            run_id="verify-1",
            required_gates=("static", "security"),
        )


def test_duplicate_gate_configuration_is_rejected() -> None:
    with pytest.raises(VerificationPolicyError, match="duplicate"):
        VerificationService(
            [
                FakeGate("tests", VerificationStatus.PASSED),
                FakeGate("tests", VerificationStatus.PASSED),
            ]
        )
