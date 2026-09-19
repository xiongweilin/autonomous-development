from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autonomous_development.application.feedback import (
    FeedbackAttributionError,
    FeedbackService,
    FeedbackSubmission,
)
from autonomous_development.domain.enums import FeedbackKind


class FeedbackRequest(BaseModel):
    feedback_id: str = Field(min_length=1, max_length=128)
    kind: FeedbackKind
    category: str = Field(min_length=1, max_length=128)
    severity: int = Field(ge=0, le=5)
    request_ref: str | None = Field(default=None, max_length=256)
    free_text: str | None = Field(default=None, max_length=4000)
    reported_release_id: str | None = Field(default=None, max_length=128)
    reported_deployment_id: str | None = Field(default=None, max_length=128)


class FeedbackResponse(BaseModel):
    feedback_id: str
    target_id: str
    release_id: str | None
    deployment_id: str | None
    experiment_id: str | None
    received_at: datetime


def create_feedback_router(service: FeedbackService) -> APIRouter:
    router = APIRouter(prefix="/v1/targets", tags=["feedback"])

    @router.post("/{target_id}/feedback", response_model=FeedbackResponse, status_code=201)
    def ingest_feedback(target_id: str, request: FeedbackRequest) -> FeedbackResponse:
        try:
            feedback = service.ingest(
                FeedbackSubmission(
                    id=request.feedback_id,
                    target_id=target_id,
                    received_at=datetime.now(UTC),
                    kind=request.kind,
                    category=request.category,
                    severity=request.severity,
                    provenance="feedback-api",
                    request_ref=request.request_ref,
                    free_text=request.free_text,
                    reported_release_id=request.reported_release_id,
                    reported_deployment_id=request.reported_deployment_id,
                )
            )
        except FeedbackAttributionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not feedback.attributable:
            raise RuntimeError("feedback API accepted unattributable feedback")
        return FeedbackResponse(
            feedback_id=feedback.id,
            target_id=feedback.target_id,
            release_id=feedback.release_id,
            deployment_id=feedback.deployment_id,
            experiment_id=feedback.experiment_id,
            received_at=feedback.received_at,
        )

    return router
