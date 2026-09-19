from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from autonomous_development.application.operator import OperatorService, OperatorStatus

from .operator_security import OperatorAuthenticationError, OperatorAuthenticator


class RequirementSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    request_id: str = Field(alias="requestId", min_length=1, max_length=160)
    target_id: str = Field(alias="targetId", min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    external_reference_digest: str = Field(
        alias="externalReferenceDigest",
        min_length=1,
        max_length=64,
    )
    title: str = Field(min_length=1, max_length=512)
    normalized_requirement_text: str = Field(
        alias="normalizedRequirementText", min_length=1, max_length=100_000
    )
    content_sha256: str = Field(alias="contentSha256", min_length=64, max_length=64)


class InterventionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    response: str = Field(min_length=1, max_length=8_000)
    choice: str | None = Field(default=None, max_length=256)


class InterventionView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    intervention_id: str = Field(alias="interventionId")
    kind: str
    question: str
    choices: tuple[str, ...]
    status: str


class RequirementView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    target_id: str = Field(alias="targetId")
    source: str
    title: str
    status: str
    cycle_id: str | None = Field(default=None, alias="cycleId")
    pending_intervention_id: str | None = Field(
        default=None,
        alias="pendingInterventionId",
    )
    workflow_id: str | None = Field(default=None, alias="workflowId")
    cycle_state: str | None = Field(default=None, alias="cycleState")
    serving_release_id: str | None = Field(default=None, alias="servingReleaseId")
    intervention: InterventionView | None = None
    created_at: datetime = Field(alias="createdAt")


def create_operator_router(
    service: OperatorService,
    authenticator: OperatorAuthenticator,
    *,
    start_workflow: Callable[[str, str], None],
) -> APIRouter:
    router = APIRouter(prefix="/v1/operator", tags=["operator"])

    @router.post(
        "/requirements",
        response_model=RequirementView,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_requirement(request: Request) -> RequirementView:
        body = await request.body()
        try:
            submission = RequirementSubmission.model_validate_json(body)
            _authenticate(authenticator, request, body, submission.request_id)
            stored = service.submit_request(
                request_id=submission.request_id,
                target_id=submission.target_id,
                source=submission.source,
                external_reference_digest=submission.external_reference_digest,
                title=submission.title,
                normalized_requirement_text=submission.normalized_requirement_text,
                content_sha256=submission.content_sha256,
            )
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="operator request conflicts with durable state",
            ) from exc
        return _view(stored)

    @router.get("/requirements/{request_id}", response_model=RequirementView)
    async def get_requirement(request_id: str, request: Request) -> RequirementView:
        try:
            _authenticate(authenticator, request, b"", request_id)
            return _status_view(service.status(request_id))
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="requirement not found") from exc

    @router.post("/requirements/{request_id}/start", response_model=RequirementView)
    async def start_requirement(request_id: str, request: Request) -> RequirementView:
        try:
            _authenticate(authenticator, request, b"", request_id)
            stored, should_start = service.start_record(request_id)
            if should_start and stored.active_workflow_id is not None:
                start_workflow(stored.id, stored.active_workflow_id)
            return _view(stored)
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="requirement not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="requirement cannot be started") from exc

    @router.post("/requirements/{request_id}/cancel", response_model=RequirementView)
    async def cancel_requirement(request_id: str, request: Request) -> RequirementView:
        try:
            _authenticate(authenticator, request, b"", request_id)
            return _view(service.cancel_request(request_id))
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="requirement not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="requirement cannot be cancelled") from exc

    @router.post(
        "/interventions/{intervention_id}/responses",
        response_model=RequirementView,
    )
    async def respond_intervention(
        intervention_id: str,
        request: Request,
    ) -> RequirementView:
        body = await request.body()
        try:
            response = InterventionResponse.model_validate_json(body)
            _authenticate(authenticator, request, body, intervention_id)
            return _view(service.respond_intervention(intervention_id, response.response))
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="intervention not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="intervention response conflicts with durable state",
            ) from exc

    @router.get("/events")
    async def events(request: Request, after: int = 0, limit: int = 100) -> dict[str, object]:
        try:
            _authenticate(authenticator, request, b"", f"events:{after}:{limit}")
            items = service.list_events(after=after, limit=limit)
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        return {
            "events": [
                {
                    "eventId": item.id,
                    "sequence": item.sequence,
                    "requestId": item.request_id,
                    "cycleId": item.cycle_id,
                    "eventType": item.event_type,
                    "payload": dict(item.payload),
                    "createdAt": item.created_at.isoformat(),
                }
                for item in items
            ],
            "next": items[-1].sequence if items and items[-1].sequence is not None else after,
            "pending": service.pending_event_count(),
        }

    @router.post("/events/{event_id}/ack")
    async def acknowledge_event(event_id: str, request: Request) -> dict[str, object]:
        try:
            _authenticate(authenticator, request, b"", event_id)
            event = service.acknowledge_event(event_id)
        except OperatorAuthenticationError as exc:
            raise HTTPException(status_code=401, detail="operator authentication failed") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="operator event not found") from exc
        return {"eventId": event.id, "acknowledged": event.acknowledged_at is not None}

    return router


def _authenticate(
    authenticator: OperatorAuthenticator,
    request: Request,
    body: bytes,
    request_id: str,
) -> None:
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    authenticator.verify(
        timestamp=request.headers.get("X-Operator-Timestamp", ""),
        request_id=request_id,
        method=request.method,
        path=path,
        body=body,
        signature=request.headers.get("X-Operator-Signature", ""),
    )


def _view(request: object) -> RequirementView:
    from autonomous_development.domain.models import DevelopmentRequest

    if not isinstance(request, DevelopmentRequest):
        raise TypeError("operator view requires a development request")
    return RequirementView(
        requestId=request.id,
        targetId=request.target_id,
        source=request.source,
        title=request.title,
        status=request.status.value,
        cycleId=request.cycle_id,
        pendingInterventionId=request.pending_intervention_id,
        workflowId=request.active_workflow_id,
        createdAt=request.created_at,
    )


def _status_view(status: OperatorStatus) -> RequirementView:
    request = status.request
    intervention = status.pending_intervention
    return RequirementView(
        requestId=request.id,
        targetId=request.target_id,
        source=request.source,
        title=request.title,
        status=request.status.value,
        cycleId=request.cycle_id,
        pendingInterventionId=request.pending_intervention_id,
        workflowId=request.active_workflow_id,
        cycleState=status.cycle_state,
        servingReleaseId=status.serving_release_id,
        intervention=(
            InterventionView(
                interventionId=intervention.id,
                kind=intervention.kind,
                question=intervention.question,
                choices=intervention.choices,
                status=intervention.status.value,
            )
            if intervention is not None
            else None
        ),
        createdAt=request.created_at,
    )
