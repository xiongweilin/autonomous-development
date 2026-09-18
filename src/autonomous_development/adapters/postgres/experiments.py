from __future__ import annotations

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.canary import CanaryStageDecision
from autonomous_development.domain.enums import CanaryDecisionKind
from autonomous_development.domain.models import CanaryStage, Experiment
from autonomous_development.ports.persistence import (
    ConcurrentUpdateError,
    ExperimentRepository,
    ExperimentStageReceipt,
    OperationConflictError,
)

from .schema import experiment_stage_operations, experiments


class SqlExperimentRepository(ExperimentRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, experiment: Experiment) -> Experiment:
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(experiments).values(**_experiment_values(experiment)))
        except IntegrityError as exc:
            if self.get(experiment.id) is not None:
                raise ValueError(f"experiment already exists: {experiment.id}") from exc
            raise
        return experiment

    def get(self, experiment_id: str) -> Experiment | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(experiments).where(experiments.c.id == experiment_id)
                )
                .mappings()
                .first()
            )
        return _experiment_from_row(row) if row is not None else None

    def get_stage_decision(self, operation_id: str) -> ExperimentStageReceipt | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(experiment_stage_operations).where(
                        experiment_stage_operations.c.operation_id == operation_id
                    )
                )
                .mappings()
                .first()
            )
        return _receipt_from_row(row) if row is not None else None

    def commit_stage_decision(
        self,
        experiment: Experiment,
        decision: CanaryStageDecision,
        *,
        expected_stage_index: int,
        operation_id: str,
    ) -> ExperimentStageReceipt:
        if decision.experiment_id != experiment.id:
            raise ValueError("canary decision belongs to another experiment")
        if decision.stage_index != expected_stage_index:
            raise ValueError("canary decision stage does not match expected stage")

        receipt = ExperimentStageReceipt(
            operation_id=operation_id,
            experiment_id=experiment.id,
            stage_index=expected_stage_index,
            result_stage_index=experiment.current_stage_index,
            decision_kind=decision.kind,
            evidence_refs=decision.evidence_refs,
            violated_guardrails=decision.violated_guardrails,
            reason=decision.reason,
        )
        existing = self.get_stage_decision(operation_id)
        if existing is not None:
            return _validate_existing_receipt(existing, receipt)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(experiment_stage_operations).values(**_receipt_values(receipt))
                )
                result = connection.execute(
                    update(experiments)
                    .where(
                        experiments.c.id == experiment.id,
                        experiments.c.current_stage_index == expected_stage_index,
                    )
                    .values(current_stage_index=experiment.current_stage_index)
                )
                if result.rowcount != 1:
                    raise ConcurrentUpdateError(
                        f"experiment {experiment.id} no longer matches stage "
                        f"{expected_stage_index}"
                    )
        except IntegrityError as exc:
            existing = self.get_stage_decision(operation_id)
            if existing is None:
                raise
            try:
                return _validate_existing_receipt(existing, receipt)
            except OperationConflictError as conflict:
                raise conflict from exc
        return receipt


def _validate_existing_receipt(
    existing: ExperimentStageReceipt,
    requested: ExperimentStageReceipt,
) -> ExperimentStageReceipt:
    if existing != requested:
        raise OperationConflictError(
            f"operation id {requested.operation_id} is already bound to another canary decision"
        )
    return existing


def _experiment_values(experiment: Experiment) -> dict[str, object]:
    return {
        "id": experiment.id,
        "target_id": experiment.target_id,
        "control_release_id": experiment.control_release_id,
        "candidate_deployment_id": experiment.candidate_deployment_id,
        "stages_json": [
            {
                "weight_percent": stage.weight_percent,
                "min_duration_seconds": stage.min_duration_seconds,
                "min_requests": stage.min_requests,
            }
            for stage in experiment.stages
        ],
        "current_stage_index": experiment.current_stage_index,
    }


def _receipt_values(receipt: ExperimentStageReceipt) -> dict[str, object]:
    return {
        "operation_id": receipt.operation_id,
        "experiment_id": receipt.experiment_id,
        "stage_index": receipt.stage_index,
        "result_stage_index": receipt.result_stage_index,
        "decision_kind": receipt.decision_kind.value,
        "evidence_refs_json": list(receipt.evidence_refs),
        "violated_guardrails_json": list(receipt.violated_guardrails),
        "reason": receipt.reason,
    }


def _experiment_from_row(row: RowMapping) -> Experiment:
    values = dict(row)
    raw_stages = values["stages_json"]
    if not isinstance(raw_stages, list):
        raise RuntimeError("persisted experiment stages are malformed")
    stages: list[CanaryStage] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            raise RuntimeError("persisted experiment stage is malformed")
        stages.append(
            CanaryStage(
                weight_percent=int(item["weight_percent"]),
                min_duration_seconds=int(item["min_duration_seconds"]),
                min_requests=int(item["min_requests"]),
            )
        )
    return Experiment(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        control_release_id=str(values["control_release_id"]),
        candidate_deployment_id=str(values["candidate_deployment_id"]),
        stages=tuple(stages),
        current_stage_index=int(values["current_stage_index"]),
    )


def _receipt_from_row(row: RowMapping) -> ExperimentStageReceipt:
    values = dict(row)
    evidence_refs = values["evidence_refs_json"]
    violated = values["violated_guardrails_json"]
    if not isinstance(evidence_refs, list) or not isinstance(violated, list):
        raise RuntimeError("persisted canary decision receipt is malformed")
    return ExperimentStageReceipt(
        operation_id=str(values["operation_id"]),
        experiment_id=str(values["experiment_id"]),
        stage_index=int(values["stage_index"]),
        result_stage_index=int(values["result_stage_index"]),
        decision_kind=CanaryDecisionKind(str(values["decision_kind"])),
        evidence_refs=tuple(str(item) for item in evidence_refs),
        violated_guardrails=tuple(str(item) for item in violated),
        reason=str(values["reason"]),
    )
