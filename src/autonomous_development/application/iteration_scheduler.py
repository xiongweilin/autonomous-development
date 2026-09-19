from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from autonomous_development.application.cycles import CycleService
from autonomous_development.application.evidence_windows import EvidenceWindowService
from autonomous_development.application.iteration import (
    DiagnosisConfidenceInsufficient,
    IterationService,
)
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.domain.models import UserFeedback
from autonomous_development.ports.persistence import (
    FeedbackRepository,
    FeedbackTriggerReceipt,
    FeedbackTriggerRepository,
)
from autonomous_development.ports.target_contract import TargetContractLoader


@dataclass(frozen=True, slots=True)
class FeedbackIterationPolicy:
    minimum_severity: int = 3
    diagnosis_delay_seconds: int = 300
    evidence_lookback_seconds: int = 900
    minimum_diagnosis_confidence: float = 0.65

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_severity <= 5:
            raise ValueError("minimum feedback severity must be between 0 and 5")
        if self.diagnosis_delay_seconds < 0:
            raise ValueError("diagnosis delay cannot be negative")
        if self.evidence_lookback_seconds < 0:
            raise ValueError("evidence lookback cannot be negative")
        if not 0.0 <= self.minimum_diagnosis_confidence <= 1.0:
            raise ValueError("minimum diagnosis confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class FeedbackIterationResult:
    status: str
    target_id: str
    feedback_id: str | None = None
    cycle_id: str | None = None
    proposal_id: str | None = None
    reason: str | None = None

    @property
    def prepared(self) -> bool:
        return self.status == "prepared"


class FeedbackIterationSchedulerService:
    def __init__(
        self,
        *,
        cycles: CycleService,
        releases: ReleaseCatalogService,
        targets: TargetRegistryService,
        feedback: FeedbackRepository,
        triggers: FeedbackTriggerRepository,
        evidence: EvidenceWindowService,
        iterations: IterationService,
        contracts: TargetContractLoader,
        policy: FeedbackIterationPolicy,
    ) -> None:
        self._cycles = cycles
        self._releases = releases
        self._targets = targets
        self._feedback = feedback
        self._triggers = triggers
        self._evidence = evidence
        self._iterations = iterations
        self._contracts = contracts
        self._policy = policy

    def prepare_next(
        self,
        target_id: str,
        *,
        scheduled_time: datetime,
    ) -> FeedbackIterationResult:
        if scheduled_time.tzinfo is None:
            raise ValueError("scheduled_time must be timezone-aware")

        active = self._cycles.active_for_target(target_id)
        serving = self._releases.serving(target_id)
        if serving is None:
            return FeedbackIterationResult(
                status="idle",
                target_id=target_id,
                reason="target has no serving release",
            )

        eligible_until = scheduled_time - timedelta(
            seconds=self._policy.diagnosis_delay_seconds
        )
        if eligible_until < serving.promoted_at:
            return FeedbackIterationResult(
                status="idle",
                target_id=target_id,
                reason="no feedback is old enough for deterministic diagnosis",
            )

        candidates = self._feedback.list_attributable(
            target_id,
            serving.id,
            opened_at=serving.promoted_at,
            closed_at=eligible_until,
        )
        feedback = self._select_feedback(candidates)
        if feedback is None:
            if active is not None:
                return FeedbackIterationResult(
                    status="idle",
                    target_id=target_id,
                    cycle_id=active.id,
                    reason=f"active cycle {active.id} is {active.state.value}",
                )
            return FeedbackIterationResult(
                status="idle",
                target_id=target_id,
                reason="no unprocessed feedback meets the trigger policy",
            )

        identities = _identities(target_id, serving.id, feedback.id)
        if active is not None and active.id != identities.cycle_id:
            return FeedbackIterationResult(
                status="idle",
                target_id=target_id,
                feedback_id=feedback.id,
                cycle_id=active.id,
                reason=f"active cycle {active.id} is {active.state.value}",
            )
        opened_at = max(
            serving.promoted_at,
            feedback.received_at
            - timedelta(seconds=self._policy.evidence_lookback_seconds),
        )
        closed_at = feedback.received_at + timedelta(
            seconds=self._policy.diagnosis_delay_seconds
        )
        window = self._evidence.close(
            window_id=identities.window_id,
            target_id=target_id,
            opened_at=opened_at,
            closed_at=closed_at,
            feedback_ids=(feedback.id,),
        )

        target = self._targets.runtime_target(
            target_id,
            serving_release_id=serving.id,
        )
        objective = self._targets.get_active_objective(target)
        repository_root = Path(target.repository).expanduser().resolve(strict=True)
        contract = self._contracts.load(str(repository_root))
        if contract.target_id != target.id:
            raise ValueError("target contract identity does not match registered target")
        if contract.revision != target.target_contract_revision:
            raise ValueError("target contract revision does not match registered target")

        try:
            prepared = self._iterations.prepare_from_evidence(
                target,
                objective,
                serving,
                window,
                repository_root=repository_root,
                cycle_id=identities.cycle_id,
                diagnosis_id=identities.diagnosis_id,
                proposal_id=identities.proposal_id,
                mandatory_gates=contract.mandatory_gates,
                operation_id=identities.operation_id,
                minimum_diagnosis_confidence=(
                    self._policy.minimum_diagnosis_confidence
                ),
            )
        except DiagnosisConfidenceInsufficient as exc:
            receipt = self._triggers.record(
                FeedbackTriggerReceipt(
                    feedback_id=feedback.id,
                    target_id=target_id,
                    release_id=serving.id,
                    evidence_window_id=window.id,
                    cycle_id=exc.cycle.id,
                    proposal_id=None,
                    outcome="blocked-low-confidence",
                )
            )
            return FeedbackIterationResult(
                status=receipt.outcome,
                target_id=target_id,
                feedback_id=feedback.id,
                cycle_id=receipt.cycle_id,
                reason=str(exc),
            )

        receipt = self._triggers.record(
            FeedbackTriggerReceipt(
                feedback_id=feedback.id,
                target_id=target_id,
                release_id=serving.id,
                evidence_window_id=window.id,
                cycle_id=prepared.cycle.id,
                proposal_id=prepared.proposal.id,
                outcome="prepared",
            )
        )
        return FeedbackIterationResult(
            status=receipt.outcome,
            target_id=target_id,
            feedback_id=receipt.feedback_id,
            cycle_id=receipt.cycle_id,
            proposal_id=receipt.proposal_id,
        )

    def _select_feedback(
        self,
        candidates: tuple[UserFeedback, ...],
    ) -> UserFeedback | None:
        for feedback in candidates:
            if feedback.severity < self._policy.minimum_severity:
                continue
            if self._triggers.get(feedback.id) is None:
                return feedback
        return None


@dataclass(frozen=True, slots=True)
class _IterationIdentities:
    window_id: str
    cycle_id: str
    diagnosis_id: str
    proposal_id: str
    operation_id: str


def _identities(target_id: str, release_id: str, feedback_id: str) -> _IterationIdentities:
    digest = hashlib.sha256(
        f"{target_id}\0{release_id}\0{feedback_id}".encode()
    ).hexdigest()[:24]
    prefix = f"feedback-{digest}"
    return _IterationIdentities(
        window_id=f"{prefix}-window",
        cycle_id=f"{prefix}-cycle",
        diagnosis_id=f"{prefix}-diagnosis",
        proposal_id=f"{prefix}-proposal",
        operation_id=f"{prefix}-prepare",
    )
