from __future__ import annotations

from pathlib import Path
from typing import Protocol

from autonomous_development.domain.build import DockerBuildSpec
from autonomous_development.domain.models import BuildArtifact, CandidateRevision


class BuildProviderError(RuntimeError):
    """Candidate artifact construction or supply-chain verification failed."""


class BuildProvider(Protocol):
    def build(
        self,
        candidate: CandidateRevision,
        *,
        workspace: Path,
        spec: DockerBuildSpec,
    ) -> BuildArtifact: ...
