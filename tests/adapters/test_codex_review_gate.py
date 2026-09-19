import json
from pathlib import Path

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.quality import CodexReviewGate
from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision
from autonomous_development.ports.codex import (
    CodexTurnRequest,
    CodexTurnResult,
)


class FakeCodex:
    def __init__(self, message: str, status: str = "completed") -> None:
        self.message = message
        self.status = status
        self.requests: list[CodexTurnRequest] = []

    def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult:
        self.requests.append(request)
        return CodexTurnResult(
            thread_id="review-thread",
            turn_id="review-turn",
            status=self.status,
            events=(),
            agent_messages=(self.message,),
        )


def candidate(tmp_path: Path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-review",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path.resolve()),
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("src/app.py", "tests/test_app.py"),
        codex_thread_id="implementation-thread",
        implementation_attempt=1,
    )


def test_codex_review_passes_without_blocking_findings(tmp_path: Path) -> None:
    codex = FakeCodex(json.dumps({"summary": "looks sound", "blocking_findings": []}))
    gate = CodexReviewGate(
        codex,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.PASSED
    assert codex.requests[0].sandbox.value == "read-only"


def test_codex_review_fails_with_blocking_finding(tmp_path: Path) -> None:
    codex = FakeCodex(
        json.dumps(
            {
                "summary": "state transition can skip verification",
                "blocking_findings": ["verification bypass"],
            }
        )
    )
    gate = CodexReviewGate(
        codex,
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.FAILED
    assert check.measurements["blocking_findings"] == 1


def test_invalid_review_output_blocks_instead_of_passing(tmp_path: Path) -> None:
    gate = CodexReviewGate(
        FakeCodex("not-json"),
        LocalEvidenceStore((tmp_path / "evidence").resolve()),
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.BLOCKED
