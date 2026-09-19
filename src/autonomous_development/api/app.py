from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from starlette.types import ASGIApp

from autonomous_development.application.feedback import FeedbackService
from autonomous_development.application.operator import OperatorService
from autonomous_development.ports.readiness import ReadinessProvider

from .feedback import create_feedback_router
from .operator import create_operator_router
from .operator_security import OperatorAuthenticator


def create_control_app(
    feedback: FeedbackService,
    readiness: ReadinessProvider,
    *,
    product_app: ASGIApp | None = None,
    product_mount_path: str = "/product",
    operator: OperatorService | None = None,
    operator_authenticator: OperatorAuthenticator | None = None,
    start_operator_workflow: Callable[[str, str], None] | None = None,
) -> FastAPI:
    if not product_mount_path.startswith("/"):
        raise ValueError("product mount path must be absolute")
    app = FastAPI(title="autonomous-development-control-plane")
    metrics_registry = CollectorRegistry()
    operator_events_pending = Gauge(
        "operator_events_pending",
        "Unacknowledged operator events in the durable outbox.",
        registry=metrics_registry,
    )
    interventions_pending = Gauge(
        "interventions_pending",
        "Open human interventions in the operator domain.",
        registry=metrics_registry,
    )
    app.include_router(create_feedback_router(feedback))
    if operator is not None:
        if operator_authenticator is None or start_operator_workflow is None:
            raise ValueError("operator service requires authentication and workflow starter")
        app.include_router(
            create_operator_router(
                operator,
                operator_authenticator,
                start_workflow=start_operator_workflow,
            )
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        if operator is not None:
            operator_events_pending.set(operator.pending_event_count())
            interventions_pending.set(operator.pending_intervention_count())
        return Response(generate_latest(metrics_registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/ready")
    def ready() -> JSONResponse:
        report = readiness.check()
        content = {
            "status": "ready" if report.ready else "not-ready",
            "checks": [
                {
                    "name": check.name,
                    "ready": check.ready,
                    "detail": check.detail,
                }
                for check in report.checks
            ],
        }
        return JSONResponse(
            status_code=200 if report.ready else 503,
            content=content,
        )

    if product_app is not None:
        app.mount(product_mount_path.rstrip("/") or "/", product_app)

    return app
