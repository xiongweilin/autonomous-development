from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from autonomous_development.application.cycles import CycleService
from autonomous_development.application.proposals import ProposalService
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.requirements import RequirementAnalysisService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.enums import (
    CycleState,
    DevelopmentRequestStatus,
    HumanInterventionStatus,
)
from autonomous_development.domain.models import (
    ChangeProposal,
    DevelopmentCycle,
    DevelopmentRequest,
    HumanIntervention,
    OperatorEvent,
    ProductObjectiveRevision,
    ReleasedVersion,
    RequirementAnalysis,
)
from autonomous_development.ports.persistence import OperatorRepository
from autonomous_development.ports.repository import RepositoryProvider
from autonomous_development.ports.target_contract import TargetContractLoader


@dataclass(frozen=True, slots=True)
class RequirementPreparation:
    status: str
    request: DevelopmentRequest
    cycle_id: str | None = None
    proposal_id: str | None = None
    intervention_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class OperatorStatus:
    request: DevelopmentRequest
    cycle_state: str | None
    serving_release_id: str | None
    pending_intervention: HumanIntervention | None


class OperatorService:
    def __init__(
        self,
        repository: OperatorRepository,
        *,
        cycles: CycleService,
        proposals: ProposalService,
        targets: TargetRegistryService,
        releases: ReleaseCatalogService,
        requirements: RequirementAnalysisService,
        target_contracts: TargetContractLoader,
        source_repository: RepositoryProvider,
        repository_root: Path,
        mandatory_gate_ids: tuple[str, ...],
    ) -> None:
        self._repository = repository
        self._cycles = cycles
        self._proposals = proposals
        self._targets = targets
        self._releases = releases
        self._requirements = requirements
        self._target_contracts = target_contracts
        self._source_repository = source_repository
        self._repository_root = repository_root
        self._mandatory_gate_ids = tuple(dict.fromkeys((*mandatory_gate_ids, "performance")))

    def submit_request(
        self,
        *,
        request_id: str,
        target_id: str,
        source: str,
        external_reference_digest: str,
        title: str,
        normalized_requirement_text: str,
        content_sha256: str,
        created_at: datetime | None = None,
    ) -> DevelopmentRequest:
        existing = self._repository.get_request(request_id)
        if existing is not None:
            if (
                existing.content_sha256 != content_sha256
                or existing.target_id != target_id
                or existing.source != source
                or existing.external_reference_digest != external_reference_digest
                or existing.title != title
                or existing.normalized_requirement_text != normalized_requirement_text
            ):
                raise ValueError("request id was reused with different immutable content")
            stored = existing
        else:
            request = DevelopmentRequest(
                id=request_id,
                target_id=target_id,
                source=source,
                external_reference_digest=external_reference_digest,
                title=title,
                normalized_requirement_text=normalized_requirement_text,
                content_sha256=content_sha256,
                created_at=created_at or datetime.now(UTC),
            )
            stored = self._repository.add_request(request)
            if stored.id != request_id:
                raise ValueError("external reference digest belongs to another request")
        self._emit(
            request_id=stored.id,
            cycle_id=None,
            event_type="requirement_received",
            payload={
                "request_id": stored.id,
                "target_id": stored.target_id,
                "title": stored.title,
                "content_sha256_prefix": stored.content_sha256[:12],
            },
        )
        return stored

    def status(self, request_id: str) -> OperatorStatus:
        request = self.get_request(request_id)
        cycle_state: str | None = None
        if request.cycle_id is not None:
            try:
                cycle = self._cycles.get(request.cycle_id)
            except LookupError:
                cycle = None
            if cycle is not None:
                cycle_state = cycle.state.value
        serving = self._releases.serving(request.target_id)
        intervention = (
            self._repository.get_intervention(request.pending_intervention_id)
            if request.pending_intervention_id
            else None
        )
        return OperatorStatus(
            request=request,
            cycle_state=cycle_state,
            serving_release_id=serving.id if serving is not None else None,
            pending_intervention=intervention,
        )

    def get_request(self, request_id: str) -> DevelopmentRequest:
        request = self._repository.get_request(request_id)
        if request is None:
            raise KeyError(f"unknown development request: {request_id}")
        return request

    def respond_intervention(
        self,
        intervention_id: str,
        response: str,
        *,
        responded_at: datetime | None = None,
    ) -> DevelopmentRequest:
        intervention = self._repository.respond_intervention(
            intervention_id,
            response.strip(),
            responded_at or datetime.now(UTC),
        )
        request = self.get_request(intervention.request_id)
        updated = replace(
            request,
            status=DevelopmentRequestStatus.READY,
            pending_intervention_id=None,
            active_workflow_id=None,
        )
        self._repository.update_request(updated)
        self._emit(
            request_id=updated.id,
            cycle_id=updated.cycle_id,
            event_type="requirement_ready",
            payload={"request_id": updated.id, "intervention_id": intervention.id},
        )
        return updated

    def prepare(self, request_id: str, *, operation_id: str) -> RequirementPreparation:
        request = self.get_request(request_id)
        if request.status in {
            DevelopmentRequestStatus.COMPLETED,
            DevelopmentRequestStatus.ROLLED_BACK,
            DevelopmentRequestStatus.FAILED,
            DevelopmentRequestStatus.CANCELLED,
        }:
            return RequirementPreparation(request.status.value, request, request.cycle_id)
        if request.status is DevelopmentRequestStatus.NEEDS_HUMAN:
            intervention = (
                self._repository.get_intervention(request.pending_intervention_id)
                if request.pending_intervention_id
                else None
            )
            if intervention is not None and intervention.status is HumanInterventionStatus.OPEN:
                return RequirementPreparation(
                    "needs-human",
                    request,
                    request.cycle_id,
                    intervention_id=intervention.id,
                    reason=intervention.question,
                )

        analyzing = replace(request, status=DevelopmentRequestStatus.ANALYZING)
        self._repository.update_request(analyzing)
        target = self._targets.get_target(request.target_id)
        baseline = self._releases.serving(target.id)
        if baseline is None:
            return self._needs_human(
                analyzing,
                kind="missing-baseline",
                question=(
                    "The registered target has no serving baseline release. Restore or "
                    "bootstrap the target before starting this requirement."
                ),
                choices=("restore-baseline", "cancel"),
            )
        repository_root = Path(target.repository).expanduser().resolve(strict=True)
        contract = self._target_contracts.load(str(repository_root))
        if contract.target_id != target.id:
            return self._needs_human(
                analyzing,
                kind="target-contract-mismatch",
                question=(
                    "The active target contract identity does not match the registered target."
                ),
                choices=("repair-target-registration", "cancel"),
            )
        try:
            observed = self._source_repository.verify_baseline(
                repository_root,
                target.default_branch,
            )
        except Exception as exc:
            return self._needs_human(
                analyzing,
                kind="baseline-recovery",
                question=(
                    "The target repository baseline could not be verified safely. Human "
                    f"recovery is required before autonomous implementation ({type(exc).__name__})."
                ),
                choices=("restore-baseline", "cancel"),
            )
        if observed.commit != baseline.source_commit or observed.tree != baseline.source_tree:
            return self._needs_human(
                analyzing,
                kind="baseline-recovery",
                question=(
                    "The target repository does not match the serving baseline. Human "
                    "recovery is required before autonomous implementation."
                ),
                choices=("restore-baseline", "cancel"),
            )
        try:
            analysis = self._requirements.analyze(
                request,
                self._targets.get_active_objective(target),
                baseline,
                contract,
                repository_root=repository_root,
            )
        except Exception as exc:
            return self._needs_human(
                analyzing,
                kind="requirement-analysis-failed",
                question=(
                    "The read-only requirement analysis could not be completed safely. "
                    f"Human review or retry is required ({type(exc).__name__})."
                ),
                choices=("retry-analysis", "cancel"),
            )
        if not analysis.actionable:
            return self._needs_human(
                analyzing,
                kind="requirement-ambiguity",
                question=_intervention_question(analysis),
                choices=("clarify-requirement", "cancel"),
            )
        active = self._cycles.active_for_target(target.id)
        if active is not None and active.id != request.cycle_id:
            return self._needs_human(
                analyzing,
                kind="active-cycle-conflict",
                question="The single registered target already has an active development cycle.",
                choices=("wait-for-active-cycle", "cancel"),
            )

        objective = self._targets.get_active_objective(target)
        cycle_id = request.cycle_id or _cycle_id_for_request(request.id)
        proposal_id = f"proposal:request:{request.id}"
        cycle = self._cycles.get(cycle_id) if request.cycle_id else None
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            proposal = self._proposals.add(
                _proposal_from_requirement(
                    request,
                    analysis,
                    objective,
                    baseline,
                    mandatory_gates=self._mandatory_gate_ids,
                )
            )
        if cycle is None:
            cycle = self._cycles.create(
                DevelopmentCycle(
                    id=cycle_id,
                    target_id=target.id,
                    objective_revision_id=objective.id,
                    baseline_release_id=baseline.id,
                )
            )
        if cycle.state is CycleState.NEW:
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.BASELINE_VERIFIED,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:baseline",
            )
        if cycle.state is CycleState.BASELINE_VERIFIED:
            cycle = self._cycles.transition(
                cycle.id,
                CycleState.CHANGE_PROPOSED,
                expected_version=cycle.version,
                operation_id=f"{operation_id}:proposal",
                change_proposal_id=proposal.id,
            )
        updated = replace(
            analyzing,
            status=DevelopmentRequestStatus.RUNNING,
            cycle_id=cycle.id,
            pending_intervention_id=None,
        )
        self._repository.update_request(updated)
        self._emit(
            request_id=updated.id,
            cycle_id=cycle.id,
            event_type="development_started",
            payload={"request_id": updated.id, "cycle_id": cycle.id},
        )
        return RequirementPreparation("ready", updated, cycle.id, proposal.id)

    def finish(
        self,
        request_id: str,
        result: Mapping[str, object],
    ) -> dict[str, object]:
        request = self.get_request(request_id)
        status = _terminal_status(result)
        updated = replace(request, status=status, active_workflow_id=None)
        self._repository.update_request(updated)
        event_type = {
            DevelopmentRequestStatus.COMPLETED: "completed",
            DevelopmentRequestStatus.ROLLED_BACK: "rolled_back",
            DevelopmentRequestStatus.FAILED: "failed",
            DevelopmentRequestStatus.CANCELLED: "cancelled",
        }[status]
        payload: dict[str, object] = {
            "request_id": updated.id,
            "cycle_id": updated.cycle_id,
            "status": status.value,
        }
        _copy_safe_string(result, payload, "reason", "reason")
        execution = result.get("execution")
        if isinstance(execution, dict):
            _copy_safe_string(execution, payload, "release", "release")
            _copy_safe_string(execution, payload, "status", "execution_status")
            release = execution.get("release")
            if isinstance(release, dict):
                _copy_safe_string(release, payload, "id", "release_id")
                _copy_safe_string(release, payload, "source_commit", "source_commit")
            candidate = execution.get("candidate")
            if isinstance(candidate, dict):
                _copy_safe_string(candidate, payload, "candidate_commit", "candidate_commit")
                changed_paths = candidate.get("changed_paths")
                if isinstance(changed_paths, list):
                    payload["changed_paths_count"] = min(len(changed_paths), 1000)
        soak = result.get("soak")
        if isinstance(soak, dict):
            _copy_safe_string(soak, payload, "status", "soak_status")
        self._emit(
            request_id=updated.id,
            cycle_id=updated.cycle_id,
            event_type=event_type,
            payload=payload,
        )
        return {"status": status.value, "request_id": updated.id, "cycle_id": updated.cycle_id}

    def cancel_request(self, request_id: str) -> DevelopmentRequest:
        request = self.get_request(request_id)
        if request.status in {
            DevelopmentRequestStatus.COMPLETED,
            DevelopmentRequestStatus.ROLLED_BACK,
            DevelopmentRequestStatus.FAILED,
            DevelopmentRequestStatus.CANCELLED,
        }:
            return request
        if request.status is DevelopmentRequestStatus.RUNNING:
            raise ValueError("running development cycle is not safely cancellable")
        updated = replace(
            request,
            status=DevelopmentRequestStatus.CANCELLED,
            active_workflow_id=None,
        )
        self._repository.update_request(updated)
        self._emit(
            request_id=updated.id,
            cycle_id=updated.cycle_id,
            event_type="cancelled",
            payload={"request_id": updated.id, "cycle_id": updated.cycle_id},
        )
        return updated

    def start_record(self, request_id: str) -> tuple[DevelopmentRequest, bool]:
        request = self.get_request(request_id)
        if request.status is DevelopmentRequestStatus.NEEDS_HUMAN:
            raise ValueError("request is waiting for human intervention")
        if request.status in {
            DevelopmentRequestStatus.COMPLETED,
            DevelopmentRequestStatus.ROLLED_BACK,
            DevelopmentRequestStatus.FAILED,
            DevelopmentRequestStatus.CANCELLED,
        }:
            return request, False
        if request.active_workflow_id:
            return request, False
        workflow_attempt = request.workflow_attempt + 1
        workflow_id = f"requirement:{request.id}:{workflow_attempt}"
        updated = replace(
            request,
            status=DevelopmentRequestStatus.READY,
            workflow_attempt=workflow_attempt,
            active_workflow_id=workflow_id,
        )
        self._repository.update_request(updated)
        return updated, True

    def list_events(self, *, after: int, limit: int) -> tuple[OperatorEvent, ...]:
        return self._repository.list_events(after=after, limit=limit)

    def acknowledge_event(self, event_id: str) -> OperatorEvent:
        return self._repository.acknowledge_event(event_id, datetime.now(UTC))

    def pending_event_count(self) -> int:
        return self._repository.pending_event_count()

    def pending_intervention_count(self) -> int:
        return self._repository.pending_intervention_count()

    def latest_event_sequence(self) -> int:
        return self._repository.latest_event_sequence()

    def _needs_human(
        self,
        request: DevelopmentRequest,
        *,
        kind: str,
        question: str,
        choices: tuple[str, ...],
    ) -> RequirementPreparation:
        intervention_id = f"intervention:{request.id}"
        existing = self._repository.get_intervention(intervention_id)
        if existing is None:
            existing = self._repository.add_intervention(
                HumanIntervention(
                    id=intervention_id,
                    request_id=request.id,
                    cycle_id=request.cycle_id,
                    kind=kind,
                    question=question,
                    choices=choices,
                    status=HumanInterventionStatus.OPEN,
                    created_at=datetime.now(UTC),
                )
            )
        updated = replace(
            request,
            status=DevelopmentRequestStatus.NEEDS_HUMAN,
            pending_intervention_id=existing.id,
            active_workflow_id=None,
        )
        self._repository.update_request(updated)
        self._emit(
            request_id=updated.id,
            cycle_id=updated.cycle_id,
            event_type="needs_human",
            payload={
                "request_id": updated.id,
                "cycle_id": updated.cycle_id,
                "intervention_id": existing.id,
                "kind": existing.kind,
                "question": existing.question,
                "choices": list(existing.choices),
            },
        )
        return RequirementPreparation(
            "needs-human",
            updated,
            updated.cycle_id,
            intervention_id=existing.id,
            reason=existing.question,
        )

    def _emit(
        self,
        *,
        request_id: str | None,
        cycle_id: str | None,
        event_type: str,
        payload: Mapping[str, object],
    ) -> OperatorEvent:
        event_id = f"operator-event:{event_type}:{request_id or 'global'}:{cycle_id or 'none'}"
        return self._repository.append_event(
            OperatorEvent(
                id=event_id,
                request_id=request_id,
                cycle_id=cycle_id,
                event_type=event_type,
                sequence=None,
                payload=dict(payload),
                created_at=datetime.now(UTC),
            )
        )


