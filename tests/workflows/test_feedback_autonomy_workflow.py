from datetime import UTC, datetime
from pathlib import Path

from dbos import DBOS, DBOSConfig, DBOSConfiguredInstance, SetWorkflowID

from autonomous_development.application.feedback_controller import FeedbackIterationPlan
from autonomous_development.workflows.feedback_autonomy import FeedbackAutonomyWorkflow


class FakeController:
    def __init__(self) -> None:
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
        return FeedbackIterationPlan(
            target_id=target_id,
            feedback_id="feedback-1",
            release_id="release-1",
            evidence_window_id="window-1",
            cycle_id="cycle-1",
            proposal_id="proposal-1",
            executable=True,
            reason="prepared",
        )


@DBOS.dbos_class()
class FakeExecution(DBOSConfiguredInstance):
    def __init__(self) -> None:
        self.calls = 0
        super().__init__(config_name="fake-feedback-execution")

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
        return {"status": "promoted", "cycle_id": cycle_id}


@DBOS.dbos_class()
class FakeSoak(DBOSConfiguredInstance):
    def __init__(self) -> None:
        self.calls = 0
        super().__init__(config_name="fake-feedback-soak")

    @DBOS.workflow()
    def run(self, cycle_id: str, operation_id: str) -> dict[str, object]:
        self.calls += 1
        assert cycle_id == "cycle-1"
        assert operation_id == "cycle-1:soak"
        return {"status": "completed", "cycle_id": cycle_id}


def test_feedback_parent_workflow_replays_without_repeating_children(tmp_path: Path) -> None:
    system_database = tmp_path / "dbos.db"
    config: DBOSConfig = {
        "name": "feedback-autonomy-test",
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{system_database}",
    }
    DBOS(config=config)
    controller = FakeController()
    execution = FakeExecution()
    soak = FakeSoak()
    workflow = FeedbackAutonomyWorkflow(
        controller,  # type: ignore[arg-type]
        execution,  # type: ignore[arg-type]
        soak,  # type: ignore[arg-type]
        config_name="test-feedback-autonomy",
    )
    DBOS.launch()
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
