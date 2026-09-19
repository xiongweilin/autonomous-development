from __future__ import annotations

from pathlib import Path

from autonomous_development.domain.models import CandidateRevision
from autonomous_development.ports.repository import RepositoryProvider


class SourcePromotionService:
    def __init__(self, repository: RepositoryProvider) -> None:
        self._repository = repository

    def promote(
        self,
        candidate: CandidateRevision,
        *,
        repository_root: Path,
        default_branch: str,
    ) -> CandidateRevision:
        promoted = self._repository.promote_candidate(
            repository_root,
            default_branch,
            baseline_commit=candidate.base_commit,
            candidate_commit=candidate.candidate_commit,
            candidate_tree=candidate.tree_hash,
        )
        if promoted.commit != candidate.candidate_commit:
            raise RuntimeError("source promotion returned a different candidate commit")
        if promoted.tree != candidate.tree_hash:
            raise RuntimeError("source promotion returned a different candidate tree")
        if promoted.changed_paths != candidate.changed_paths:
            raise RuntimeError("source promotion changed candidate path identity")
        if (
            promoted.codex_thread_id is not None
            and promoted.codex_thread_id != candidate.codex_thread_id
        ):
            raise RuntimeError("source promotion changed Codex thread identity")
        return candidate

    def restore_baseline(
        self,
        *,
        repository_root: Path,
        default_branch: str,
        baseline_commit: str,
    ) -> None:
        restored = self._repository.restore_baseline(
            repository_root,
            default_branch,
            baseline_commit=baseline_commit,
        )
        if restored.commit != baseline_commit:
            raise RuntimeError("source rollback returned a different baseline commit")
