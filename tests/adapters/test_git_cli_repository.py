from __future__ import annotations

import subprocess

from autonomous_development.adapters.git_cli.repository import (
    GitCliRepository,
    GitRepositoryError,
)


def run(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_repo(tmp_path):
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


def test_worktree_candidate_has_stable_provenance(tmp_path) -> None:
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
    candidate = provider.commit_candidate(worktree, message="candidate")

    assert candidate.changed_paths == ("app.py",)
    assert candidate.commit != baseline.commit
    assert candidate.tree != baseline.tree
    assert run(repo, "rev-parse", "HEAD") == baseline.commit

    provider.remove_worktree(baseline, worktree)
    assert not worktree.path.exists()


def test_dirty_baseline_is_rejected(tmp_path) -> None:
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")
    provider = GitCliRepository()

    try:
        provider.verify_baseline(repo, "main")
    except GitRepositoryError:
        return
    raise AssertionError("dirty baseline was accepted")


def test_unsafe_cycle_id_is_rejected(tmp_path) -> None:
    repo = make_repo(tmp_path)
    provider = GitCliRepository()
    baseline = provider.verify_baseline(repo, "main")

    try:
        provider.create_worktree(
            baseline,
            cycle_id="../escape",
            worktree_root=tmp_path / "worktrees",
        )
    except GitRepositoryError:
        return
    raise AssertionError("unsafe cycle id was accepted")
