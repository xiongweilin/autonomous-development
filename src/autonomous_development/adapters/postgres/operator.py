from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from autonomous_development.domain.enums import (
    DevelopmentRequestStatus,
    HumanInterventionStatus,
)
from autonomous_development.domain.models import (
    DevelopmentRequest,
    HumanIntervention,
    OperatorEvent,
    RequirementAnalysis,
)
from autonomous_development.ports.persistence import OperatorRepository

from .schema import development_requests, human_interventions, operator_events, requirement_analyses


class SqlOperatorRepository(OperatorRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_request(self, request: DevelopmentRequest) -> DevelopmentRequest:
        existing = self.get_request(request.id)
        if existing is not None:
            if existing.content_sha256 != request.content_sha256:
                raise ValueError("request id already exists with different content")
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(development_requests).values(**_request_values(request)))
        except IntegrityError as exc:
            by_external = self._get_by_external_digest(request.external_reference_digest)
            if by_external is not None:
                if by_external.content_sha256 != request.content_sha256:
                    raise ValueError(
                        "external reference digest already exists with different content"
                    ) from exc
                return by_external
            raise
        return request

    def get_request(self, request_id: str) -> DevelopmentRequest | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(development_requests).where(development_requests.c.id == request_id)
                )
                .mappings()
                .first()
            )
        return None if row is None else _request_from_row(row)

    def update_request(self, request: DevelopmentRequest) -> DevelopmentRequest:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(development_requests)
                .where(development_requests.c.id == request.id)
                .values(**_request_values(request))
            )
        if result.rowcount != 1:
            raise KeyError(f"unknown development request: {request.id}")
        return request

    def add_analysis(self, analysis: RequirementAnalysis) -> RequirementAnalysis:
        existing = self.get_analysis(analysis.request_id)
        if existing is not None:
            if existing != analysis:
                raise ValueError("request already has a different requirement analysis")
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(requirement_analyses).values(**_analysis_values(analysis))
                )
        except IntegrityError:
            existing = self.get_analysis(analysis.request_id)
            if existing is None or existing != analysis:
                raise
            return existing
        return analysis

    def get_analysis(self, request_id: str) -> RequirementAnalysis | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(requirement_analyses).where(
                        requirement_analyses.c.request_id == request_id
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _analysis_from_row(row)

    def add_intervention(self, intervention: HumanIntervention) -> HumanIntervention:
        existing = self.get_intervention(intervention.id)
        if existing is not None:
            if existing != intervention:
                raise ValueError("intervention id already exists with different content")
            return existing
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    insert(human_interventions).values(**_intervention_values(intervention))
                )
        except IntegrityError:
            existing = self.get_intervention(intervention.id)
            if existing is None or existing != intervention:
                raise
            return existing
        return intervention

    def get_intervention(self, intervention_id: str) -> HumanIntervention | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(human_interventions).where(human_interventions.c.id == intervention_id)
                )
                .mappings()
                .first()
            )
        return None if row is None else _intervention_from_row(row)

    def respond_intervention(
        self,
        intervention_id: str,
        response: str,
        responded_at: datetime,
    ) -> HumanIntervention:
        existing = self.get_intervention(intervention_id)
        if existing is None:
            raise KeyError(f"unknown intervention: {intervention_id}")
        if existing.status in {
            HumanInterventionStatus.RESPONDED,
            HumanInterventionStatus.CLOSED,
        }:
            if existing.response != response:
                raise ValueError("closed intervention cannot be rewritten")
            return existing
        updated = HumanIntervention(
            id=existing.id,
            request_id=existing.request_id,
            cycle_id=existing.cycle_id,
            kind=existing.kind,
            question=existing.question,
            choices=existing.choices,
            status=HumanInterventionStatus.RESPONDED,
            created_at=existing.created_at,
            response=response,
            responded_at=responded_at,
        )
        with self._engine.begin() as connection:
            connection.execute(
                update(human_interventions)
                .where(human_interventions.c.id == intervention_id)
                .values(**_intervention_values(updated))
            )
        return updated

    def append_event(self, event: OperatorEvent) -> OperatorEvent:
        existing = self._event_by_id(event.id)
        if existing is not None:
            if existing.payload != event.payload or existing.event_type != event.event_type:
                raise ValueError("operator event id already exists with different content")
            return existing
        if event.sequence is not None:
            raise ValueError("new operator events must not specify a sequence")
        with self._engine.begin() as connection:
            connection.execute(insert(operator_events).values(**_event_values(event)))
        stored = self._event_by_id(event.id)
        if stored is None:
            raise RuntimeError("operator event was not readable after insert")
        return stored

    def list_events(self, *, after: int, limit: int) -> tuple[OperatorEvent, ...]:
        if after < 0 or not 1 <= limit <= 500:
            raise ValueError("event cursor or limit is outside bounds")
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(operator_events)
                    .where(operator_events.c.sequence > after)
                    .order_by(operator_events.c.sequence)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return tuple(_event_from_row(row) for row in rows)

    def acknowledge_event(self, event_id: str, acknowledged_at: datetime) -> OperatorEvent:
        existing = self._event_by_id(event_id)
        if existing is None:
            raise KeyError(f"unknown operator event: {event_id}")
        if existing.acknowledged_at is not None:
            return existing
        with self._engine.begin() as connection:
            connection.execute(
                update(operator_events)
                .where(operator_events.c.id == event_id)
                .values(acknowledged_at=acknowledged_at)
            )
        updated = self._event_by_id(event_id)
        if updated is None:
            raise RuntimeError("operator event disappeared during acknowledgement")
        return updated

    def pending_event_count(self) -> int:
        with self._engine.connect() as connection:
            value = connection.execute(
                select(func.count())
                .select_from(operator_events)
                .where(operator_events.c.acknowledged_at.is_(None))
            ).scalar_one()
        return int(value)

    def pending_intervention_count(self) -> int:
        with self._engine.connect() as connection:
            value = connection.execute(
                select(func.count())
                .select_from(human_interventions)
                .where(human_interventions.c.status == HumanInterventionStatus.OPEN.value)
            ).scalar_one()
        return int(value)

    def latest_event_sequence(self) -> int:
        with self._engine.connect() as connection:
            value = connection.execute(select(func.max(operator_events.c.sequence))).scalar_one()
        return 0 if value is None else int(value)

    def _get_by_external_digest(self, digest: str) -> DevelopmentRequest | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    select(development_requests).where(
                        development_requests.c.external_reference_digest == digest
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _request_from_row(row)

    def _event_by_id(self, event_id: str) -> OperatorEvent | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(operator_events).where(operator_events.c.id == event_id))
                .mappings()
                .first()
            )
        return None if row is None else _event_from_row(row)


