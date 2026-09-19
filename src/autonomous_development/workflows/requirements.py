from __future__ import annotations

from dbos import DBOS, DBOSConfiguredInstance, SetWorkflowID

from autonomous_development.application.operator import OperatorService

from .autonomous_iteration import AutonomousIterationWorkflow
from .post_promotion_soak import PostPromotionSoakWorkflow


@DBOS.dbos_class()
class RequirementAutonomyWorkflow(DBOSConfiguredInstance):
    """Turn one durable human requirement into the existing V1 lifecycle."""

    def __init__(
        self,
        operator: OperatorService,
        execution: AutonomousIterationWorkflow,
        soak: PostPromotionSoakWorkflow,
        *,
        config_name: str = "requirement-autonomy-v1",
    ) -> None:
        self._operator = operator
        self._execution = execution
        self._soak = soak
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=20)
    def run(self, request_id: str, operation_id: str) -> dict[str, object]:
        preparation = self._prepare_step(request_id, operation_id)
        if preparation["status"] != "ready":
            return preparation
        cycle_id = _required_string(preparation.get("cycle_id"), "cycle_id")
        proposal_id = _required_string(preparation.get("proposal_id"), "proposal_id")
        execution = self._execution.run(
            cycle_id,
            proposal_id,
            f"{operation_id}:execution",
        )
        execution_status = execution.get("status")
        if execution_status != "promoted":
            return self._finish_step(
                request_id,
                {"status": execution_status, "execution": execution},
            )
        soak = self._soak.run(cycle_id, f"{operation_id}:soak")
        return self._finish_step(
            request_id,
            {"status": soak.get("status"), "execution": execution, "soak": soak},
        )

    @DBOS.step(
        retries_allowed=True,
        max_attempts=3,
        interval_seconds=1.0,
        backoff_rate=2.0,
    )
    def _prepare_step(self, request_id: str, operation_id: str) -> dict[str, object]:
        prepared = self._operator.prepare(request_id, operation_id=operation_id)
        result: dict[str, object] = {
            "status": prepared.status,
            "request_id": prepared.request.id,
            "cycle_id": prepared.cycle_id,
            "proposal_id": prepared.proposal_id,
            "intervention_id": prepared.intervention_id,
        }
        if prepared.reason is not None:
            result["reason"] = prepared.reason[:1000]
        return result

    @DBOS.step(retries_allowed=False)
    def _finish_step(self, request_id: str, result: dict[str, object]) -> dict[str, object]:
        return self._operator.finish(request_id, result)


def start_requirement_workflow(
    workflow: RequirementAutonomyWorkflow,
    *,
    request_id: str,
    workflow_id: str,
) -> None:
    with SetWorkflowID(workflow_id):
        DBOS.start_workflow(workflow.run, request_id, workflow_id)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value
