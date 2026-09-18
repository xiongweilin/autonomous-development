from __future__ import annotations

import subprocess

import pytest

from autonomous_development.adapters.git_cli.repository import GitCliRepository
from autonomous_development.application.engineering import EngineeringService
from autonomous_development.domain.models import ChangeProposal
from autonomous_development.domain.policies import ScopeViolation
from autonomous_development.ports.codex import CodexTurnResult


class EditingCodex:
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path

    def run_turn(self, request):
        path = request.cwd / self.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 2\n", encoding="utf-8")
        return CodexTurnResult(
            thread_id="thread-1",
            turn_id="turn-1",
            status="completed",
            events=(),
            agent_messages=("done",),
        )


def run(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    run(repo, "add", ".")
    run(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "baseline",
    )
    return repo


def proposal(repo, *, forbidden=()) -> ChangeProposal:
    baseline = run(repo, "rev-parse", "HEAD")
    return ChangeProposal(
        id="proposal-1",
        target_id="target-1",
        baseline_release_id="release-1",
        baseline_commit=baseline,
        objective_revision_id="objective-1",
        diagnosis_id=None,
        acceptance_criteria=("change VALUE to 2",),
        allowed_paths=("src",),
        forbidden_paths=forbidden,
        max_implementation_attempts=2,
        mandatory_gates=("tests", "static"),
    )


def test_engineering_service_produces_bounded_candidate(tmp_path) -> None:
    repo = make_repo(tmp_path)
    service = EngineeringService(GitCliRepository(), EditingCodex("src/app.py"))

    result = service.implement(
        proposal(repo),
        repository_root=repo,
        default_branch="main",
        worktree_root=tmp_path / "worktrees",
        cycle_id="cycle-1",
        attempt=1,
    )

    assert result.candidate.changed_paths == ("src/app.py",)
    assert result.candidate.base_commit == run(repo, "rev-parse", "HEAD")
    assert result.candidate.candidate_commit != result.candidate.base_commit
    assert result.candidate.codex_thread_id == "thread-1"


def test_engineering_service_blocks_codex_scope_escape(tmp_path) -> None:
    repo = make_repo(tmp_path)
    service = EngineeringService(GitCliRepository(), EditingCodex("forbidden.txt"))

    with pytest.raises(ScopeViolation):
        service.implement(
            proposal(repo),
            repository_root=repo,
            default_branch="main",
            worktree_root=tmp_path / "worktrees",
            cycle_id="cycle-escape",
            attempt=1,
        )


def test_engineering_service_rejects_stale_baseline(tmp_path) -> None:
    repo = make_repo(tmp_path)
    stale = proposal(repo)
    (repo / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    run(repo, "add", ".")
    run(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "advance",
    )
    service = EngineeringService(GitCliRepository(), EditingCodex("src/app.py"))

    with pytest.raises(ValueError, match="baseline moved"):
        service.implement(
            stale,
            repository_root=repo,
            default_branch="main",
            worktree_root=tmp_path / "worktrees",
            cycle_id="cycle-stale",
            attempt=1,
        )