def _request_values(request: DevelopmentRequest) -> dict[str, object]:
    return {
        "id": request.id,
        "target_id": request.target_id,
        "source": request.source,
        "external_reference_digest": request.external_reference_digest,
        "title": request.title,
        "normalized_requirement_text": request.normalized_requirement_text,
        "content_sha256": request.content_sha256,
        "created_at": request.created_at,
        "status": request.status.value,
        "cycle_id": request.cycle_id,
        "pending_intervention_id": request.pending_intervention_id,
        "workflow_attempt": request.workflow_attempt,
        "active_workflow_id": request.active_workflow_id,
    }


def _analysis_values(analysis: RequirementAnalysis) -> dict[str, object]:
    return {
        "id": analysis.id,
        "request_id": analysis.request_id,
        "summary": analysis.summary,
        "acceptance_criteria_json": list(analysis.acceptance_criteria),
        "requested_paths_json": list(analysis.requested_paths),
        "expected_behavior_json": list(analysis.expected_behavior),
        "risks_json": list(analysis.risks),
        "missing_information_json": list(analysis.missing_information),
        "ambiguity_json": list(analysis.ambiguity),
        "validation_expectations_json": list(analysis.validation_expectations),
        "created_at": analysis.created_at,
    }


def _intervention_values(intervention: HumanIntervention) -> dict[str, object]:
    return {
        "id": intervention.id,
        "request_id": intervention.request_id,
        "cycle_id": intervention.cycle_id,
        "kind": intervention.kind,
        "question": intervention.question,
        "choices_json": list(intervention.choices),
        "status": intervention.status.value,
        "created_at": intervention.created_at,
        "response": intervention.response,
        "responded_at": intervention.responded_at,
    }


