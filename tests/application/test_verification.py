from __future__ import annotations

import sys

from autonomous_development.adapters.file_evidence import FileEvidenceStore
from autonomous_development.adapters.subprocess_runner import LocalCommandRunner
from autonomous_development.application.verification import VerificationService
from autonomous_development.domain.commands import CommandSpec
from autonomous_development.domain.models import CandidateRevision
from autonomous_development.domain.verification import (
    VerificationGateSpec,
    VerificationPlan,
)


def candidate(tmp_path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-1",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path),
        branch_name="autodev/cycle-1",
        base_commit="base",
        candidate_commit="candidate",
        tree_hash="tree",
        changed_paths=("src/app.py",),
        codex_thread_id="thread",
        implementation_attempt=1,
    )


def test_verification_records_machine_evidence_for_every_gate(tmp_path) -> None:
    plan = VerificationPlan(
        gates=(
            VerificationGateSpec(
                "static",
                (CommandSpec((sys.executable, "-c", "print('static')")),),
            ),
            VerificationGateSpec(
                "tests",
                (CommandSpec((sys.executable, "-c", "print('tests')")),),
            ),
            VerificationGateSpec(
                "security",
                (CommandSpec((sys.executable, "-c", "print('security')")),),
            ),
        )
    )
    run = VerificationService(
        LocalCommandRunner(),
        FileEvidenceStore(tmp_path / "evidence"),
    ).verify(candidate(tmp_path), plan, workspace=tmp_path, run_id="verify-1")

    assert run.passed
    assert run.passed_gates == frozenset({"static", "tests", "security"})
    assert all(check.evidence_refs for check in run.checks)


def test_failed_command_fails_gate(tmp_path) -> None:
    plan = VerificationPlan(
        gates=(
            VerificationGateSpec(
                "static",
                (CommandSpec((sys.executable, "-c", "raise SystemExit(3)")),),
            ),
            VerificationGateSpec(
                "tests",
                (CommandSpec((sys.executable, "-c", "print('ok')")),),
            ),
            VerificationGateSpec(
                "security",
                (CommandSpec((sys.executable, "-c", "print('ok')")),),
            ),
        )
    )
    run = VerificationService(
        LocalCommandRunner(),
        FileEvidenceStore(tmp_path / "evidence"),
    ).verify(candidate(tmp_path), plan, workspace=tmp_path, run_id="verify-fail")

    assert not run.passed
    assert "static" not in run.passed_gates


def test_missing_required_platform_gate_is_rejected() -> None:
    plan = VerificationPlan(
        gates=(
            VerificationGateSpec(
                "tests",
                (CommandSpec(("pytest",)),),
            ),
        )
    )

    try:
        plan.require_platform_baseline()
    except ValueError as exc:
        assert "static" in str(exc)
        assert "security" in str(exc)
        return
    raise AssertionError("incomplete verification plan was accepted")
