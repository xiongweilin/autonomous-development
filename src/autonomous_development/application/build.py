from __future__ import annotations

import hashlib
from pathlib import Path

from autonomous_development.domain.models import BuildArtifact, CandidateRevision
from autonomous_development.ports.build import (
    BuildProvider,
    BuildRequest,
    SupplyChainScanner,
)


class BuildRejected(RuntimeError):
    pass


class BuildService:
    def __init__(
        self,
        build_provider: BuildProvider,
        scanner: SupplyChainScanner,
    ) -> None:
        self._build_provider = build_provider
        self._scanner = scanner

    def build(
        self,
        candidate: CandidateRevision,
        *,
        artifact_id: str,
        dockerfile: Path,
        dependency_locks: tuple[Path, ...],
    ) -> BuildArtifact:
        if not artifact_id.strip():
            raise ValueError("artifact_id must be non-empty")
        context = Path(candidate.worktree_path).resolve()
        resolved_dockerfile = dockerfile.resolve(strict=True)
        if not resolved_dockerfile.is_relative_to(context):
            raise ValueError("Dockerfile must be inside the candidate worktree")
        if not dependency_locks:
            raise ValueError("at least one dependency lockfile is required")

        resolved_locks = tuple(path.resolve(strict=True) for path in dependency_locks)
        for lockfile in resolved_locks:
            if not lockfile.is_relative_to(context):
                raise ValueError("dependency lockfile must be inside the candidate worktree")

        built = self._build_provider.build(
            BuildRequest(
                candidate_id=candidate.id,
                source_tree_hash=candidate.tree_hash,
                context_dir=context,
                dockerfile=resolved_dockerfile,
            )
        )
        if not built.image_digest.startswith("sha256:"):
            raise BuildRejected("build provider returned a non-content-addressed image identity")

        supply_chain = self._scanner.scan(
            built.image_digest,
            candidate_id=candidate.id,
        )
        if not supply_chain.passed:
            raise BuildRejected(
                "candidate image failed the configured vulnerability threshold: "
                f"{supply_chain.vulnerability_scan_ref}"
            )

        return BuildArtifact(
            id=artifact_id,
            candidate_id=candidate.id,
            image_digest=built.image_digest,
            source_tree_hash=candidate.tree_hash,
            build_definition_digest=_digest_file(resolved_dockerfile),
            dependency_lock_digest=_digest_lockset(context, resolved_locks),
            build_evidence_ref=built.evidence_ref,
            sbom_digest=supply_chain.sbom_digest,
            sbom_ref=supply_chain.sbom_ref,
            vulnerability_scan_ref=supply_chain.vulnerability_scan_ref,
        )


def _digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _digest_lockset(root: Path, paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()
