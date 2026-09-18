from __future__ import annotations

import json
from pathlib import Path

from autonomous_development.adapters.docker_build import DockerBuildProvider
from autonomous_development.adapters.file_evidence import FileEvidenceStore
from autonomous_development.domain.build import DockerBuildSpec
from autonomous_development.domain.commands import CommandResult
from autonomous_development.domain.models import CandidateRevision


class FakeRunner:
    def run(self, spec, *, default_cwd: Path):
        argv = spec.argv
        if len(argv) > 1 and argv[1] == "build":
            iidfile = Path(argv[argv.index("--iidfile") + 1])
            iidfile.write_text("sha256:" + "a" * 64, encoding="utf-8")
        elif argv[0] == "syft":
            output = argv[argv.index("-o") + 1]
            path = Path(output.split("=", 1)[1])
            path.write_text(json.dumps({"spdxVersion": "SPDX-2.3"}), encoding="utf-8")
        return CommandResult(
            argv=argv,
            exit_code=0,
            stdout="{}",
            stderr="",
            duration_seconds=0.01,
        )


def candidate(workspace: Path) -> CandidateRevision:
    return CandidateRevision(
        id="candidate-1",
        cycle_id="cycle-1",
        worktree_path=str(workspace),
        branch_name="autodev/cycle-1",
        base_commit="base",
        candidate_commit="commit",
        tree_hash="tree-hash",
        changed_paths=("app.py",),
        codex_thread_id="thread",
        implementation_attempt=1,
    )


def test_docker_build_binds_source_sbom_and_scan(tmp_path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("lock", encoding="utf-8")
    provider = DockerBuildProvider(
        FakeRunner(),
        FileEvidenceStore(tmp_path / "evidence"),
    )

    artifact = provider.build(
        candidate(tmp_path),
        workspace=tmp_path,
        spec=DockerBuildSpec(dependency_lock_files=("uv.lock",)),
    )

    assert artifact.image_digest == "sha256:" + "a" * 64
    assert artifact.source_tree_hash == "tree-hash"
    assert artifact.sbom_digest
    assert artifact.vulnerability_scan_ref.startswith("file-evidence:build-candidate-1:")
