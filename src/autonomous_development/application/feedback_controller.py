from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autonomous_development.application.cycles import CycleService
from autonomous_development.application.evidence_windows import EvidenceWindowService
from autonomous_development.application.iteration import (
    DiagnosisConfidenceInsufficient,
    IterationService,
)
from autonomous_development.application.release_catalog import ReleaseCatalogService
from autonomous_development.application.target_registry import TargetRegistryService
from autonomous_development.ports.persistence import (
    FeedbackRepository,
    FeedbackTriggerReceipt,
    FeedbackTriggerRepository,
)


@dataclass(frozen=True, slots=True)
class FeedbackIterationPlan:
    target_id: str
    feedback_id: str
    release_id: str
    evidence_window_id: str
    cycle_id: str
    proposal_id: str | None
    executable: bool
    reason: str


class FeedbackIterationController:
    def __init__(
        self,
        registry: TargetRegistryService,
        releases: ReleaseCatalogService,
        feedback: FeedbackRepository,
        windows: EvidenceWindowService,
        cycles: CycleService,
        iterations: IterationService,
        triggers: FeedbackTriggerRepository,
        *,
        minimum_feedback_severity: int,
        minimum_diagnosis_confidence: float,
        evidence_lookback_seconds: int,
        mandatory_gates: tuple[str, ...],
    ) -> None:
        if not 0 <= minimum_feedback_severity <= 5:
            raise ValueError("minimum feedback severity must be between 0 and 5")
        if not 0.0 <= minimum_diagnosis_confidence <= 1.0:
            raise ValueError("minimum diagnosis confidence must be between 0 and 1")
        if evidence_lookback_seconds < 0:
            raise ValueError("evidence lookback cannot be negative")
        if not mandatory_gates or len(set(mandatory_gates)) != len(mandatory_gates):
            raise ValueError("mandatory gates must be non-empty and unique")
        self._registry = registry
        self._releases = releases
        self._feedback = feedback
        self._windows = windows
        self._cycles = cycles
        self._iterations = iterations
        self._triggers = triggers
        self._minimum_feedback_severity = minimum_feedback_severity
        self._minimum_diagnosis_confidence = minimum_diagnosis_confidence
        self._evidence_lookback_seconds = evidence_lookback_seconds
        self._mandatory_gates = mandatory_gates

    def prepare_next(
        self,
        target_id: str,
        *,
        closed_at: datetime,
    ) -> FeedbackIterationPlan | None:
        baseline = self._releases.serving(target_id)
        if baseline is None:
            return None

        target = self._registry.runtime_target(
            target_id,
            serving_release_id=baseline.id,
        )
        objective = self._registry.get_active_objective(target)
        repository_root = Path(target.repository)
        if not repository_root.is_absolute():
            raise ValueError("registered target repository must be an absolute path")
        repository_root = repository_root.resolve()

        candidate_feedback = self._feedback.list_attributable(
            target_id,
            baseline.id,
            deployment_id=baseline.deployment_id,
            opened_at=datetime.min.replace(tzinfo=UTC),
            closed_at=closed_at,
        )
        trigger = next(
            (
                item
                for item in candidate_feedback
                if item.severity >= self._minimum_feedback_severity
                and self._triggers.get(item.id) is None
            ),
            None,
        )
        if trigger is None:
            return None

        ids = _trigger_ids(target_id, baseline.id, trigger.id)
        active = self._cycles.active_for_target(target_id)
        if active is not None and active.id != ids.cycle_id:
            return None

        contextual_opened_at = trigger.received_at - timedelta(
            seconds=self._evidence_lookback_seconds
        )
        opened_at = (
            max(baseline.promoted_at, contextual_opened_at)
            if trigger.release_id == baseline.id
            else contextual_opened_at
        )
        window = self._windows.close(
            window_id=ids.window_id,
            target_id=target_id,
            opened_at=opened_at,
            closed_at=closed_at,
            feedback_ids=(trigger.id,),
        )

        try:
            preparation = self._iterations.prepare_from_evidence(
                target,
                objective,
                baseline,
                window,
                repository_root=repository_root,
                cycle_id=ids.cycle_id,
                diagnosis_id=ids.diagnosis_id,
                proposal_id=ids.proposal_id,
                mandatory_gates=self._mandatory_gates,
                operation_id=ids.operation_id,
                minimum_diagnosis_confidence=self._minimum_diagnosis_confidence,
            )
        except DiagnosisConfidenceInsufficient as exc:
            receipt = self._triggers.record(
                FeedbackTriggerReceipt(
                    feedback_id=trigger.id,
                    target_id=target_id,
                    release_id=baseline.id,
                    evidence_window_id=window.id,
                    cycle_id=exc.cycle.id,
                    proposal_id=None,
                    outcome="blocked-low-confidence",
                )
            )
            return FeedbackIterationPlan(
                target_id=receipt.target_id,
                feedback_id=receipt.feedback_id,
                release_id=receipt.release_id,
                evidence_window_id=receipt.evidence_window_id,
                cycle_id=receipt.cycle_id,
                proposal_id=None,
                executable=False,
                reason=str(exc),
            )

        receipt = self._triggers.record(
            FeedbackTriggerReceipt(
                feedback_id=trigger.id,
                target_id=target_id,
                release_id=baseline.id,
                evidence_window_id=window.id,
                cycle_id=preparation.cycle.id,
                proposal_id=preparation.proposal.id,
                outcome="prepared",
            )
        )
        return FeedbackIterationPlan(
            target_id=receipt.target_id,
            feedback_id=receipt.feedback_id,
            release_id=receipt.release_id,
            evidence_window_id=receipt.evidence_window_id,
            cycle_id=receipt.cycle_id,
            proposal_id=receipt.proposal_id,
            executable=True,
            reason="feedback prepared for autonomous execution",
        )


@dataclass(frozen=True, slots=True)
class _TriggerIds:
    window_id: str
    cycle_id: str
    diagnosis_id: str
    proposal_id: str
    operation_id: str


def _trigger_ids(target_id: str, release_id: str, feedback_id: str) -> _TriggerIds:
    digest = hashlib.sha256(
        f"{target_id}\0{release_id}\0{feedback_id}".encode()
    ).hexdigest()[:24]
    prefix = f"feedback-{digest}"
    return _TriggerIds(
        window_id=f"{prefix}-window",
        cycle_id=f"{prefix}-cycle",
        diagnosis_id=f"{prefix}-diagnosis",
        proposal_id=f"{prefix}-proposal",
        operation_id=f"{prefix}-prepare",
    )
