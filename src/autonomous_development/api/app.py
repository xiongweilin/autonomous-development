from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from autonomous_development.application.feedback import FeedbackService
from autonomous_development.ports.readiness import ReadinessProvider

from .feedback import create_feedback_router


def create_control_app(
    feedback: FeedbackService,
    readiness: ReadinessProvider,
    *,
    product_app: ASGIApp | None = None,
    product_mount_path: str = "/product",
) -> FastAPI:
    if not product_mount_path.startswith("/"):
        raise ValueError("product mount path must be absolute")
    app = FastAPI(title="autonomous-development-control-plane")
    app.include_router(create_feedback_router(feedback))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