def _event_values(event: OperatorEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "request_id": event.request_id,
        "cycle_id": event.cycle_id,
        "event_type": event.event_type,
        "payload_json": dict(event.payload),
        "created_at": event.created_at,
        "acknowledged_at": event.acknowledged_at,
    }


def _request_from_row(row: RowMapping) -> DevelopmentRequest:
    values = dict(row)
    return DevelopmentRequest(
        id=str(values["id"]),
        target_id=str(values["target_id"]),
        source=str(values["source"]),
        external_reference_digest=str(values["external_reference_digest"]),
        title=str(values["title"]),
        normalized_requirement_text=str(values["normalized_requirement_text"]),
        content_sha256=str(values["content_sha256"]),
        created_at=_utc(values["created_at"]),
        status=DevelopmentRequestStatus(str(values["status"])),
        cycle_id=_optional_str(values.get("cycle_id")),
        pending_intervention_id=_optional_str(values.get("pending_intervention_id")),
        workflow_attempt=int(values.get("workflow_attempt") or 0),
        active_workflow_id=_optional_str(values.get("active_workflow_id")),
    )


def _analysis_from_row(row: RowMapping) -> RequirementAnalysis:
    values = dict(row)
    return RequirementAnalysis(
        id=str(values["id"]),
        request_id=str(values["request_id"]),
        summary=str(values["summary"]),
        acceptance_criteria=_strings(values["acceptance_criteria_json"]),
        requested_paths=_strings(values["requested_paths_json"]),
        expected_behavior=_strings(values["expected_behavior_json"]),
        risks=_strings(values["risks_json"]),
        missing_information=_strings(values["missing_information_json"]),
        ambiguity=_strings(values["ambiguity_json"]),
        validation_expectations=_strings(values["validation_expectations_json"]),
        created_at=_utc(values["created_at"]),
    )


def _intervention_from_row(row: RowMapping) -> HumanIntervention:
    values = dict(row)
    return HumanIntervention(
        id=str(values["id"]),
        request_id=str(values["request_id"]),
        cycle_id=_optional_str(values.get("cycle_id")),
        kind=str(values["kind"]),
        question=str(values["question"]),
        choices=_strings(values["choices_json"]),
        status=HumanInterventionStatus(str(values["status"])),
        created_at=_utc(values["created_at"]),
        response=_optional_str(values.get("response")),
        responded_at=_optional_datetime(values.get("responded_at")),
    )


def _event_from_row(row: RowMapping) -> OperatorEvent:
    values = dict(row)
    payload = values["payload_json"]
    if not isinstance(payload, dict):
        raise RuntimeError("operator event payload is malformed")
    return OperatorEvent(
        id=str(values["id"]),
        request_id=_optional_str(values.get("request_id")),
        cycle_id=_optional_str(values.get("cycle_id")),
        event_type=str(values["event_type"]),
        sequence=int(values["sequence"]),
        payload=dict(payload),
        created_at=_utc(values["created_at"]),
        acknowledged_at=_optional_datetime(values.get("acknowledged_at")),
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError("operator JSON list is malformed")
    return tuple(str(item) for item in value)


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("operator timestamp is malformed")
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _utc(value)
