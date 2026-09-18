from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RepositoryBaseline:
    repository_root: Path
    commit: str
    tree: str
    branch: str

    def __post_init__(self) -> None:
        if not self.repository_root.is_absolute():
            raise ValueError("repository root must be absolute")


@dataclass(frozen=True, slots=True)
class Worktree:
    path: Path
    branch: str
    base_commit: str

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError("worktree path must be absolute")


@dataclass(frozen=True, slots=True)
class CandidateCommit:
    commit: str
    tree: str
    changed_paths: tuple[str, ...]
    codex_thread_id: str | None = None


class RepositoryProvider(Protocol):
    def verify_baseline(self, repository_root: Path, default_branch: str) -> RepositoryBaseline: ...

    def create_worktree(
        self,
        baseline: RepositoryBaseline,
        *,
        cycle_id: str,
        worktree_root: Path,
    ) -> Worktree: ...

    def changed_paths(self, worktree: Worktree) -> tuple[str, ...]: ...

    def recover_candidate(
        self,
        worktree: Worktree,
        *,
        expected_message: str,
    ) -> CandidateCommit | None: ...

    def commit_candidate(
        self,
        worktree: Worktree,
        *,
        message: str,
        codex_thread_id: str,
    ) -> CandidateCommit: ...

    def promote_candidate(
        self,
        repository_root: Path,
        default_branch: str,
        *,
        baseline_commit: str,
        candidate_commit: str,
        candidate_tree: str,
    ) -> CandidateCommit: ...

    def remove_worktree(self, baseline: RepositoryBaseline, worktree: Worktree) -> None: ...
