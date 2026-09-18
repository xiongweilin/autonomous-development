from __future__ import annotations

from autonomous_development.domain.canary import CanaryStageDecision, advance_experiment
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import Experiment
from autonomous_development.ports.persistence import (
    ConcurrentUpdateError,
    ExperimentRepository,
    ExperimentStageReceipt,
    OperationConflictError,
)


class ExperimentNotFoundError(LookupError):
    pass


class ExperimentService:
    def __init__(self, repository: ExperimentRepository) -> None:
        self._repository = repository

    def create(self, experiment: Experiment) -> Experiment:
        return self._repository.add(experiment)

    def get(self, experiment_id: str) -> Experiment:
        experiment = self._repository.get(experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError(experiment_id)
        return experiment

    def replay_decision(
        self,
        operation_id: str,
    ) -> tuple[Experiment, CanaryStageDecision] | None:
        receipt = self._repository.get_stage_decision(operation_id)
        if receipt is None:
            return None
        experiment = self.get(receipt.experiment_id)
        if experiment.current_stage_index < receipt.result_stage_index:
            raise RuntimeError("canary decision receipt is ahead of persisted experiment")
        return experiment, _decision_from_receipt(receipt)

    def require_recorded_decision(
        self,
        experiment_id: str,
        decision: CanaryStageDecision,
    ) -> None:
        self.get(experiment_id)
        receipts = self._repository.list_stage_decisions(experiment_id)
        if not any(_decision_from_receipt(receipt) == decision for receipt in receipts):
            raise ValueError("canary decision is not present in durable experiment history")

    def promotion_history(
        self,
        experiment_id: str,
    ) -> tuple[CanaryStageDecision, ...]:
        experiment = self.get(experiment_id)
        receipts = self._repository.list_stage_decisions(experiment_id)
        history: list[CanaryStageDecision] = []
        expected_stage = 0
        final_stage = len(experiment.stages) - 1

        for receipt in receipts:
            if receipt.stage_index < expected_stage:
                continue
            if receipt.stage_index > expected_stage:
                raise RuntimeError("canary receipt history skipped an experiment stage")

            decision = _decision_from_receipt(receipt)
            if decision.kind is CanaryDecisionKind.HOLD:
                continue
            if decision.kind is CanaryDecisionKind.ROLLBACK:
                raise ValueError("canary experiment contains a rollback decision")

            expected_kind = (
                CanaryDecisionKind.PROMOTION_READY
                if expected_stage == final_stage
                else CanaryDecisionKind.ADVANCE
            )
            if decision.kind is not expected_kind:
                raise ValueError("canary experiment history is not promotion-complete")

            history.append(decision)
            if expected_stage == final_stage:
                return tuple(history)
            expected_stage += 1

        raise ValueError("canary experiment history is incomplete")

    def record_decision(
        self,
        experiment_id: str,
        decision: CanaryStageDecision,
        *,
        expected_stage_index: int,
        operation_id: str,
    ) -> Experiment:
        existing = self._repository.get_stage_decision(operation_id)
        if existing is not None:
            _validate_receipt(existing, decision, expected_stage_index)
            experiment = self.get(experiment_id)
            if experiment.current_stage_index < existing.result_stage_index:
                raise RuntimeError("canary decision receipt is ahead of persisted experiment")
            return experiment

        current = self.get(experiment_id)
        if current.current_stage_index != expected_stage_index:
            raise ConcurrentUpdateError(
                f"experiment {experiment_id} is at stage "
                f"{current.current_stage_index}, not {expected_stage_index}"
            )
        updated = (
            advance_experiment(current, decision)
            if decision.kind is CanaryDecisionKind.ADVANCE
            else current
        )
        self._repository.commit_stage_decision(
            updated,
            decision,
            expected_stage_index=expected_stage_index,
            operation_id=operation_id,
        )
        return updated


def _validate_receipt(
    receipt: ExperimentStageReceipt,
    decision: CanaryStageDecision,
    expected_stage_index: int,
) -> None:
    expected_result = (
        decision.next_stage_index
        if decision.kind is CanaryDecisionKind.ADVANCE
        else expected_stage_index
    )
    if (
        receipt.experiment_id != decision.experiment_id
        or receipt.stage_index != expected_stage_index
        or receipt.result_stage_index != expected_result
        or receipt.decision_kind is not decision.kind
        or receipt.evidence_refs != decision.evidence_refs
        or receipt.violated_guardrails != decision.violated_guardrails
        or receipt.reason != decision.reason
    ):
        raise OperationConflictError(
            f"operation id {receipt.operation_id} is already bound to another canary decision"
        )


def _decision_from_receipt(receipt: ExperimentStageReceipt) -> CanaryStageDecision:
    return CanaryStageDecision(
        kind=receipt.decision_kind,
        experiment_id=receipt.experiment_id,
        stage_index=receipt.stage_index,
        next_stage_index=(
            receipt.result_stage_index
            if receipt.decision_kind is CanaryDecisionKind.ADVANCE
            else None
        ),
        evidence_refs=receipt.evidence_refs,
        violated_guardrails=receipt.violated_guardrails,
        reason=receipt.reason,
    )
