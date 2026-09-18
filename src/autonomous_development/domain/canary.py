from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .enums import CanaryDecisionKind
from .models import CanaryStage, Experiment


@dataclass(frozen=True, slots=True)
class CanaryGuardrails:
    max_candidate_error_rate: float
    max_error_rate_delta: float
    max_candidate_p95_latency_ms: float
    max_p95_latency_ratio: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.max_candidate_error_rate <= 1.0:
            raise ValueError("max candidate error rate must be between 0 and 1")
        if not 0.0 <= self.max_error_rate_delta <= 1.0:
            raise ValueError("max error rate delta must be between 0 and 1")
        if self.max_candidate_p95_latency_ms <= 0:
            raise ValueError("max candidate p95 latency must be positive")
        if self.max_p95_latency_ratio < 1.0:
            raise ValueError("max p95 latency ratio must be at least 1")


@dataclass(frozen=True, slots=True)
class CanaryStageEvidence:
    experiment_id: str
    stage_index: int
    weight_percent: int
    observed_duration_seconds: int
    total_requests: int
    candidate_requests: int
    control_requests: int
    candidate_error_rate: float
    control_error_rate: float | None
    candidate_p95_latency_ms: float
    control_p95_latency_ms: float | None
    evidence_refs: tuple[str, ...]
    telemetry_complete: bool = True

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must be non-empty")
        if self.stage_index < 0:
            raise ValueError("stage_index cannot be negative")
        if not 1 <= self.weight_percent <= 100:
            raise ValueError("weight_percent must be between 1 and 100")
        for label, value in (
            ("observed duration", self.observed_duration_seconds),
            ("total requests", self.total_requests),
            ("candidate requests", self.candidate_requests),
            ("control requests", self.control_requests),
        ):
            if value < 0:
                raise ValueError(f"{label} cannot be negative")
        if self.candidate_requests + self.control_requests != self.total_requests:
            raise ValueError("candidate and control request counts must equal total requests")
        if not 0.0 <= self.candidate_error_rate <= 1.0:
            raise ValueError("candidate error rate must be between 0 and 1")
        if self.control_error_rate is not None and not 0.0 <= self.control_error_rate <= 1.0:
            raise ValueError("control error rate must be between 0 and 1")
        if self.candidate_p95_latency_ms < 0:
            raise ValueError("candidate p95 latency cannot be negative")
        if self.control_p95_latency_ms is not None and self.control_p95_latency_ms < 0:
            raise ValueError("control p95 latency cannot be negative")
        if not self.evidence_refs:
            raise ValueError("canary stage evidence requires reconstructable evidence refs")


@dataclass(frozen=True, slots=True)
class CanaryStageDecision:
    kind: CanaryDecisionKind
    experiment_id: str
    stage_index: int
    next_stage_index: int | None
    evidence_refs: tuple[str, ...]
    violated_guardrails: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.reason.strip():
            raise ValueError("canary decision identity and reason must be non-empty")
        if self.stage_index < 0:
            raise ValueError("canary decision stage cannot be negative")
        if self.kind is CanaryDecisionKind.ADVANCE and self.next_stage_index is None:
            raise ValueError("advance decision requires next stage")
        if self.kind is not CanaryDecisionKind.ADVANCE and self.next_stage_index is not None:
            raise ValueError("non-advance decision cannot carry next stage")
        if not self.evidence_refs:
            raise ValueError("canary decision requires evidence refs")


def evaluate_canary_stage(
    experiment: Experiment,
    evidence: CanaryStageEvidence,
    guardrails: CanaryGuardrails,
) -> CanaryStageDecision:
    if evidence.experiment_id != experiment.id:
        raise ValueError("canary evidence belongs to another experiment")
    if evidence.stage_index != experiment.current_stage_index:
        raise ValueError("canary evidence does not match current experiment stage")
    stage = experiment.stages[experiment.current_stage_index]
    if evidence.weight_percent != stage.weight_percent:
        raise ValueError("observed canary weight does not match configured stage")

    violations = stage_guardrail_violations(evidence, guardrails)
    if violations:
        return CanaryStageDecision(
            kind=CanaryDecisionKind.ROLLBACK,
            experiment_id=experiment.id,
            stage_index=evidence.stage_index,
            next_stage_index=None,
            evidence_refs=evidence.evidence_refs,
            violated_guardrails=violations,
            reason="hard canary regression exceeded configured guardrails",
        )

    insufficient = stage_insufficient_reasons(stage, evidence)
    if insufficient:
        return CanaryStageDecision(
            kind=CanaryDecisionKind.HOLD,
            experiment_id=experiment.id,
            stage_index=evidence.stage_index,
            next_stage_index=None,
            evidence_refs=evidence.evidence_refs,
            violated_guardrails=(),
            reason="insufficient canary evidence: " + ", ".join(insufficient),
        )

    if experiment.current_stage_index == len(experiment.stages) - 1:
        return CanaryStageDecision(
            kind=CanaryDecisionKind.PROMOTION_READY,
            experiment_id=experiment.id,
            stage_index=evidence.stage_index,
            next_stage_index=None,
            evidence_refs=evidence.evidence_refs,
            violated_guardrails=(),
            reason="final canary stage satisfied evidence and guardrail requirements",
        )

    return CanaryStageDecision(
        kind=CanaryDecisionKind.ADVANCE,
        experiment_id=experiment.id,
        stage_index=evidence.stage_index,
        next_stage_index=experiment.current_stage_index + 1,
        evidence_refs=evidence.evidence_refs,
        violated_guardrails=(),
        reason="canary stage satisfied evidence and guardrail requirements",
    )


