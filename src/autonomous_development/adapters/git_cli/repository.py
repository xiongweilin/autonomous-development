from __future__ import annotations

import re
import subprocess
from pathlib import Path

from autonomous_development.ports.repository import (
    CandidateCommit,
    RepositoryBaseline,
    RepositoryProvider,
    Worktree,
)


class GitRepositoryError(RuntimeError):
    """A Git repository operation failed or violated the workspace contract."""


class GitCliRepository(RepositoryProvider):
    def __init__(self, *, git: str = "git", timeout_seconds: int = 120) -> None:
        self._git = git
        self._timeout_seconds = timeout_seconds

    def verify_baseline(
        self,
        repository_root: Path,
        default_branch: str,
    ) -> RepositoryBaseline:
        root = repository_root.resolve()
        if not root.is_dir():
            raise GitRepositoryError(f"repository does not exist: {root}")
        top = Path(self._run(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise GitRepositoryError(f"repository root mismatch: expected {root}, got {top}")
        status = self._run(root, "status", "--porcelain")
        if status:
            raise GitRepositoryError("baseline repository must be clean")
        current = self._run(root, "branch", "--show-current")
        if current != default_branch:
            raise GitRepositoryError(
                f"baseline must be on {default_branch}, currently on {current or 'detached HEAD'}"
            )
        commit = self._run(root, "rev-parse", "HEAD")
        tree = self._run(root, "rev-parse", "HEAD^{tree}")
        return RepositoryBaseline(root, commit, tree, current)

    def create_worktree(
        self,
        baseline: RepositoryBaseline,
        *,
        cycle_id: str,
        worktree_root: Path,
    ) -> Worktree:
        safe_id = _safe_cycle_id(cycle_id)
        root = worktree_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = (root / safe_id).resolve()
        if root not in path.parents:
            raise GitRepositoryError("resolved worktree escaped its configured root")
        if path.exists():
            raise GitRepositoryError(f"worktree path already exists: {path}")
        branch = f"autodev/{safe_id}"
        self._run(
            baseline.repository_root,
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            baseline.commit,
        )
        return Worktree(path=path, branch=branch, base_commit=baseline.commit)

    def changed_paths(self, worktree: Worktree) -> tuple[str, ...]:
        output = self._run(
            worktree.path,
            "status",
            "--porcelain=v1",
            "-z",
            include_trailing=True,
        )
        return _parse_porcelain_paths(output)

    def commit_candidate(
        self,
        worktree: Worktree,
        *,
        message: str,
    ) -> CandidateCommit:
        if not message.strip():
            raise ValueError("commit message must be non-empty")
        changed = self.changed_paths(worktree)
        if not changed:
            raise GitRepositoryError("candidate has no changed files")
        self._run(worktree.path, "add", "--all")
        self._run(
            worktree.path,
            "-c",
            "user.name=Autonomous Development",
            "-c",
            "user.email=autonomous-development@localhost",
            "commit",
            "-m",
            message,
        )
        commit = self._run(worktree.path, "rev-parse", "HEAD")
        tree = self._run(worktree.path, "rev-parse", "HEAD^{tree}")
        return CandidateCommit(commit=commit, tree=tree, changed_paths=changed)

    def remove_worktree(
        self,
        baseline: RepositoryBaseline,
        worktree: Worktree,
    ) -> None:
        self._run(
            baseline.repository_root,
            "worktree",
            "remove",
            "--force",
            str(worktree.path),
        )

    def _run(
        self,
        cwd: Path,
        *args: str,
        include_trailing: bool = False,
    ) -> str:
        try:
            result = subprocess.run(
                [self._git, *args],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitRepositoryError(f"git command failed to execute: {args!r}") from exc
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise GitRepositoryError(
                f"git {' '.join(args)} failed with exit {result.returncode}: {stderr}"
            )
        return result.stdout if include_trailing else result.stdout.strip()


def _safe_cycle_id(cycle_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", cycle_id):
        raise GitRepositoryError("cycle id is not safe for branch/worktree use")
    return cycle_id


def _parse_porcelain_paths(output: str) -> tuple[str, ...]:
    fields = output.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise GitRepositoryError("malformed git status porcelain entry")
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            if index >= len(fields) or not fields[index]:
                raise GitRepositoryError("rename/copy status is missing destination path")
            path = fields[index]
            index += 1
        normalized = path.replace("\\", "/")
        if normalized not in paths:
            paths.append(normalized)
    return tuple(sorted(paths))
