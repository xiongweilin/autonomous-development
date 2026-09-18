from dbos import DBOS, DBOSConfig, SetWorkflowID
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.cycles import SqlCycleRepository
from autonomous_development.adapters.postgres.schema import metadata
from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.enums import CycleState
from autonomous_development.domain.models import DevelopmentCycle
from autonomous_development.workflows.development_cycle import DevelopmentCycleWorkflow


def test_dbos_workflow_id_and_transition_operation_are_idempotent(tmp_path) -> None:
    app_database = tmp_path / "app.db"
    system_database = tmp_path / "dbos.db"
    engine = create_engine(f"sqlite+pysqlite:///{app_database}")
    metadata.create_all(engine)
    service = CycleService(SqlCycleRepository(engine))
    service.create(
        DevelopmentCycle(
            id="cycle-1",
            target_id="target-1",
            objective_revision_id="objective-1",
            baseline_release_id="release-1",
        )
    )

    config: DBOSConfig = {
        "name": "autonomous-development-test",
        "application_version": "0.1.0",
        "system_database_url": f"sqlite:///{system_database}",
    }
    DBOS(config=config)
    workflow = DevelopmentCycleWorkflow(service, config_name="test-cycle-workflow")
    DBOS.launch()
    try:
        with SetWorkflowID("cycle-1:baseline"):
            first = workflow.transition(
                "cycle-1",
                CycleState.BASELINE_VERIFIED.value,
                0,
                "cycle-1:0:baseline",
            )
        with SetWorkflowID("cycle-1:baseline"):
            second = workflow.transition(
                "cycle-1",
                CycleState.BASELINE_VERIFIED.value,
                0,
                "cycle-1:0:baseline",
            )
        assert first == second
        assert service.get("cycle-1").version == 1
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=5)
        engine.dispose()