def _proposal_from_requirement(
    request: DevelopmentRequest,
    analysis: RequirementAnalysis,
    objective: ProductObjectiveRevision,
    baseline: ReleasedVersion,
    *,
    mandatory_gates: tuple[str, ...],
) -> ChangeProposal:
    acceptance = tuple(
        dict.fromkeys((*objective.acceptance_criteria, *analysis.acceptance_criteria))
    )
    intent = (
        f"Human requirement: {analysis.summary}\n"
        f"Expected behavior: {'; '.join(analysis.expected_behavior)}\n"
        f"Validation expectations: {'; '.join(analysis.validation_expectations)}"
    )[:4000]
    return ChangeProposal(
        id=f"proposal:request:{request.id}",
        target_id=objective.target_id,
        baseline_release_id=baseline.id,
        baseline_commit=baseline.source_commit,
        objective_revision_id=objective.id,
        diagnosis_id=None,
        acceptance_criteria=acceptance,
        allowed_paths=analysis.requested_paths,
        forbidden_paths=objective.mutation_policy.forbidden_paths,
        max_implementation_attempts=objective.mutation_policy.max_implementation_attempts,
        mandatory_gates=mandatory_gates,
        change_intent=intent,
        max_changed_files=objective.mutation_policy.max_changed_files,
    )


def _cycle_id_for_request(request_id: str) -> str:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:32]
    return f"cycle-request-{digest}"


def _intervention_question(analysis: RequirementAnalysis) -> str:
    details = list(analysis.missing_information) + list(analysis.ambiguity)
    return (
        "The requirement needs clarification before it can enter autonomous implementation: "
        + "; ".join(details[:4])
    )


def _terminal_status(result: Mapping[str, object]) -> DevelopmentRequestStatus:
    status = result.get("status")
    if status in {"completed", "promoted"}:
        return DevelopmentRequestStatus.COMPLETED
    if status in {"rolled-back", "rollback", "rolled_back"}:
        return DevelopmentRequestStatus.ROLLED_BACK
    if status in {"cancelled", "canceled"}:
        return DevelopmentRequestStatus.CANCELLED
    return DevelopmentRequestStatus.FAILED


def _copy_safe_string(
    source: Mapping[str, object],
    target: dict[str, object],
    source_key: str,
    target_key: str,
) -> None:
    value = source.get(source_key)
    if isinstance(value, str) and value:
        target[target_key] = value[:256]
