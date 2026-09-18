import sys
from pathlib import Path

from autonomous_development.adapters.evidence import LocalEvidenceStore
from autonomous_development.adapters.process import SubprocessRunner
from autonomous_development.adapters.quality import CommandQualityGate
from autonomous_development.domain.enums import VerificationStatus
from autonomous_development.domain.models import CandidateRevision


def candidate(tmp_path: Path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-command",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path.resolve()),
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("src/app.py",),
        codex_thread_id="thread-1",
        implementation_attempt=1,
    )


def test_command_gate_passes_only_on_zero_exit(tmp_path: Path) -> None:
    gate = CommandQualityGate(
        gate_id="tests",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        runner=SubprocessRunner(),
        evidence=LocalEvidenceStore((tmp_path / "evidence").resolve()),
        timeout_seconds=10,
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.PASSED
    assert check.measurements["returncode"] == 0


def test_command_gate_records_nonzero_as_failure(tmp_path: Path) -> None:
    gate = CommandQualityGate(
        gate_id="tests",
        command=(sys.executable, "-c", "raise SystemExit(7)"),
        runner=SubprocessRunner(),
        evidence=LocalEvidenceStore((tmp_path / "evidence").resolve()),
        timeout_seconds=10,
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.FAILED
    assert check.measurements["returncode"] == 7


def test_command_gate_blocks_when_tool_is_unavailable(tmp_path: Path) -> None:
    gate = CommandQualityGate(
        gate_id="security",
        command=("definitely-not-a-real-autodev-command",),
        runner=SubprocessRunner(),
        evidence=LocalEvidenceStore((tmp_path / "evidence").resolve()),
        timeout_seconds=10,
    )
    check = gate.evaluate(candidate(tmp_path))
    assert check.status is VerificationStatus.BLOCKED
    assert check.measurements["returncode"] == -1
