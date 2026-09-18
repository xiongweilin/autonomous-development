from __future__ import annotations

from datetime import datetime
from typing import Any

from dbos import DBOS, DBOSConfiguredInstance

from autonomous_development.application.feedback_controller import (
    FeedbackIterationController,
    FeedbackIterationPlan,
)

from .autonomous_iteration import AutonomousIterationWorkflow
from .post_promotion_soak import PostPromotionSoakWorkflow


@DBOS.dbos_class()
class FeedbackAutonomyWorkflow(DBOSConfiguredInstance):
    def __init__(
        self,
        controller: FeedbackIterationController,
        execution: AutonomousIterationWorkflow,
        soak: PostPromotionSoakWorkflow,
        *,
        config_name: str = "feedback-autonomy-v1",
    ) -> None:
        self._controller = controller
        self._execution = execution
        self._soak = soak
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=20)
    def run(self, target_id: str, scheduled_time_iso: str) -> dict[str, object]:
        plan_doc = self._prepare_step(target_id, scheduled_time_iso)
        if plan_doc is None:
            return {"status": "idle", "target_id": target_id}

        plan = _plan_from_document(plan_doc)
        if not plan.executable:
            return {
                "status": "blocked",
                "target_id": target_id,
                "cycle_id": plan.cycle_id,
                "reason": plan.reason,
            }
        if plan.proposal_id is None:
            raise RuntimeError("executable feedback plan has no proposal id")

        execution = self._execution.run(
            plan.cycle_id,
            plan.proposal_id,
            f"{plan.cycle_id}:execution",
        )
        status = _string(execution, "status")
        if status != "promoted":
            return {
                "status": status,
                "target_id": target_id,
                "cycle_id": plan.cycle_id,
                "execution": execution,
            }

        soak = self._soak.run(
            plan.cycle_id,
            f"{plan.cycle_id}:soak",
        )
        return {
            "status": _string(soak, "status"),
            "target_id": target_id,
            "cycle_id": plan.cycle_id,
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
    ) -> dict[str, object] | None:
        scheduled_time = datetime.fromisoformat(scheduled_time_iso)
        plan = self._controller.prepare_next(
            target_id,
            closed_at=scheduled_time,
        )
        return _plan_document(plan) if plan is not None else None


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


def _plan_document(plan: FeedbackIterationPlan) -> dict[str, object]:
    return {
        "target_id": plan.target_id,
        "feedback_id": plan.feedback_id,
        "release_id": plan.release_id,
        "evidence_window_id": plan.evidence_window_id,
        "cycle_id": plan.cycle_id,
        "proposal_id": plan.proposal_id,
        "executable": plan.executable,
        "reason": plan.reason,
    }


def _plan_from_document(document: dict[str, object]) -> FeedbackIterationPlan:
    proposal = document.get("proposal_id")
    if proposal is not None and not isinstance(proposal, str):
        raise ValueError("feedback plan proposal id must be a string or null")
    executable = document.get("executable")
    if not isinstance(executable, bool):
        raise ValueError("feedback plan executable must be boolean")
    return FeedbackIterationPlan(
        target_id=_string(document, "target_id"),
        feedback_id=_string(document, "feedback_id"),
        release_id=_string(document, "release_id"),
        evidence_window_id=_string(document, "evidence_window_id"),
        cycle_id=_string(document, "cycle_id"),
        proposal_id=proposal,
        executable=executable,
        reason=_string(document, "reason"),
    )


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value
