from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfig, DBOSConfiguredInstance, SetWorkflowID

from autonomous_development.application.feedback_controller import FeedbackIterationPlan
from autonomous_development.workflows.feedback_autonomy import FeedbackAutonomyWorkflow


class FakeController:
    def __init__(self, plan: FeedbackIterationPlan | None) -> None:
        self.plan = plan
        self.calls = 0

    def prepare_next(
        self,
        target_id: str,
        *,
        closed_at: datetime,
    ) -> FeedbackIterationPlan | None:
        self.calls += 1
        assert target_id == "target-1"
        assert closed_at.tzinfo is not None
        return self.plan


@DBOS.dbos_class()
class FakeExecution(DBOSConfiguredInstance):
    def __init__(
        self,
        *,
        status: str = "promoted",
        config_name: str,
    ) -> None:
        self.status = status
        self.calls = 0
        super().__init__(config_name=config_name)

    @DBOS.workflow()
    def run(
        self,
        cycle_id: str,
        proposal_id: str,
        operation_id: str,
        model: str | None = None,
    ) -> dict[str, object]:
        del model
        self.calls += 1
        assert cycle_id == "cycle-1"
        assert proposal_id == "proposal-1"
        assert operation_id == "cycle-1:execution"
        return {"status": self.status, "cycle_id": cycle_id}


@DBOS.dbos_class()
class FakeSoak(DBOSConfiguredInstance):
    def __init__(self, *, config_name: str) -> None:
        self.calls = 0
        super().__init__(config_name=config_name)

    @DBOS.workflow()
    def run(self, cycle_id: str, operation_id: str) -> dict[str, object]:
        self.calls += 1
        assert cycle_id == "cycle-1"
        assert operation_id == "cycle-1:soak"
        return {"status": "completed", "cycle_id": cycle_id}


def executable_plan() -> FeedbackIterationPlan:
    return FeedbackIterationPlan(
        target_id="target-1",
        feedback_id="feedback-1",
        release_id="release-1",
        evidence_window_id="window-1",
        cycle_id="cycle-1",
        proposal_id="proposal-1",
        executable=True,
        reason="prepared",
    )


def blocked_plan() -> FeedbackIterationPlan:
    return FeedbackIterationPlan(
        target_id="target-1",
        feedback_id="feedback-1",
        release_id="release-1",
        evidence_window_id="window-1",
        cycle_id="cycle-1",
        proposal_id=None,
        executable=False,
        reason="low confidence",
    )


def launch(
    tmp_path: Path,
    *,
    name: str,
    plan: FeedbackIterationPlan | None,
    execution_status: str = "promoted",
) -> tuple[
    FeedbackAutonomyWorkflow,
    FakeController,
    FakeExecution,
    FakeSoak,
]:
    config: DBOSConfig = {
        "name": name,
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{tmp_path / (name + '.db')}",
    }
    DBOS(config=config)
    controller = FakeController(plan)
    execution = FakeExecution(
        status=execution_status,
        config_name=f"{name}-execution",
    )
    soak = FakeSoak(config_name=f"{name}-soak")
    workflow = FeedbackAutonomyWorkflow(
        controller,  # type: ignore[arg-type]
        execution,  # type: ignore[arg-type]
        soak,  # type: ignore[arg-type]
        config_name=f"{name}-parent",
    )
    DBOS.launch()
    return workflow, controller, execution, soak


def test_feedback_parent_workflow_replays_without_repeating_children(tmp_path: Path) -> None:
    workflow, controller, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-replay",
        plan=executable_plan(),
    )
    scheduled = datetime.now(UTC).isoformat()
    try:
        with SetWorkflowID("feedback-parent-1"):
            first = workflow.run("target-1", scheduled)
        with SetWorkflowID("feedback-parent-1"):
            second = workflow.run("target-1", scheduled)

        assert first == second
        assert first["status"] == "completed"
        assert controller.calls == 1
        assert execution.calls == 1
        assert soak.calls == 1
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_is_idle_without_trigger(tmp_path: Path) -> None:
    workflow, controller, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-idle",
        plan=None,
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result == {"status": "idle", "target_id": "target-1"}
        assert controller.calls == 1
        assert execution.calls == 0
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_stops_on_blocked_diagnosis(tmp_path: Path) -> None:
    workflow, _, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-blocked",
        plan=blocked_plan(),
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result["status"] == "blocked"
        assert result["cycle_id"] == "cycle-1"
        assert result["reason"] == "low confidence"
        assert execution.calls == 0
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_does_not_soak_rejected_execution(tmp_path: Path) -> None:
    workflow, _, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-rejected",
        plan=executable_plan(),
        execution_status="rejected",
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result["status"] == "rejected"
        assert execution.calls == 1
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
