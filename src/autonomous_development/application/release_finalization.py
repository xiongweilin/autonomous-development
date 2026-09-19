from __future__ import annotations

from datetime import datetime

from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import (
    BuildArtifact,
    CandidateRevision,
    Deployment,
    DevelopmentCycle,
    ReleasedVersion,
)


class ReleaseFinalizationService:
    def __init__(self, catalog: ReleaseCatalogService) -> None:
        self._catalog = catalog

    def finalize(
        self,
        cycle: DevelopmentCycle,
        candidate: CandidateRevision,
        artifact: BuildArtifact,
        deployment: Deployment,
        *,
        release_id: str,
        promoted_at: datetime,
        operation_id: str,
    ) -> ReleasedVersion:
        if not release_id.strip():
            raise ValueError("release_id must be non-empty")
        if not operation_id.strip():
            raise ValueError("operation_id must be non-empty")
        if cycle.state is not CycleState.PROMOTED:
            raise ValueError("release finalization requires a promoted cycle")
        if cycle.release_decision is not ReleaseDecisionKind.PROMOTE:
            raise ValueError("release finalization requires an explicit promote decision")
        if cycle.candidate_id != candidate.id:
            raise ValueError("candidate does not match promoted cycle")
        if cycle.artifact_id != artifact.id:
            raise ValueError("artifact does not match promoted cycle")
        if cycle.candidate_deployment_id != deployment.id:
            raise ValueError("deployment does not match promoted cycle")
        if artifact.candidate_id != candidate.id:
            raise ValueError("artifact does not belong to candidate")
        if artifact.source_tree_hash != candidate.tree_hash:
            raise ValueError("artifact source tree does not match candidate tree")
        if deployment.artifact_id != artifact.id:
            raise ValueError("deployment does not serve promoted artifact")
        if deployment.target_id != cycle.target_id:
            raise ValueError("deployment target does not match promoted cycle")

        release = ReleasedVersion(
            id=release_id,
            target_id=cycle.target_id,
            source_commit=candidate.candidate_commit,
            source_tree=candidate.tree_hash,
            artifact_digest=artifact.image_digest,
            objective_revision_id=cycle.objective_revision_id,
            deployment_id=deployment.id,
            promoted_at=promoted_at,
        )
        persisted = self._catalog.register(release)
        self._catalog.set_serving(
            cycle.target_id,
            persisted.id,
            operation_id=f"{operation_id}:serving",
        )
        return persisted

    def restore_serving(
        self,
        target_id: str,
        release_id: str,
        *,
        operation_id: str,
    ) -> ReleasedVersion:
        return self._catalog.set_serving(
            target_id,
            release_id,
            operation_id=operation_id,
        )
