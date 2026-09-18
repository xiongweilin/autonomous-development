from __future__ import annotations

from dataclasses import dataclass

from .canary import (
    CanaryGuardrails,
    CanaryStageEvidence,
    stage_guardrail_violations,
    stage_insufficient_reasons,
)
from .enums import SoakDecisionKind
from .models import CanaryStage


@dataclass(frozen=True, slots=True)
class PostPromotionSoakDecision:
    kind: SoakDecisionKind
    evidence_refs: tuple[str, ...]
    violated_guardrails: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.evidence_refs:
            raise ValueError("soak decision requires evidence refs")
        if not self.reason.strip():
            raise ValueError("soak decision reason must be non-empty")
        if (
            self.kind is SoakDecisionKind.ROLLBACK
            and not self.violated_guardrails
        ):
            raise ValueError("rollback soak decision requires violated guardrails")
        if (
            self.kind is not SoakDecisionKind.ROLLBACK
            and self.violated_guardrails
        ):
            raise ValueError("non-rollback soak decision cannot carry guardrail violations")


def evaluate_post_promotion_soak(
    stage: CanaryStage,
    evidence: CanaryStageEvidence,
    guardrails: CanaryGuardrails,
) -> PostPromotionSoakDecision:
    if stage.weight_percent != 100:
        raise ValueError("post-promotion soak requires a 100 percent stage")
    if evidence.weight_percent != 100:
        raise ValueError("post-promotion soak evidence must observe 100 percent candidate traffic")

    violations = stage_guardrail_violations(evidence, guardrails)
    if violations:
        return PostPromotionSoakDecision(
            kind=SoakDecisionKind.ROLLBACK,
            evidence_refs=evidence.evidence_refs,
            violated_guardrails=violations,
            reason="post-promotion hard regression exceeded configured guardrails",
        )

    insufficient = stage_insufficient_reasons(stage, evidence)
    if insufficient:
        return PostPromotionSoakDecision(
            kind=SoakDecisionKind.HOLD,
            evidence_refs=evidence.evidence_refs,
            violated_guardrails=(),
            reason="insufficient post-promotion soak evidence: " + ", ".join(insufficient),
        )

    return PostPromotionSoakDecision(
        kind=SoakDecisionKind.COMPLETE,
        evidence_refs=evidence.evidence_refs,
        violated_guardrails=(),
        reason="post-promotion soak satisfied evidence and hard guardrail requirements",
    )
