from __future__ import annotations

from autonomous_development.domain.models import (
    ChangeProposal,
    Diagnosis,
    ProductObjectiveRevision,
    ReleasedVersion,
)
from autonomous_development.domain.policies import validate_changed_paths
from autonomous_development.ports.persistence import ChangeProposalRepository


class ProposalService:
    def __init__(self, repository: ChangeProposalRepository) -> None:
        self._repository = repository

    def from_diagnosis(
        self,
        diagnosis: Diagnosis,
        objective: ProductObjectiveRevision,
        baseline: ReleasedVersion,
        *,
        proposal_id: str,
        mandatory_gates: tuple[str, ...],
    ) -> ChangeProposal:
        if not proposal_id.strip():
            raise ValueError("proposal_id must be non-empty")
        if baseline.target_id != objective.target_id:
            raise ValueError("baseline and objective belong to different targets")
        if baseline.objective_revision_id != objective.id:
            raise ValueError("baseline release is not governed by the active objective")
        if len(diagnosis.requested_paths) > objective.mutation_policy.max_changed_files:
            raise ValueError("diagnosis requested more paths than the mutation budget allows")

        guard = ChangeProposal(
            id=proposal_id,
            target_id=objective.target_id,
            baseline_release_id=baseline.id,
            baseline_commit=baseline.source_commit,
            objective_revision_id=objective.id,
            diagnosis_id=diagnosis.id,
            acceptance_criteria=objective.acceptance_criteria,
            allowed_paths=objective.mutation_policy.allowed_paths,
            forbidden_paths=objective.mutation_policy.forbidden_paths,
            max_implementation_attempts=objective.mutation_policy.max_implementation_attempts,
            mandatory_gates=mandatory_gates,
            change_intent=_change_intent(diagnosis),
        )
        validate_changed_paths(guard, diagnosis.requested_paths)

        proposal = ChangeProposal(
            id=proposal_id,
            target_id=objective.target_id,
            baseline_release_id=baseline.id,
            baseline_commit=baseline.source_commit,
            objective_revision_id=objective.id,
            diagnosis_id=diagnosis.id,
            acceptance_criteria=objective.acceptance_criteria,
            allowed_paths=diagnosis.requested_paths,
            forbidden_paths=objective.mutation_policy.forbidden_paths,
            max_implementation_attempts=objective.mutation_policy.max_implementation_attempts,
            mandatory_gates=mandatory_gates,
            change_intent=_change_intent(diagnosis),
        )
        return self._repository.add(proposal)

    def get(self, proposal_id: str) -> ChangeProposal | None:
        return self._repository.get(proposal_id)


def _change_intent(diagnosis: Diagnosis) -> str:
    intent = (
        f"Observed problem: {diagnosis.observed_problem}\n"
        f"Likely root cause: {diagnosis.likely_root_cause}\n"
        f"Expected outcome: {diagnosis.expected_outcome}"
    )
    if len(intent) > 4000:
        raise ValueError("diagnosis-derived change intent exceeds V1 limit")
    return intent
