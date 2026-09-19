from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonomous_development.application.soak import PostPromotionSoakService
from autonomous_development.domain.canary import CanaryGuardrails
from autonomous_development.domain.enums import (
    CycleState,
    ReleaseDecisionKind,
    SoakDecisionKind,
)
from autonomous_development.domain.models import (
    CanaryStage,
    DevelopmentCycle,
    ReleasedVersion,
)
from autonomous_development.domain.soak import PostPromotionSoakDecision
from autonomous_development.ports.deployment import DeploymentRuntime
from autonomous_development.ports.persistence import SoakDecisionReceipt


def cycle(
    state: CycleState,
    *,
    experiment_id: str | None = "experiment-1",
    candidate_deployment_id: str = "deployment-1",
) -> DevelopmentCycle:
    return DevelopmentCycle(
        id="cycle-1",
        target_id="target-1",
        objective_revision_id="objective-1",
        baseline_release_id="release-0",
        state=state,
        version=10,
        candidate_id="candidate-1",
        verification_run_id="verify-1",
        artifact_id="artifact-1",
        candidate_deployment_id=candidate_deployment_id,
        experiment_id=experiment_id,
        release_decision=ReleaseDecisionKind.PROMOTE,
    )


def release(release_id: str, deployment_id: str) -> ReleasedVersion:
    return ReleasedVersion(
        id=release_id,
        target_id="target-1",
        source_commit="a" * 40,
        source_tree="b" * 40,
        artifact_digest="sha256:" + "c" * 64,
        objective_revision_id="objective-1",
        deployment_id=deployment_id,
        promoted_at=datetime.now(UTC),
    )


def decision(kind: SoakDecisionKind) -> PostPromotionSoakDecision:
    return PostPromotionSoakDecision(
        kind=kind,
        evidence_refs=("soak:1",),
        violated_guardrails=(
            ("candidate_error_rate",)
            if kind is SoakDecisionKind.ROLLBACK
            else ()
        ),
        reason=f"{kind.value} decision",
    )


class Cycles:
    def __init__(self, value: DevelopmentCycle) -> None:
        self.value = value
        self.transitions = 0

    def get(self, cycle_id: str) -> DevelopmentCycle:
        assert cycle_id == "cycle-1"
        return self.value

    def transition(self, *args, **kwargs) -> DevelopmentCycle:
        del args, kwargs
        self.transitions += 1
        return self.value


class Releases:
    def __init__(self, serving: ReleasedVersion | None) -> None:
        self.value = serving
        self.set_calls = 0

    def serving(self, target_id: str) -> ReleasedVersion | None:
        assert target_id == "target-1"
        return self.value

    def set_serving(self, *args, **kwargs) -> ReleasedVersion:
        del args, kwargs
        self.set_calls += 1
        if self.value is None:
            raise AssertionError("set_serving requires a configured release")
        return self.value


class Decisions:
    def __init__(self, receipt: SoakDecisionReceipt | None = None) -> None:
        self.receipt = receipt

    def get(self, operation_id: str) -> SoakDecisionReceipt | None:
        del operation_id
        return self.receipt

    def record(self, *args, **kwargs) -> SoakDecisionReceipt:
        del args, kwargs
        if self.receipt is None:
            raise AssertionError("record is not expected in fail-closed tests")
        return self.receipt


class Runtime:
    def __init__(
        self,
        *,
        serving_deployment_id: str = "deployment-1",
    ) -> None:
        self.serving_deployment_id = serving_deployment_id

    def resolve(self, release_id: str) -> DeploymentRuntime:
        assert release_id == "release-0"
        return DeploymentRuntime(
            deployment_id="deployment-0",
            container_id="control",
            base_url="http://127.0.0.1:4100",
            evidence_ref="runtime:control",
        )

    def resolve_serving(self, target_id: str) -> tuple[ReleasedVersion, DeploymentRuntime]:
        assert target_id == "target-1"
        serving = release("release-1", self.serving_deployment_id)
        return (
            serving,
            DeploymentRuntime(
                deployment_id=self.serving_deployment_id,
                container_id="candidate",
                base_url="http://127.0.0.1:4200",
                evidence_ref="runtime:candidate",
            ),
        )


class SourcePromotion:
    def restore_baseline(self, **kwargs) -> None:
        del kwargs

    def cleanup_cycle(self, **kwargs) -> None:
        del kwargs


class Traffic:
    def apply(self, split):
        raise AssertionError(f"traffic should not be applied: {split}")

    def restore_control(self, **kwargs):
        raise AssertionError(f"traffic should not be restored: {kwargs}")


class Observer:
    def observe(self, **kwargs):
        raise AssertionError(f"observer should not run: {kwargs}")


def service(
    value: DevelopmentCycle,
    *,
    serving: ReleasedVersion | None = None,
    receipt: SoakDecisionReceipt | None = None,
    serving_deployment_id: str = "deployment-1",
) -> PostPromotionSoakService:
    return PostPromotionSoakService(
        Cycles(value),  # type: ignore[arg-type]
        Traffic(),  # type: ignore[arg-type]
        Observer(),  # type: ignore[arg-type]
        Releases(serving or release("release-1", "deployment-1")),  # type: ignore[arg-type]
        Decisions(receipt),  # type: ignore[arg-type]
        Runtime(serving_deployment_id=serving_deployment_id),  # type: ignore[arg-type]
        SourcePromotion(),  # type: ignore[arg-type]
        repository_root=Path.cwd().resolve(),
        worktree_root=(Path.cwd() / ".autodev-test-worktrees").resolve(),
        default_branch="main",
    )


