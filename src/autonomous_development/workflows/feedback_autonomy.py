from __future__ import annotations

from datetime import datetime
from typing import Any

from dbos import DBOS, DBOSConfiguredInstance

from autonomous_development.application.iteration_scheduler import (
    FeedbackIterationResult,
    FeedbackIterationSchedulerService,
)

from .autonomous_iteration import AutonomousIterationWorkflow
from .post_promotion_soak import PostPromotionSoakWorkflow


@DBOS.dbos_class()
class FeedbackAutonomyWorkflow(DBOSConfiguredInstance):
    def __init__(
        self,
        scheduler: FeedbackIterationSchedulerService,
        execution: AutonomousIterationWorkflow,
        soak: PostPromotionSoakWorkflow,
        *,
        config_name: str = "feedback-autonomy-v1",
    ) -> None:
        self._scheduler = scheduler
        self._execution = execution
        self._soak = soak
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=20)
    def run(self, target_id: str, scheduled_time_iso: str) -> dict[str, object]:
        result_doc = self._prepare_step(target_id, scheduled_time_iso)
        result = _result_from_document(result_doc)
        if not result.prepared:
            response: dict[str, object] = {
                "status": result.status,
                "target_id": target_id,
            }
            if result.feedback_id is not None:
                response["feedback_id"] = result.feedback_id
            if result.cycle_id is not None:
                response["cycle_id"] = result.cycle_id
            if result.reason is not None:
                response["reason"] = result.reason
            return response

        if result.cycle_id is None or result.proposal_id is None:
            raise RuntimeError("prepared feedback iteration is missing cycle or proposal identity")

        execution = self._execution.run(
            result.cycle_id,
            result.proposal_id,
            f"{result.cycle_id}:execution",
        )
        status = _string(execution, "status")
        if status != "promoted":
            return {
                "status": status,
                "target_id": target_id,
                "cycle_id": result.cycle_id,
                "execution": execution,
            }

        soak = self._soak.run(
            result.cycle_id,
            f"{result.cycle_id}:soak",
        )
        return {
            "status": _string(soak, "status"),
            "target_id": target_id,
            "cycle_id": result.cycle_id,
            "execution": execution,
            "soak": soak,
        }

    @DBOS.step(
        retries_allowed=True,
        max_attempts=3,
        interval_seconds=1.0,
        backoff_rate=2.0,
    )
    def _prepare_step(
        self,
        target_id: str,
        scheduled_time_iso: str,
    ) -> dict[str, object]:
        scheduled_time = datetime.fromisoformat(scheduled_time_iso)
        result = self._scheduler.prepare_next(
            target_id,
            scheduled_time=scheduled_time,
        )
        return _result_document(result)


_SCHEDULED_WORKFLOW: FeedbackAutonomyWorkflow | None = None


def bind_scheduled_feedback_workflow(workflow: FeedbackAutonomyWorkflow) -> None:
    global _SCHEDULED_WORKFLOW
    if _SCHEDULED_WORKFLOW is not None and _SCHEDULED_WORKFLOW is not workflow:
        raise RuntimeError("scheduled feedback workflow is already bound")
    _SCHEDULED_WORKFLOW = workflow


@DBOS.workflow(max_recovery_attempts=20)
def scheduled_feedback_tick(
    scheduled_time: datetime,
    context: Any,
) -> dict[str, object]:
    workflow = _SCHEDULED_WORKFLOW
    if workflow is None:
        raise RuntimeError("scheduled feedback workflow has not been bound")
    if not isinstance(context, str) or not context.strip():
        raise ValueError("scheduled feedback context must be a target id")
    return workflow.run(context, scheduled_time.isoformat())


def _result_document(result: FeedbackIterationResult) -> dict[str, object]:
    return {
        "status": result.status,
        "target_id": result.target_id,
        "feedback_id": result.feedback_id,
        "cycle_id": result.cycle_id,
        "proposal_id": result.proposal_id,
        "reason": result.reason,
    }


def _result_from_document(document: dict[str, object]) -> FeedbackIterationResult:
    return FeedbackIterationResult(
        status=_string(document, "status"),
        target_id=_string(document, "target_id"),
        feedback_id=_optional_string(document.get("feedback_id")),
        cycle_id=_optional_string(document.get("cycle_id")),
        proposal_id=_optional_string(document.get("proposal_id")),
        reason=_optional_string(document.get("reason")),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional feedback iteration field must be a string or null")
    return value


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value
