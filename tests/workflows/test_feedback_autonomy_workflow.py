from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfig, DBOSConfiguredInstance, SetWorkflowID

from autonomous_development.application.iteration_scheduler import FeedbackIterationResult
from autonomous_development.workflows.feedback_autonomy import FeedbackAutonomyWorkflow


class FakeScheduler:
    def __init__(self, result: FeedbackIterationResult) -> None:
        self.result = result
        self.calls = 0

    def prepare_next(
        self,
        target_id: str,
        *,
        scheduled_time: datetime,
    ) -> FeedbackIterationResult:
        self.calls += 1
        assert target_id == "target-1"
        assert scheduled_time.tzinfo is not None
        return self.result


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


def prepared_result() -> FeedbackIterationResult:
    return FeedbackIterationResult(
        status="prepared",
        target_id="target-1",
        feedback_id="feedback-1",
        cycle_id="cycle-1",
        proposal_id="proposal-1",
    )


def idle_result() -> FeedbackIterationResult:
    return FeedbackIterationResult(
        status="idle",
        target_id="target-1",
        reason="no unprocessed feedback meets the trigger policy",
    )


def blocked_result() -> FeedbackIterationResult:
    return FeedbackIterationResult(
        status="blocked-low-confidence",
        target_id="target-1",
        feedback_id="feedback-1",
        cycle_id="cycle-1",
        reason="diagnosis confidence below autonomous threshold",
    )


def launch(
    tmp_path: Path,
    *,
    name: str,
    result: FeedbackIterationResult,
    execution_status: str = "promoted",
) -> tuple[
    FeedbackAutonomyWorkflow,
    FakeScheduler,
    FakeExecution,
    FakeSoak,
]:
    config: DBOSConfig = {
        "name": name,
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{tmp_path / (name + '.db')}",
    }
    DBOS(config=config)
    scheduler = FakeScheduler(result)
    execution = FakeExecution(
        status=execution_status,
        config_name=f"{name}-execution",
    )
    soak = FakeSoak(config_name=f"{name}-soak")
    workflow = FeedbackAutonomyWorkflow(
        scheduler,  # type: ignore[arg-type]
        execution,  # type: ignore[arg-type]
        soak,  # type: ignore[arg-type]
        config_name=f"{name}-parent",
    )
    DBOS.launch()
    return workflow, scheduler, execution, soak


def test_feedback_parent_workflow_replays_without_repeating_children(tmp_path: Path) -> None:
    workflow, scheduler, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-replay",
        result=prepared_result(),
    )
    scheduled = datetime.now(UTC).isoformat()
    try:
        with SetWorkflowID("feedback-parent-1"):
            first = workflow.run("target-1", scheduled)
        with SetWorkflowID("feedback-parent-1"):
            second = workflow.run("target-1", scheduled)

        assert first == second
        assert first["status"] == "completed"
        assert scheduler.calls == 1
        assert execution.calls == 1
        assert soak.calls == 1
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_is_idle_without_trigger(tmp_path: Path) -> None:
    workflow, scheduler, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-idle",
        result=idle_result(),
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result["status"] == "idle"
        assert result["reason"] == "no unprocessed feedback meets the trigger policy"
        assert scheduler.calls == 1
        assert execution.calls == 0
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_stops_on_low_confidence_diagnosis(tmp_path: Path) -> None:
    workflow, _, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-blocked",
        result=blocked_result(),
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result["status"] == "blocked-low-confidence"
        assert result["cycle_id"] == "cycle-1"
        assert result["reason"] == "diagnosis confidence below autonomous threshold"
        assert execution.calls == 0
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_feedback_parent_workflow_does_not_soak_rejected_execution(tmp_path: Path) -> None:
    workflow, _, execution, soak = launch(
        tmp_path,
        name="feedback-autonomy-rejected",
        result=prepared_result(),
        execution_status="rejected",
    )
    try:
        result = workflow.run("target-1", datetime.now(UTC).isoformat())
        assert result["status"] == "rejected"
        assert execution.calls == 1
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