def receipt(
    kind: SoakDecisionKind,
    *,
    cycle_id: str = "cycle-1",
) -> SoakDecisionReceipt:
    return SoakDecisionReceipt(
        operation_id="decision-1",
        cycle_id=cycle_id,
        decision=decision(kind),
    )


def test_start_is_replay_safe_when_already_soaking() -> None:
    value = cycle(CycleState.SOAKING)
    assert service(value).start("cycle-1", operation_id="start-1") == value


def test_start_requires_promoted_cycle() -> None:
    with pytest.raises(ValueError, match="requires a promoted cycle"):
        service(cycle(CycleState.NEW)).start("cycle-1", operation_id="start-1")


def test_start_requires_candidate_to_be_current_serving_release() -> None:
    with pytest.raises(ValueError, match="not the current serving release"):
        service(
            cycle(CycleState.PROMOTED),
            serving=release("release-1", "other-deployment"),
        ).start("cycle-1", operation_id="start-1")


def test_observe_replays_recorded_decision() -> None:
    stored = receipt(SoakDecisionKind.HOLD)
    observed = service(cycle(CycleState.SOAKING), receipt=stored).observe(
        "cycle-1",
        CanaryStage(100, 1, 1),
        CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
        route_operation_id="route-1",
        decision_operation_id="decision-1",
    )
    assert observed == stored.decision


def test_observe_rejects_decision_receipt_from_another_cycle() -> None:
    with pytest.raises(ValueError, match="belongs to another cycle"):
        service(
            cycle(CycleState.SOAKING),
            receipt=receipt(SoakDecisionKind.HOLD, cycle_id="cycle-2"),
        ).observe(
            "cycle-1",
            CanaryStage(100, 1, 1),
            CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
            route_operation_id="route-1",
            decision_operation_id="decision-1",
        )


@pytest.mark.parametrize(
    ("value", "stage", "match"),
    [
        (cycle(CycleState.PROMOTED), CanaryStage(100, 1, 1), "soaking cycle"),
        (
            cycle(CycleState.SOAKING, experiment_id=None),
            CanaryStage(100, 1, 1),
            "no canary experiment",
        ),
        (cycle(CycleState.SOAKING), CanaryStage(50, 1, 1), "100 percent"),
    ],
)
def test_observe_rejects_invalid_soak_context(
    value: DevelopmentCycle,
    stage: CanaryStage,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        service(value).observe(
            "cycle-1",
            stage,
            CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
            route_operation_id="route-1",
            decision_operation_id="decision-1",
        )


def test_observe_rejects_serving_release_that_is_not_candidate() -> None:
    with pytest.raises(ValueError, match="not the promoted candidate deployment"):
        service(
            cycle(CycleState.SOAKING),
            serving_deployment_id="other-deployment",
        ).observe(
            "cycle-1",
            CanaryStage(100, 1, 1),
            CanaryGuardrails(0.02, 0.01, 250.0, 1.25),
            route_operation_id="route-1",
            decision_operation_id="decision-1",
        )


def test_apply_requires_durable_decision_receipt() -> None:
    with pytest.raises(ValueError, match="not durably recorded"):
        service(cycle(CycleState.SOAKING)).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )


def test_apply_hold_requires_cycle_to_still_be_soaking() -> None:
    with pytest.raises(ValueError, match="remain soaking"):
        service(
            cycle(CycleState.PROMOTED),
            receipt=receipt(SoakDecisionKind.HOLD),
        ).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )


def test_apply_complete_is_replay_safe_for_completed_cycle() -> None:
    value = cycle(CycleState.COMPLETED)
    assert (
        service(value, receipt=receipt(SoakDecisionKind.COMPLETE)).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )
        == value
    )


def test_apply_complete_requires_soaking_cycle() -> None:
    with pytest.raises(ValueError, match="requires a soaking cycle"):
        service(
            cycle(CycleState.PROMOTED),
            receipt=receipt(SoakDecisionKind.COMPLETE),
        ).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )


def test_apply_rollback_requires_soaking_cycle() -> None:
    with pytest.raises(ValueError, match="requires a soaking cycle"):
        service(
            cycle(CycleState.PROMOTED),
            receipt=receipt(SoakDecisionKind.ROLLBACK),
        ).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )


def test_apply_rollback_requires_experiment_identity() -> None:
    with pytest.raises(ValueError, match="no experiment identity"):
        service(
            cycle(CycleState.SOAKING, experiment_id=None),
            receipt=receipt(SoakDecisionKind.ROLLBACK),
        ).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )


def test_apply_rollback_rejects_mismatched_serving_candidate() -> None:
    with pytest.raises(ValueError, match="not the promoted candidate deployment"):
        service(
            cycle(CycleState.SOAKING),
            receipt=receipt(SoakDecisionKind.ROLLBACK),
            serving_deployment_id="other-deployment",
        ).apply(
            "cycle-1",
            decision_operation_id="decision-1",
            effect_operation_id="effect-1",
        )
