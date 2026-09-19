from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autonomous_development.api.operator import create_operator_router
from autonomous_development.api.operator_security import OperatorAuthenticator
from autonomous_development.application.operator import OperatorStatus
from autonomous_development.domain.enums import DevelopmentRequestStatus, HumanInterventionStatus
from autonomous_development.domain.models import (
    DevelopmentRequest,
    HumanIntervention,
    OperatorEvent,
)

SECRET = "operator-secret"


def request() -> DevelopmentRequest:
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


class FakeService:
    def __init__(self) -> None:
        self.value = request()
        self.intervention = HumanIntervention(
            id="intervention-1",
            request_id="request-1",
            cycle_id=None,
            kind="clarify",
            question="Which format?",
            choices=("json", "text"),
            status=HumanInterventionStatus.OPEN,
            created_at=datetime(2026, 9, 19, tzinfo=UTC),
        )
        self.events = (
            OperatorEvent(
                id="event-1",
                request_id="request-1",
                cycle_id=None,
                event_type="requirement_received",
                sequence=1,
                payload={"request_id": "request-1"},
                created_at=datetime(2026, 9, 19, tzinfo=UTC),
            ),
        )

    def submit_request(self, **_: object) -> DevelopmentRequest:
        return self.value

    def get_request(self, request_id: str) -> DevelopmentRequest:
        assert request_id == self.value.id
        return self.value

    def status(self, request_id: str) -> OperatorStatus:
        assert request_id == self.value.id
        return OperatorStatus(self.value, "change-proposed", "release-1", self.intervention)

    def start_record(self, request_id: str) -> tuple[DevelopmentRequest, bool]:
        assert request_id == self.value.id
        self.value = replace(
            self.value,
            status=DevelopmentRequestStatus.READY,
            workflow_attempt=1,
            active_workflow_id="workflow-1",
        )
        return self.value, True

    def cancel_request(self, request_id: str) -> DevelopmentRequest:
        assert request_id == self.value.id
        self.value = replace(self.value, status=DevelopmentRequestStatus.CANCELLED)
        return self.value

    def respond_intervention(self, intervention_id: str, response: str) -> DevelopmentRequest:
        assert intervention_id == self.intervention.id
        assert response == "json"
        return self.value

    def list_events(self, *, after: int, limit: int) -> tuple[OperatorEvent, ...]:
        assert after == 0
        assert limit == 100
        return self.events

    def pending_event_count(self) -> int:
        return 1

    def acknowledge_event(self, event_id: str) -> OperatorEvent:
        assert event_id == "event-1"
        return self.events[0]


def signed(method: str, path: str, request_id: str, body: bytes = b"") -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"{timestamp}\n{request_id}\n{method}\n{path}\n{digest}".encode()
    return {
        "X-Operator-Timestamp": timestamp,
        "X-Operator-Signature": hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest(),
    }


def client() -> tuple[TestClient, FakeService]:
    service = FakeService()
    app = FastAPI()
    app.include_router(
        create_operator_router(
            service,  # type: ignore[arg-type]
            OperatorAuthenticator(SECRET),
            start_workflow=lambda request_id, workflow_id: None,
        )
    )
    return TestClient(app), service


def test_operator_router_covers_submit_status_start_cancel_intervention_and_outbox() -> None:
    http, service = client()
    body = json.dumps(
        {
            "requestId": "request-1",
            "targetId": "target-1",
            "source": "test",
            "externalReferenceDigest": "external-1",
            "title": "Requirement",
            "normalizedRequirementText": "Do the thing.",
            "contentSha256": "a" * 64,
        },
        separators=(",", ":"),
    ).encode()
    submit = http.post(
        "/v1/operator/requirements",
        content=body,
        headers=signed("POST", "/v1/operator/requirements", "request-1", body),
    )
    assert submit.status_code == 201
    assert submit.json()["requestId"] == "request-1"

    status = http.get(
        "/v1/operator/requirements/request-1",
        headers=signed("GET", "/v1/operator/requirements/request-1", "request-1"),
    )
    assert status.status_code == 200
    assert status.json()["cycleState"] == "change-proposed"
    assert status.json()["intervention"]["interventionId"] == "intervention-1"

    started = http.post(
        "/v1/operator/requirements/request-1/start",
        headers=signed("POST", "/v1/operator/requirements/request-1/start", "request-1"),
    )
    assert started.status_code == 200
    assert started.json()["workflowId"] == "workflow-1"

    cancelled = http.post(
        "/v1/operator/requirements/request-1/cancel",
        headers=signed("POST", "/v1/operator/requirements/request-1/cancel", "request-1"),
    )
    assert cancelled.status_code == 200
    assert service.value.status is DevelopmentRequestStatus.CANCELLED

    intervention_body = b'{"response":"json"}'
    responded = http.post(
        "/v1/operator/interventions/intervention-1/responses",
        content=intervention_body,
        headers=signed(
            "POST",
            "/v1/operator/interventions/intervention-1/responses",
            "intervention-1",
            intervention_body,
        ),
    )
    assert responded.status_code == 200

    event_path = "/v1/operator/events?after=0&limit=100"
    events = http.get(event_path, headers=signed("GET", event_path, "events:0:100"))
    assert events.status_code == 200
    assert events.json()["next"] == 1

    acknowledged = http.post(
        "/v1/operator/events/event-1/ack",
        headers=signed("POST", "/v1/operator/events/event-1/ack", "event-1"),
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged"] is False


def test_operator_router_rejects_invalid_hmac() -> None:
    http, _ = client()
    response = http.get(
        "/v1/operator/requirements/request-1",
        headers={"X-Operator-Timestamp": "1", "X-Operator-Signature": "bad"},
    )
    assert response.status_code == 401
