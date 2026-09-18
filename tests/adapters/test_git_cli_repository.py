from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autonomous_development.adapters.git_cli.repository import (
    GitCliRepository,
    GitRepositoryError,
)


def run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    run(repo, "add", "app.py")
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


def test_worktree_candidate_has_stable_provenance(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")
    worktree = provider.create_worktree(
        baseline,
        cycle_id="cycle-1",
        worktree_root=tmp_path / "worktrees",
    )
    (worktree.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert provider.changed_paths(worktree) == ("app.py",)
    candidate = provider.commit_candidate(
        worktree,
        message="candidate",
        codex_thread_id="thread-1",
    )

    assert candidate.changed_paths == ("app.py",)
    assert candidate.commit != baseline.commit
    assert candidate.tree != baseline.tree
    assert candidate.codex_thread_id == "thread-1"
    assert run(repo, "rev-parse", "HEAD") == baseline.commit

    recovered = provider.recover_candidate(worktree, expected_message="candidate")
    assert recovered == candidate
    provider.remove_worktree(baseline, worktree)
    assert not worktree.path.exists()


def test_create_worktree_reconciles_same_cycle_after_restart(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")

    first = provider.create_worktree(
        baseline,
        cycle_id="cycle-restart",
        worktree_root=tmp_path / "worktrees",
    )
    (first.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    second = provider.create_worktree(
        baseline,
        cycle_id="cycle-restart",
        worktree_root=tmp_path / "worktrees",
    )
    assert second == first
    assert provider.changed_paths(second) == ("app.py",)


def test_changed_paths_treats_rename_as_old_and_new_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")
    worktree = provider.create_worktree(
        baseline,
        cycle_id="cycle-rename",
        worktree_root=tmp_path / "worktrees",
    )
    run(worktree.path, "mv", "app.py", "moved.py")

    assert provider.changed_paths(worktree) == ("app.py", "moved.py")


def test_committed_candidate_replay_does_not_create_second_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")
    worktree = provider.create_worktree(
        baseline,
        cycle_id="cycle-commit-replay",
        worktree_root=tmp_path / "worktrees",
    )
    (worktree.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    first = provider.commit_candidate(
        worktree,
        message="autodev: implement proposal-1",
        codex_thread_id="thread-1",
    )
    second = provider.commit_candidate(
        worktree,
        message="autodev: implement proposal-1",
        codex_thread_id="thread-1",
    )
    assert second == first
    assert run(worktree.path, "rev-list", "--count", f"{baseline.commit}..HEAD") == "1"


def test_dirty_baseline_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")
    provider = GitCliRepository()

    with pytest.raises(GitRepositoryError):
        provider.verify_baseline(repo, "main")


def test_unsafe_cycle_id_is_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")

    with pytest.raises(GitRepositoryError):
        provider.create_worktree(
            baseline,
            cycle_id="../escape",
            worktree_root=tmp_path / "worktrees",
        )