def advance_experiment(
    experiment: Experiment,
    decision: CanaryStageDecision,
) -> Experiment:
    if decision.experiment_id != experiment.id:
        raise ValueError("canary decision belongs to another experiment")
    if decision.stage_index != experiment.current_stage_index:
        raise ValueError("canary decision is stale for the experiment")
    if decision.kind is not CanaryDecisionKind.ADVANCE or decision.next_stage_index is None:
        raise ValueError("only an advance decision can move experiment stage")
    if decision.next_stage_index != experiment.current_stage_index + 1:
        raise ValueError("canary decision attempts to skip an experiment stage")
    return replace(experiment, current_stage_index=decision.next_stage_index)


def stage_guardrail_violations(
    evidence: CanaryStageEvidence,
    guardrails: CanaryGuardrails,
) -> tuple[str, ...]:
    violations: list[str] = []
    if evidence.candidate_error_rate > guardrails.max_candidate_error_rate:
        violations.append("candidate_error_rate")
    if evidence.candidate_p95_latency_ms > guardrails.max_candidate_p95_latency_ms:
        violations.append("candidate_p95_latency_ms")

    if (
        evidence.control_requests > 0
        and evidence.control_error_rate is not None
        and evidence.control_p95_latency_ms is not None
        and evidence.control_p95_latency_ms > 0
    ):
        if (
            evidence.candidate_error_rate - evidence.control_error_rate
            > guardrails.max_error_rate_delta
        ):
            violations.append("error_rate_delta")
        if (
            evidence.candidate_p95_latency_ms / evidence.control_p95_latency_ms
            > guardrails.max_p95_latency_ratio
        ):
            violations.append("p95_latency_ratio")
    return tuple(violations)


def stage_insufficient_reasons(
    stage: CanaryStage,
    evidence: CanaryStageEvidence,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not evidence.telemetry_complete:
        reasons.append("telemetry_incomplete")
    if evidence.observed_duration_seconds < stage.min_duration_seconds:
        reasons.append("duration")
    if evidence.total_requests < stage.min_requests:
        reasons.append("total_requests")

    minimum_candidate = max(1, math.ceil(stage.min_requests * stage.weight_percent / 100))
    if evidence.candidate_requests < minimum_candidate:
        reasons.append("candidate_requests")

    if stage.weight_percent < 100:
        minimum_control = max(
            1,
            math.ceil(stage.min_requests * (100 - stage.weight_percent) / 100),
        )
        if evidence.control_requests < minimum_control:
            reasons.append("control_requests")
        if (
            evidence.control_error_rate is None
            or evidence.control_p95_latency_ms is None
            or evidence.control_p95_latency_ms <= 0
        ):
            reasons.append("control_metrics")
    return tuple(reasons)


def promotion_evidence_refs(
    experiment: Experiment,
    decisions: tuple[CanaryStageDecision, ...],
) -> tuple[str, ...]:
    if len(decisions) != len(experiment.stages):
        raise ValueError("canary decision history must contain every configured stage")
    refs: list[str] = []
    for index, decision in enumerate(decisions):
        if decision.experiment_id != experiment.id or decision.stage_index != index:
            raise ValueError("canary decision history is not ordered for this experiment")
        expected = (
            CanaryDecisionKind.PROMOTION_READY
            if index == len(decisions) - 1
            else CanaryDecisionKind.ADVANCE
        )
        if decision.kind is not expected:
            raise ValueError("canary decision history is not promotion-complete")
        refs.extend(decision.evidence_refs)
    if not refs:
        raise ValueError("canary promotion history has no evidence")
    return tuple(refs)
