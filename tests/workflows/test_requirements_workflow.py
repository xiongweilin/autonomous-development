from __future__ import annotations

from pathlib import Path

from dbos import DBOS, DBOSConfig, DBOSConfiguredInstance, SetWorkflowID

from autonomous_development.application.operator import RequirementPreparation
from autonomous_development.domain.models import DevelopmentRequest
from autonomous_development.workflows.requirements import RequirementAutonomyWorkflow


def request() -> DevelopmentRequest:
    from datetime import UTC, datetime

    return DevelopmentRequest(
        id="request-1",
        target_id="target-1",
        source="test",
        external_reference_digest="external-1",
        title="Requirement",
        normalized_requirement_text="Do the thing.",
        content_sha256="a" * 64,
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


class FakeOperator:
    def __init__(self, *, preparation_status: str = "ready") -> None:
        self.preparation_status = preparation_status
        self.prepare_calls = 0
        self.finish_calls = 0

    def prepare(self, request_id: str, *, operation_id: str) -> RequirementPreparation:
        self.prepare_calls += 1
        assert request_id == "request-1"
        assert operation_id == "workflow-1"
        if self.preparation_status != "ready":
            return RequirementPreparation(self.preparation_status, request())
        return RequirementPreparation(
            "ready",
            request(),
            cycle_id="cycle-1",
            proposal_id="proposal-1",
        )

    def finish(self, request_id: str, result: dict[str, object]) -> dict[str, object]:
        self.finish_calls += 1
        assert request_id == "request-1"
        return {"status": result["status"], "request_id": request_id}


@DBOS.dbos_class()
class RequirementFakeExecution(DBOSConfiguredInstance):
    def __init__(self, *, status: str, config_name: str) -> None:
        self.status = status
        self.calls = 0
        super().__init__(config_name=config_name)

    @DBOS.workflow()
    def run(self, cycle_id: str, proposal_id: str, operation_id: str) -> dict[str, object]:
        self.calls += 1
        assert (cycle_id, proposal_id, operation_id) == (
            "cycle-1",
            "proposal-1",
            "workflow-1:execution",
        )
        return {"status": self.status, "cycle_id": cycle_id}


@DBOS.dbos_class()
class RequirementFakeSoak(DBOSConfiguredInstance):
    def __init__(self, *, config_name: str) -> None:
        self.calls = 0
        super().__init__(config_name=config_name)

    @DBOS.workflow()
    def run(self, cycle_id: str, operation_id: str) -> dict[str, object]:
        self.calls += 1
        assert (cycle_id, operation_id) == ("cycle-1", "workflow-1:soak")
        return {"status": "completed", "cycle_id": cycle_id}


def launch(
    tmp_path: Path,
    *,
    name: str,
    preparation_status: str = "ready",
    execution_status: str = "promoted",
) -> tuple[
    RequirementAutonomyWorkflow,
    FakeOperator,
    RequirementFakeExecution,
    RequirementFakeSoak,
]:
    config: DBOSConfig = {
        "name": name,
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{tmp_path / (name + '.db')}",
    }
    DBOS(config=config)
    operator = FakeOperator(preparation_status=preparation_status)
    execution = RequirementFakeExecution(
        status=execution_status,
        config_name=f"{name}-execution",
    )
    soak = RequirementFakeSoak(config_name=f"{name}-soak")
    workflow = RequirementAutonomyWorkflow(
        operator,  # type: ignore[arg-type]
        execution,  # type: ignore[arg-type]
        soak,  # type: ignore[arg-type]
        config_name=f"{name}-parent",
    )
    DBOS.launch()
    return workflow, operator, execution, soak


def test_requirement_workflow_replays_children_once(tmp_path: Path) -> None:
    workflow, operator, execution, soak = launch(tmp_path, name="requirement-replay")
    try:
        with SetWorkflowID("workflow-1"):
            first = workflow.run("request-1", "workflow-1")
        with SetWorkflowID("workflow-1"):
            second = workflow.run("request-1", "workflow-1")
        assert first == second
        assert first["status"] == "completed"
        assert operator.prepare_calls == 1
        assert operator.finish_calls == 1
        assert execution.calls == 1
        assert soak.calls == 1
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)


def test_requirement_workflow_returns_needs_human_without_children(tmp_path: Path) -> None:
    workflow, operator, execution, soak = launch(
        tmp_path,
        name="requirement-human",
        preparation_status="needs-human",
    )
    try:
        result = workflow.run("request-1", "workflow-1")
        assert result["status"] == "needs-human"
        assert operator.finish_calls == 0
        assert execution.calls == 0
        assert soak.calls == 0
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
