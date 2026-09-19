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
        branch = f"autodev/{safe_id}"

        if path.exists():
            self._validate_existing_worktree(path, branch, baseline.commit)
            return Worktree(path=path, branch=branch, base_commit=baseline.commit)

        self._run(baseline.repository_root, "worktree", "prune")
        branch_exists = (
            self._returncode(
                baseline.repository_root,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            )
            == 0
        )
        if branch_exists:
            self._run(
                baseline.repository_root,
                "worktree",
                "add",
                str(path),
                branch,
            )
        else:
            self._run(
                baseline.repository_root,
                "worktree",
                "add",
                "-b",
                branch,
                str(path),
                baseline.commit,
            )
        self._validate_existing_worktree(path, branch, baseline.commit)
        return Worktree(path=path, branch=branch, base_commit=baseline.commit)

    def changed_paths(self, worktree: Worktree) -> tuple[str, ...]:
        tracked = _parse_nul_paths(
            self._run(
                worktree.path,
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                "HEAD",
                include_trailing=True,
            )
        )
        staged = _parse_nul_paths(
            self._run(
                worktree.path,
                "diff",
                "--cached",
                "--name-only",
                "--no-renames",
                "-z",
                "HEAD",
                include_trailing=True,
            )
        )
        untracked = _parse_nul_paths(
            self._run(
                worktree.path,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                include_trailing=True,
            )
        )
        return tuple(sorted(set((*tracked, *staged, *untracked))))

    def recover_candidate(
        self,
        worktree: Worktree,
        *,
        expected_message: str,
    ) -> CandidateCommit | None:
        if not expected_message.strip():
            raise ValueError("expected candidate message must be non-empty")
        if self.changed_paths(worktree):
            return None

        head = self._run(worktree.path, "rev-parse", "HEAD")
        if head == worktree.base_commit:
            return None
        count = self._run(
            worktree.path,
            "rev-list",
            "--count",
            f"{worktree.base_commit}..{head}",
        )
        if count != "1":
            raise GitRepositoryError(
                "candidate branch advanced by an unexpected number of commits"
            )
        subject = self._run(worktree.path, "log", "-1", "--format=%s")
        if subject != expected_message:
            raise GitRepositoryError(
                "candidate branch contains an unexpected commit message"
            )
        thread_id = self._run(
            worktree.path,
            "log",
            "-1",
            "--format=%(trailers:key=Autodev-Codex-Thread,valueonly)",
        )
        if not thread_id:
            raise GitRepositoryError(
                "candidate commit is missing the durable Codex thread trailer"
            )
        tree = self._run(worktree.path, "rev-parse", "HEAD^{tree}")
        changed_paths = _parse_nul_paths(
            self._run(
                worktree.path,
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                worktree.base_commit,
                head,
                include_trailing=True,
            )
        )
        if not changed_paths:
            raise GitRepositoryError("candidate commit has no changed paths")
        return CandidateCommit(
            commit=head,
            tree=tree,
            changed_paths=changed_paths,
            codex_thread_id=thread_id,
        )

    def commit_candidate(
        self,
        worktree: Worktree,
        *,
        message: str,
        codex_thread_id: str,
    ) -> CandidateCommit:
        if not message.strip():
            raise ValueError("commit message must be non-empty")
        if not codex_thread_id.strip():
            raise ValueError("Codex thread id must be non-empty")

        existing = self.recover_candidate(
            worktree,
            expected_message=message,
        )
        if existing is not None:
            if existing.codex_thread_id != codex_thread_id:
                raise GitRepositoryError(
                    "candidate commit Codex thread differs from replayed thread"
                )
            return existing

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
            "-m",
            f"Autodev-Codex-Thread: {codex_thread_id}",
        )
        committed = self.recover_candidate(
            worktree,
            expected_message=message,
        )
        if committed is None:
            raise GitRepositoryError("candidate commit could not be reconciled after commit")
        if committed.codex_thread_id != codex_thread_id:
            raise GitRepositoryError("candidate commit persisted the wrong Codex thread")
        return committed

    def promote_candidate(
        self,
        repository_root: Path,
        default_branch: str,
        *,
        baseline_commit: str,
        candidate_commit: str,
        candidate_tree: str,
    ) -> CandidateCommit:
        root = repository_root.resolve()
        if not root.is_dir():
            raise GitRepositoryError(f"repository does not exist: {root}")
        top = Path(self._run(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise GitRepositoryError(f"repository root mismatch: expected {root}, got {top}")
        if self._run(root, "status", "--porcelain"):
            raise GitRepositoryError("default branch checkout must be clean for source promotion")
        branch = self._run(root, "branch", "--show-current")
        if branch != default_branch:
            observed_branch = branch or "detached HEAD"
            raise GitRepositoryError(
                f"source promotion requires {default_branch}, currently on {observed_branch}"
            )

        observed_candidate_tree = self._run(
            root,
            "rev-parse",
            f"{candidate_commit}^{{tree}}",
        )
        if observed_candidate_tree != candidate_tree:
            raise GitRepositoryError("candidate commit tree does not match recorded candidate tree")
        merge_base = self._run(root, "merge-base", baseline_commit, candidate_commit)
        if merge_base != baseline_commit:
            raise GitRepositoryError("candidate commit is not descended from recorded baseline")

        head = self._run(root, "rev-parse", "HEAD")
        if head == candidate_commit:
            return self._promoted_commit(
                root,
                baseline_commit=baseline_commit,
                candidate_commit=candidate_commit,
                candidate_tree=candidate_tree,
            )
        if head != baseline_commit:
            raise GitRepositoryError(
                "default branch moved after candidate creation; source promotion is blocked"
            )

        self._run(root, "merge", "--ff-only", candidate_commit)
        return self._promoted_commit(
            root,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree,
        )

    def _promoted_commit(
        self,
        root: Path,
        *,
        baseline_commit: str,
        candidate_commit: str,
        candidate_tree: str,
    ) -> CandidateCommit:
        head = self._run(root, "rev-parse", "HEAD")
        tree = self._run(root, "rev-parse", "HEAD^{tree}")
        if head != candidate_commit or tree != candidate_tree:
            raise GitRepositoryError("default branch did not reconcile to the candidate identity")
        changed_paths = _parse_nul_paths(
            self._run(
                root,
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                baseline_commit,
                candidate_commit,
                include_trailing=True,
            )
        )
        if not changed_paths:
            raise GitRepositoryError("promoted candidate contains no source changes")
        thread_id = self._run(
            root,
            "log",
            "-1",
            "--format=%(trailers:key=Autodev-Codex-Thread,valueonly)",
        )
        return CandidateCommit(
            commit=head,
            tree=tree,
            changed_paths=changed_paths,
            codex_thread_id=thread_id or None,
        )

    def restore_baseline(
        self,
        repository_root: Path,
        default_branch: str,
        *,
        baseline_commit: str,
    ) -> RepositoryBaseline:
        root = repository_root.resolve()
        current = self.verify_baseline(root, default_branch)
        if current.commit == baseline_commit:
            return current

        merge_base = self._run(root, "merge-base", baseline_commit, current.commit)
        if merge_base != baseline_commit:
            raise GitRepositoryError(
                "current default branch is not descended from rollback baseline"
            )
        count = self._run(root, "rev-list", "--count", f"{baseline_commit}..{current.commit}")
        if count != "1":
            raise GitRepositoryError(
                "source rollback refuses to discard more than one autonomous commit"
            )
        subject = self._run(root, "log", "-1", "--format=%s")
        thread_id = self._run(
            root,
            "log",
            "-1",
            "--format=%(trailers:key=Autodev-Codex-Thread,valueonly)",
        )
        if not subject.startswith("autodev: implement ") or not thread_id:
            raise GitRepositoryError(
                "source rollback refuses a default branch head without autonomous provenance"
            )

        self._run(root, "reset", "--hard", baseline_commit)
        restored = self.verify_baseline(root, default_branch)
        if restored.commit != baseline_commit:
            raise GitRepositoryError("default branch did not reconcile to rollback baseline")
        return restored

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

    def _validate_existing_worktree(
        self,
        path: Path,
        expected_branch: str,
        base_commit: str,
    ) -> None:
        top = Path(self._run(path, "rev-parse", "--show-toplevel")).resolve()
        if top != path:
            raise GitRepositoryError(
                f"existing worktree root mismatch: expected {path}, got {top}"
            )
        branch = self._run(path, "branch", "--show-current")
        if branch != expected_branch:
            raise GitRepositoryError(
                f"existing worktree branch mismatch: expected {expected_branch}, got {branch}"
            )
        merge_base = self._run(path, "merge-base", base_commit, "HEAD")
        if merge_base != base_commit:
            raise GitRepositoryError(
                "existing worktree is not descended from the requested baseline"
            )

    def _returncode(self, cwd: Path, *args: str) -> int:
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
        return result.returncode

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


def _parse_nul_paths(output: str) -> tuple[str, ...]:
    paths = {
        item.replace("\\", "/")
        for item in output.split("\0")
        if item
    }
    return tuple(sorted(paths))
