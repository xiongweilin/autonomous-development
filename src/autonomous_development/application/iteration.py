from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autonomous_development.application.cycles import CycleNotFoundError, CycleService
from autonomous_development.application.diagnosis import DiagnosisService
from autonomous_development.application.proposals import ProposalService
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import (
    ChangeProposal,
    DevelopmentCycle,
    DevelopmentTarget,
    Diagnosis,
    EvidenceWindow,
    ProductObjectiveRevision,
    ReleasedVersion,
)
from autonomous_development.ports.repository import RepositoryProvider


@dataclass(frozen=True, slots=True)
class IterationPreparation:
    cycle: DevelopmentCycle
    diagnosis: Diagnosis
    proposal: ChangeProposal


class DiagnosisConfidenceInsufficient(RuntimeError):
    def __init__(
        self,
        cycle: DevelopmentCycle,
        diagnosis: Diagnosis,
        threshold: float,
    ) -> None:
        self.cycle = cycle
        self.diagnosis = diagnosis
        self.threshold = threshold
        super().__init__(
            f"diagnosis confidence {diagnosis.confidence:.3f} is below "
            f"required threshold {threshold:.3f}"
        )


class IterationService:
    def __init__(
        self,
        cycles: CycleService,
        repository: RepositoryProvider,
        diagnoses: DiagnosisService,
        proposals: ProposalService,
    ) -> None:
        self._cycles = cycles
        self._repository = repository
        self._diagnoses = diagnoses
        self._proposals = proposals

    def prepare_from_evidence(
        self,
        target: DevelopmentTarget,
        objective: ProductObjectiveRevision,
        baseline: ReleasedVersion,
        window: EvidenceWindow,
        *,
        repository_root: Path,
        cycle_id: str,
        diagnosis_id: str,
        proposal_id: str,
        mandatory_gates: tuple[str, ...],
        operation_id: str,
        minimum_diagnosis_confidence: float = 0.0,
    ) -> IterationPreparation:
        _validate_context(target, objective, baseline, window, repository_root, operation_id)
        if not 0.0 <= minimum_diagnosis_confidence <= 1.0:
            raise ValueError("minimum diagnosis confidence must be between 0 and 1")

        try:
            cycle = self._cycles.get(cycle_id)
        except CycleNotFoundError:
            cycle = self._cycles.create(
                DevelopmentCycle(
                    id=cycle_id,
                    target_id=target.id,
                    objective_revision_id=objective.id,
                    baseline_release_id=baseline.id,
                )
            )
        _validate_cycle_identity(cycle, target, objective, baseline, window)

        if cycle.change_proposal_id is not None:
            return self._load_prepared(cycle, diagnosis_id, proposal_id)

        if cycle.state is CycleState.BLOCKED and cycle.diagnosis_id is not None:
            diagnosis = self._require_diagnosis(cycle, diagnosis_id)
            if diagnosis.confidence < minimum_diagnosis_confidence:
                raise DiagnosisConfidenceInsufficient(
                    cycle,
                    diagnosis,
                    minimum_diagnosis_confidence,
                )
            raise RuntimeError("cycle is blocked despite sufficient diagnosis confidence")

        if cycle.state is CycleState.NEW:
            observed = self._repository.verify_baseline(
                repository_root,
                target.default_branch,
            )
            if observed.commit != baseline.source_commit or observed.tree != baseline.source_tree:
                raise ValueError(
                    "repository baseline does not match the serving ReleasedVersion identity"
                )
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.BASELINE_VERIFIED,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:baseline",
            )

        if cycle.state is CycleState.BASELINE_VERIFIED:
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.EVIDENCE_READY,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:evidence",
                evidence_window_id=window.id,
            )

        if cycle.state is CycleState.EVIDENCE_READY:
            diagnosis = self._diagnoses.diagnose(
                window,
                objective,
                diagnosis_id=diagnosis_id,
                repository_root=repository_root,
            )
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.DIAGNOSED,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:diagnosis",
                diagnosis_id=diagnosis.id,
            )
        else:
            diagnosis = self._require_diagnosis(cycle, diagnosis_id)

        if diagnosis.confidence < minimum_diagnosis_confidence:
            if cycle.state is CycleState.DIAGNOSED:
                cycle = self._cycles.transition(
                    cycle.id,
                    CycleState.BLOCKED,
                    expected_version=cycle.version,
                    operation_id=f"{operation_id}:low-confidence",
                )
            raise DiagnosisConfidenceInsufficient(
                cycle,
                diagnosis,
                minimum_diagnosis_confidence,
            )

        if cycle.state is CycleState.DIAGNOSED:
            proposal = self._proposals.from_diagnosis(
                diagnosis,
                objective,
                baseline,
                proposal_id=proposal_id,
                mandatory_gates=mandatory_gates,
            )
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.CHANGE_PROPOSED,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:proposal",
                change_proposal_id=proposal.id,
            )
        else:
            proposal = self._require_proposal(cycle, proposal_id)

        if cycle.state is not CycleState.CHANGE_PROPOSED:
            raise RuntimeError(
                f"iteration preparation stopped in unexpected state: {cycle.state.value}"
            )
        return IterationPreparation(cycle=cycle, diagnosis=diagnosis, proposal=proposal)

    def _load_prepared(
        self,
        cycle: DevelopmentCycle,
        diagnosis_id: str,
        proposal_id: str,
    ) -> IterationPreparation:
        diagnosis = self._require_diagnosis(cycle, diagnosis_id)
        proposal = self._require_proposal(cycle, proposal_id)
        return IterationPreparation(cycle=cycle, diagnosis=diagnosis, proposal=proposal)

    def _require_diagnosis(
        self,
        cycle: DevelopmentCycle,
        diagnosis_id: str,
    ) -> Diagnosis:
        if cycle.diagnosis_id != diagnosis_id:
            raise ValueError("cycle diagnosis identity does not match requested diagnosis")
        diagnosis = self._diagnoses.get(diagnosis_id)
        if diagnosis is None:
            raise RuntimeError("cycle references a diagnosis that is not durable")
        return diagnosis

    def _require_proposal(
        self,
        cycle: DevelopmentCycle,
        proposal_id: str,
    ) -> ChangeProposal:
        if cycle.change_proposal_id != proposal_id:
            raise ValueError("cycle proposal identity does not match requested proposal")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise RuntimeError("cycle references a proposal that is not durable")
        return proposal


