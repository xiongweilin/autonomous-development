from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from autonomous_development.adapters.postgres.operator import SqlOperatorRepository
from autonomous_development.adapters.postgres.schema import metadata
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


def _request(*, request_id: str = "request-1", digest: str = "a" * 64) -> DevelopmentRequest:
    return DevelopmentRequest(
        id=request_id,
        target_id="target-1",
        source="feishu-autodev",
        external_reference_digest="external-" + digest[:8],
        title="Bounded requirement",
        normalized_requirement_text="Add a deterministic response.",
        content_sha256=digest,
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


def test_operator_records_are_immutable_and_outbox_is_replayable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlOperatorRepository(engine)

    request = _request()
    assert repository.add_request(request) == request
    assert repository.add_request(request) == request
    with pytest.raises(ValueError, match="different content"):
        repository.add_request(_request(digest="b" * 64))

    analysis = RequirementAnalysis(
        id="analysis:request-1",
        request_id=request.id,
        summary="Add a deterministic response.",
        acceptance_criteria=("the response is stable",),
        requested_paths=("src/app.py",),
        expected_behavior=("same input returns same output",),
        risks=(),
        missing_information=(),
        ambiguity=(),
        validation_expectations=("run unit tests",),
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    assert repository.add_analysis(analysis) == analysis
    assert repository.add_analysis(analysis) == analysis

    intervention = HumanIntervention(
        id="intervention:request-1",
        request_id=request.id,
        cycle_id=None,
        kind="requirement-ambiguity",
        question="Which response format should be used?",
        choices=("json", "text"),
        status=HumanInterventionStatus.OPEN,
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    assert repository.add_intervention(intervention) == intervention
    responded = repository.respond_intervention(
        intervention.id,
        "json",
        datetime(2026, 9, 19, 0, 1, tzinfo=UTC),
    )
    assert responded.status is HumanInterventionStatus.RESPONDED
    assert repository.respond_intervention(
        intervention.id,
        "json",
        datetime(2026, 9, 19, 0, 2, tzinfo=UTC),
    ) == responded
    with pytest.raises(ValueError, match="cannot be rewritten"):
        repository.respond_intervention(
            intervention.id,
            "text",
            datetime(2026, 9, 19, 0, 3, tzinfo=UTC),
        )

    event = OperatorEvent(
        id="operator-event:requirement_received:request-1:none",
        request_id=request.id,
        cycle_id=None,
        event_type="requirement_received",
        sequence=None,
        payload={"request_id": request.id},
        created_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    stored = repository.append_event(event)
    assert stored.sequence == 1
    assert repository.append_event(event) == stored
    assert repository.list_events(after=0, limit=10) == (stored,)
    acknowledged = repository.acknowledge_event(
        event.id,
        datetime(2026, 9, 19, 0, 4, tzinfo=UTC),
    )
    assert acknowledged.acknowledged_at is not None
    assert repository.acknowledge_event(event.id, datetime(2026, 9, 19, tzinfo=UTC)) == acknowledged
    assert repository.pending_event_count() == 0
    assert repository.latest_event_sequence() == 1


def test_request_update_preserves_durable_lifecycle_fields() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    repository = SqlOperatorRepository(engine)
    request = _request()
    repository.add_request(request)
    updated = replace(
        request,
        status=DevelopmentRequestStatus.READY,
        workflow_attempt=1,
        active_workflow_id="requirement:request-1:1",
    )
    assert repository.update_request(updated) == updated
    assert repository.get_request(request.id) == updated
