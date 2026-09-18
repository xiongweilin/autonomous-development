from pathlib import Path

import pytest

from autonomous_development.application.build import BuildRejected, BuildService
from autonomous_development.domain.models import CandidateRevision
from autonomous_development.ports.build import BuiltImage, SupplyChainEvidence


class FakeBuilder:
    def build(self, request: object) -> BuiltImage:
        return BuiltImage(image_digest="sha256:" + "1" * 64, evidence_ref="build:evidence")


class FakeScanner:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed

    def scan(self, image_digest: str, *, candidate_id: str) -> SupplyChainEvidence:
        assert image_digest.startswith("sha256:")
        return SupplyChainEvidence(
            sbom_digest="sha256:" + "2" * 64,
            sbom_ref=f"sbom:{candidate_id}",
            vulnerability_scan_ref=f"scan:{candidate_id}",
            passed=self.passed,
        )


def candidate(tmp_path: Path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-build",
        cycle_id="cycle-1",
        worktree_path=str(tmp_path.resolve()),
        branch_name="autodev/cycle-1",
        base_commit="a" * 40,
        candidate_commit="b" * 40,
        tree_hash="c" * 40,
        changed_paths=("Dockerfile", "uv.lock"),
        codex_thread_id="thread-1",
        implementation_attempt=1,
    )


def test_build_artifact_binds_source_definition_locks_and_scan(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    lockfile = tmp_path / "uv.lock"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    lockfile.write_text("version = 1\n", encoding="utf-8")

    artifact = BuildService(FakeBuilder(), FakeScanner()).build(
        candidate(tmp_path),
        artifact_id="artifact-1",
        dockerfile=dockerfile,
        dependency_locks=(lockfile,),
    )

    assert artifact.image_digest == "sha256:" + "1" * 64
    assert artifact.source_tree_hash == "c" * 40
    assert artifact.build_definition_digest.startswith("sha256:")
    assert artifact.dependency_lock_digest.startswith("sha256:")
    assert artifact.sbom_digest == "sha256:" + "2" * 64
    assert artifact.vulnerability_scan_ref == "scan:candidate-build"


def test_vulnerability_threshold_blocks_artifact(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    lockfile = tmp_path / "uv.lock"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    lockfile.write_text("version = 1\n", encoding="utf-8")

    with pytest.raises(BuildRejected, match="vulnerability"):
        BuildService(FakeBuilder(), FakeScanner(passed=False)).build(
            candidate(tmp_path),
            artifact_id="artifact-1",
            dockerfile=dockerfile,
            dependency_locks=(lockfile,),
        )


def test_build_inputs_cannot_escape_candidate_worktree(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    lockfile = worktree / "uv.lock"
    lockfile.write_text("version = 1\n", encoding="utf-8")
    outside = tmp_path / "Dockerfile"
    outside.write_text("FROM scratch\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inside"):
        BuildService(FakeBuilder(), FakeScanner()).build(
            candidate(worktree),
            artifact_id="artifact-1",
            dockerfile=outside,
            dependency_locks=(lockfile,),
        )
