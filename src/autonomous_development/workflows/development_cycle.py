from __future__ import annotations

from dbos import DBOS, DBOSConfiguredInstance

from autonomous_development.application.cycles import CycleService
from autonomous_development.domain.enums import CycleState, ReleaseDecisionKind
from autonomous_development.domain.models import DevelopmentCycle


@DBOS.dbos_class()
class DevelopmentCycleWorkflow(DBOSConfiguredInstance):
    """Durable wrapper around versioned cycle transitions."""

    def __init__(
        self,
        service: CycleService,
        *,
        config_name: str = "cycle-workflow-v1",
    ) -> None:
        self._service = service
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=10)
    def transition(
        self,
        cycle_id: str,
        to_state: str,
        expected_version: int,
        operation_id: str,
        references: dict[str, str | None] | None = None,
    ) -> dict[str, object]:
        return self._transition_step(
            cycle_id,
            to_state,
            expected_version,
            operation_id,
            references or {},
        )

    @DBOS.step(retries_allowed=False)
    def _transition_step(
        self,
        cycle_id: str,
        to_state: str,
        expected_version: int,
        operation_id: str,
        references: dict[str, str | None],
    ) -> dict[str, object]:
        decision_raw = references.get("release_decision")
        cycle = self._service.transition(
            cycle_id,
            CycleState(to_state),
            expected_version=expected_version,
            operation_id=operation_id,
            candidate_id=references.get("candidate_id"),
            verification_run_id=references.get("verification_run_id"),
            artifact_id=references.get("artifact_id"),
            candidate_deployment_id=references.get("candidate_deployment_id"),
            experiment_id=references.get("experiment_id"),
            release_decision=(
                ReleaseDecisionKind(decision_raw) if decision_raw is not None else None
            ),
        )
        return _cycle_result(cycle)


def _cycle_result(cycle: DevelopmentCycle) -> dict[str, object]:
    return {
        "id": cycle.id,
        "target_id": cycle.target_id,
        "state": cycle.state.value,
        "version": cycle.version,
        "candidate_id": cycle.candidate_id,
        "verification_run_id": cycle.verification_run_id,
        "artifact_id": cycle.artifact_id,
        "candidate_deployment_id": cycle.candidate_deployment_id,
        "experiment_id": cycle.experiment_id,
        "release_decision": cycle.release_decision.value if cycle.release_decision else None,
    }