def _validate_context(
    target: DevelopmentTarget,
    objective: ProductObjectiveRevision,
    baseline: ReleasedVersion,
    window: EvidenceWindow,
    repository_root: Path,
    operation_id: str,
) -> None:
    if not repository_root.is_absolute():
        raise ValueError("repository_root must be absolute")
    if not operation_id.strip():
        raise ValueError("operation_id must be non-empty")
    if target.id != objective.target_id or target.id != baseline.target_id:
        raise ValueError("target, objective and baseline identities do not match")
    if target.active_objective_revision_id != objective.id:
        raise ValueError("objective is not the target's active human-owned objective")
    if target.mutation_policy != objective.mutation_policy:
        raise ValueError("target mutation policy and objective mutation policy differ")
    if baseline.objective_revision_id != objective.id:
        raise ValueError("baseline release is governed by a different objective")
    if target.current_release_id is not None and target.current_release_id != baseline.id:
        raise ValueError("baseline is not the target's current release")
    if window.target_id != target.id or window.release_ids != (baseline.id,):
        raise ValueError("evidence window is not exclusively bound to the baseline release")


def _validate_cycle_identity(
    cycle: DevelopmentCycle,
    target: DevelopmentTarget,
    objective: ProductObjectiveRevision,
    baseline: ReleasedVersion,
    window: EvidenceWindow,
) -> None:
    if (
        cycle.target_id != target.id
        or cycle.objective_revision_id != objective.id
        or cycle.baseline_release_id != baseline.id
    ):
        raise ValueError("existing cycle identity conflicts with requested iteration")
    if cycle.evidence_window_id is not None and cycle.evidence_window_id != window.id:
        raise ValueError("existing cycle is bound to a different evidence window")
