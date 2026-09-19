from __future__ import annotations

from dbos import DBOS, DBOSConfiguredInstance

from autonomous_development.application.soak import PostPromotionSoakService
from autonomous_development.domain.canary import CanaryGuardrails
from autonomous_development.domain.enums import SoakDecisionKind
from autonomous_development.domain.models import CanaryStage, DevelopmentCycle
from autonomous_development.domain.soak import PostPromotionSoakDecision


@DBOS.dbos_class()
class PostPromotionSoakWorkflow(DBOSConfiguredInstance):
    def __init__(
        self,
        service: PostPromotionSoakService,
        *,
        stage: CanaryStage,
        guardrails: CanaryGuardrails,
        hold_sleep_seconds: float = 30.0,
        config_name: str = "post-promotion-soak-v1",
    ) -> None:
        if stage.weight_percent != 100:
            raise ValueError("post-promotion soak workflow requires a 100 percent stage")
        if hold_sleep_seconds <= 0:
            raise ValueError("soak hold sleep must be positive")
        self._service = service
        self._stage = stage
        self._guardrails = guardrails
        self._hold_sleep_seconds = hold_sleep_seconds
        super().__init__(config_name=config_name)

    @DBOS.workflow(max_recovery_attempts=20)
    def run(self, cycle_id: str, operation_id: str) -> dict[str, object]:
        if not cycle_id.strip() or not operation_id.strip():
            raise ValueError("cycle and operation ids must be non-empty")
        self._start_step(cycle_id, f"{operation_id}:start")
        round_index = 0

        while True:
            decision_operation_id = f"{operation_id}:decision:{round_index}"
            try:
                decision_doc = self._observe_step(
                    cycle_id,
                    route_operation_id=f"{operation_id}:route",
                    decision_operation_id=decision_operation_id,
                )
            except Exception as exc:
                cycle_doc = self._rollback_unobserved_step(
                    cycle_id,
                    effect_operation_id=f"{operation_id}:unobserved-rollback",
                )
                return {
                    "cycle": cycle_doc,
                    "decision": None,
                    "status": _string(cycle_doc, "state"),
                    "reason": f"soak observation failed closed: {type(exc).__name__}",
                }
            decision = _decision_from_document(decision_doc)
            cycle_doc = self._apply_step(
                cycle_id,
                decision_operation_id=decision_operation_id,
                effect_operation_id=f"{operation_id}:effect:{round_index}",
            )
            if decision.kind is SoakDecisionKind.HOLD:
                round_index += 1
                DBOS.sleep(self._hold_sleep_seconds)
                continue
            return {
                "cycle": cycle_doc,
                "decision": decision_doc,
                "status": _string(cycle_doc, "state"),
            }

    @DBOS.step(retries_allowed=False)
    def _start_step(self, cycle_id: str, operation_id: str) -> dict[str, object]:
        return _cycle_document(self._service.start(cycle_id, operation_id=operation_id))

    @DBOS.step(retries_allowed=False)
    def _observe_step(
        self,
        cycle_id: str,
        *,
        route_operation_id: str,
        decision_operation_id: str,
    ) -> dict[str, object]:
        decision = self._service.observe(
            cycle_id,
            self._stage,
            self._guardrails,
            route_operation_id=route_operation_id,
            decision_operation_id=decision_operation_id,
        )
        return _decision_document(decision)

    @DBOS.step(retries_allowed=False)
    def _rollback_unobserved_step(
        self,
        cycle_id: str,
        *,
        effect_operation_id: str,
    ) -> dict[str, object]:
        cycle = self._service.rollback_unobserved(
            cycle_id,
            effect_operation_id=effect_operation_id,
        )
        return _cycle_document(cycle)

    @DBOS.step(retries_allowed=False)
    def _apply_step(
        self,
        cycle_id: str,
        *,
        decision_operation_id: str,
        effect_operation_id: str,
    ) -> dict[str, object]:
        cycle = self._service.apply(
            cycle_id,
            decision_operation_id=decision_operation_id,
            effect_operation_id=effect_operation_id,
        )
        return _cycle_document(cycle)


def _decision_document(decision: PostPromotionSoakDecision) -> dict[str, object]:
    return {
        "kind": decision.kind.value,
        "evidence_refs": list(decision.evidence_refs),
        "violated_guardrails": list(decision.violated_guardrails),
        "reason": decision.reason,
    }


def _decision_from_document(document: dict[str, object]) -> PostPromotionSoakDecision:
    return PostPromotionSoakDecision(
        kind=SoakDecisionKind(_string(document, "kind")),
        evidence_refs=_strings(document, "evidence_refs"),
        violated_guardrails=_strings(document, "violated_guardrails"),
        reason=_string(document, "reason"),
    )


def _cycle_document(cycle: DevelopmentCycle) -> dict[str, object]:
    return {
        "id": cycle.id,
        "state": cycle.state.value,
        "version": cycle.version,
    }


def _string(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strings(document: dict[str, object], key: str) -> tuple[str, ...]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a string array")
    return tuple(value)
